"""LISTEN3: short bench impacts must not reopen a finished utterance.

The regression crosses the production ``ListenerSession.accept_chunk`` and
``run_turn_safely`` boundaries.  The impact fixtures are deterministic local
PCM, not recordings and not synthetic Korean speech.
"""

from __future__ import annotations

from array import array
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import os
from types import SimpleNamespace

import pytest

from scripts.sim.corpus import Case, generate_cases, load_corpus
from scripts.sim.audio import impact_pcm
from scripts.sim.runner import run_case
from voiney_lab.audio import FRAME_BYTES, pcm_to_wav
from voiney_lab.server import ListenerSession
from voiney_lab.vad import EndpointDetector, VadConfig


SAMPLES_PER_FRAME = FRAME_BYTES // 2
SILENCE = bytes(FRAME_BYTES)


def voice_frames(count: int, *, amplitude: float = 0.18) -> list[bytes]:
    """A continuous harmonic carrier for deterministic VAD/STT fixtures."""

    frames = []
    for frame_index in range(count):
        start = frame_index * SAMPLES_PER_FRAME
        frames.append(
            array(
                "h",
                (
                    round(
                        32767
                        * amplitude
                        * (
                            math.sin(2 * math.pi * 190 * (start + sample) / 16000)
                            + 0.35
                            * math.sin(2 * math.pi * 380 * (start + sample) / 16000)
                        )
                        / 1.35
                    )
                    for sample in range(SAMPLES_PER_FRAME)
                ),
            ).tobytes()
        )
    return frames


def impact_frames(kind: str, distance_cm: int, *, count: int = 10) -> list[bytes]:
    pcm = impact_pcm(kind, distance_cm, frames=count)
    return [
        pcm[offset : offset + FRAME_BYTES]
        for offset in range(0, len(pcm), FRAME_BYTES)
    ]


def feed_until_terminal(session: ListenerSession, frames: list[bytes]):
    events = []
    for frame in frames:
        events.extend(session.accept_chunk(frame))
        if any(event.kind in {"speech.end", "speech.rejected"} for event in events):
            break
    return events


def transient_session() -> ListenerSession:
    detector = EndpointDetector(
        VadConfig(), classifier=lambda frame: any(frame), listening_onset=True,
    )
    detector.enable_transient_noise_gate(True)
    session = ListenerSession(detector)
    session.start()
    return session


@pytest.mark.parametrize("kind", ["typing", "glass", "pipette"])
@pytest.mark.parametrize("distance_cm", [30, 100])
def test_impact_after_wearer_words_does_not_reopen_endpoint(
    kind: str, distance_cm: int,
) -> None:
    session = transient_session()
    spoken = voice_frames(30)
    events = feed_until_terminal(
        session,
        spoken
        + [SILENCE] * 25
        + impact_frames(kind, distance_cm)
        + [SILENCE] * 60,
    )

    ended = [event for event in events if event.kind == "speech.end"]
    assert len(ended) == 1, events
    assert ended[0].result.total_frames == len(spoken)
    assert ended[0].result.utterance == b"".join(spoken)


@pytest.mark.parametrize("kind", ["typing", "glass", "pipette"])
@pytest.mark.parametrize("distance_cm", [30, 100])
def test_impact_only_chunk_is_dropped_before_stt(
    kind: str, distance_cm: int,
) -> None:
    protocol = load_corpus()[0]
    noise_case = next(
        case for case in generate_cases(protocol) if case.family == "noise"
    )
    pcm = impact_pcm(kind, distance_cm, frames=20)

    result = asyncio.run(
        run_case(
            protocol,
            noise_case,
            pcm=pcm,
            classifier=lambda frame: any(frame),
            transient_noise_gate=True,
        )
    )

    assert result["stt_calls"] == 0
    assert result["changed_fields"] == []
    assert result["drop_reasons"] in (["transient_noise"], ["no_vad_endpoint"])


def test_constant_level_blob_is_dropped_before_stt() -> None:
    protocol = load_corpus()[0]
    noise_case = next(
        case for case in generate_cases(protocol) if case.family == "noise"
    )
    pcm = array("h", [12000] * (20 * SAMPLES_PER_FRAME)).tobytes()

    result = asyncio.run(
        run_case(
            protocol,
            noise_case,
            pcm=pcm,
            classifier=lambda frame: any(frame),
            transient_noise_gate=True,
        )
    )

    assert result["stt_calls"] == 0
    assert result["changed_fields"] == []
    assert result["drop_reasons"] == ["transient_noise"]


@pytest.mark.parametrize(
    ("words", "family", "frames"),
    [("네", "ambiguous", 24), ("다음", "complete", 30), ("종료", "stop", 30)],
)
def test_short_real_commands_still_reach_the_server(
    words: str, family: str, frames: int,
) -> None:
    protocol = load_corpus()[0]
    if family == "stop":
        case = Case("listen3.stop", "stop", words)
    else:
        base = next(
            case
            for case in generate_cases(protocol)
            if case.family == family and (family != "complete" or case.text == "다음")
        )
        case = replace(base, text=words)

    result = asyncio.run(
        run_case(
            protocol,
            case,
            pcm=b"".join(voice_frames(frames)),
            classifier=lambda frame: any(frame),
            transient_noise_gate=True,
        )
    )

    assert result["stt_calls"] == 1
    assert result["transcript"] == words
    assert "transient_noise" not in result["drop_reasons"]


def test_impact_sim_matrix_compares_legacy_and_current_without_requests(
    tmp_path, monkeypatch,
) -> None:
    from scripts.sim import run
    from scripts.sim.compare import compare

    protocol = load_corpus()[0]
    noise_case = next(
        case for case in generate_cases(protocol) if case.family == "noise"
    )
    monkeypatch.setattr(
        run, "prepare_cases",
        lambda args: ([(protocol, noise_case)], {"name": "development"}, {}, {}),
    )
    monkeypatch.setattr(
        run, "output_dir", lambda path: path.mkdir(parents=True, exist_ok=True),
    )
    before_path = tmp_path / "legacy.json"
    after_path = tmp_path / "current.json"

    assert run.main([
        "--impact-matrix", "--impact-filter", "legacy",
        "--output", str(before_path),
    ]) == 0
    assert run.main([
        "--impact-matrix", "--impact-filter", "current",
        "--output", str(after_path),
    ]) == 0

    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    assert before["provider_requests"] == after["provider_requests"] == 0
    assert before["overall"]["cases"] == after["overall"]["cases"] == 6
    assert before["overall"]["noise_sent_to_stt"] > 0
    assert after["overall"]["noise_sent_to_stt"] == 0
    assert after["overall"]["wearer_dropped"] == 0
    assert after["overall"]["wrong_state"] == 0
    assert compare(before, after)["regressions"] == []


def test_diagnostic_replay_outputs_anonymous_aggregate_counts(
    tmp_path, monkeypatch,
) -> None:
    from scripts import hear_listening_bench as bench

    stamp = datetime(2026, 10, 29, 13, 45, tzinfo=timezone.utc).timestamp()
    for index, word_count in enumerate((1, 0)):
        stem = tmp_path / f"private-{index}"
        stem.with_suffix(".json").write_text(json.dumps({
            "word_count": word_count,
            "raw_transcript": "must not be returned",
            "session_id": "must not be returned",
        }))
        stem.with_suffix(".wav").write_bytes(
            pcm_to_wav(bytes([index + 1, 0]) * 320)
        )
        os.utime(stem.with_suffix(".json"), (stamp + index, stamp + index))

    class FakeSession:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            pass

        def accept_chunk(self, pcm: bytes):
            if not pcm or not any(pcm):
                return []
            if pcm.startswith(b"\x01\x00"):
                return [SimpleNamespace(
                    kind="speech.end",
                    result=SimpleNamespace(rejection_reason=None, total_frames=1),
                )]
            return [SimpleNamespace(
                kind="speech.rejected",
                result=SimpleNamespace(rejection_reason="transient_noise", total_frames=0),
            )]

    monkeypatch.setattr(bench, "ListenerSession", FakeSession)
    result = bench.replay_diagnostic_wavs(
        tmp_path,
        started_at=datetime.fromtimestamp(stamp, timezone.utc),
        ended_at=datetime.fromtimestamp(stamp + 2, timezone.utc),
    )

    # LIVE3 added the chain, candidate, length and tail counters; the
    # anonymous contract (counts only) is unchanged.
    assert result == {
        "mode": "diagnostic_wav_replay",
        "clips": 2,
        "word_bearing_clips": 1,
        "word_bearing_stt_bound": 1,
        "empty_transcript_clips": 1,
        "empty_transcript_stt_bound": 0,
        "rejections": {"transient_noise": 1},
        "continuation_joins": 0,
        "candidates_rejected": 0,
        "longest_utterance_frames": 1,
        "single_utterance_clips": 1,
        "word_bearing_frames_lost": 0,
        "end_delay_ms_max": 0,
    }
    assert "private" not in json.dumps(result)
    assert "must not be returned" not in json.dumps(result)
