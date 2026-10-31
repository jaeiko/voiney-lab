#!/usr/bin/env python3
"""Does the sound kept before the words change how often Chirp 3 writes a short word twice? (lane STT1; a person runs it)

The LIVE3 probe (2026-10-31, 14 diagnostic clips under 3 s) saw the
recognizer write "다음. 다음." for a sound that holds the word once: 7 of
14 as recorded, 5 of 14 with the silent lead-in cut to 300 ms. This tool
repeats that over several lead-in lengths so a change of the product's
kept prefix (``VOINEY_LAB_CASCADE_VAD_PREFIX_MS``) can be argued from
numbers -- the decision stays a person's, and only when the LIVE3
replays and the impact matrix do not worsen.

Reads the opt-in diagnostic folder (clips whose stored transcript repeats a
word, plus as many barge-in clips that do not), sends each clip once per
lead-in length to Google STT V2 ``chirp_3`` and prints counts only: how many
came back with a repeat, and how many of those the correction stage reads
once (``repeat_collapsed``). Never a transcript, a file name or a key.
Without ``--execute`` it only plans.

    python scripts/sim/lead_in_probe.py --diagnostics-dir data/runtime/stt-diagnostics
    python scripts/sim/lead_in_probe.py --diagnostics-dir data/runtime/stt-diagnostics \\
        --lead-in-ms as_recorded,0,120,300,600 --execute --max-calls 80 --max-usd 8
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import wave
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from scripts.sim.live import Budget, failure_settings

AS_RECORDED = 'as_recorded'


def repeated(text: str) -> bool:
    """A word, or a two-word phrase, written twice in a row."""

    tokens = re.findall(r'[가-힣0-9a-zA-Z]+', text or '')
    return any(tokens[i] == tokens[i + 1] for i in range(len(tokens) - 1)) or (
        len(tokens) >= 4 and any(tokens[i:i + 2] == tokens[i + 2:i + 4] for i in range(len(tokens) - 3)))


def trim_lead_in(pcm: bytes, keep_ms: int) -> bytes:
    """The clip from ``keep_ms`` before its first voiced frame (WebRTC, mode 3)."""

    from voiney_lab.audio import FRAME_BYTES
    from voiney_lab.vad import WebRtcVadClassifier
    vad = WebRtcVadClassifier(3)
    frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)]
    first = next((i for i, frame in enumerate(frames) if vad(frame)), 0)
    start = max(0, first - keep_ms // 20)
    return b''.join(frames[start:])


def select_clips(directory: Path, max_duration_ms: int) -> list[tuple[Path, bool]]:
    clips = []
    for meta_path in sorted(directory.glob('*.json')):
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or (meta.get('duration_ms') or 0) > max_duration_ms:
            continue
        wav = meta_path.with_suffix('.wav')
        if not wav.is_file():
            continue
        clips.append((wav, repeated(str(meta.get('raw_transcript') or '')), str(meta.get('audio_origin') or '')))
    with_repeat = [(wav, True) for wav, flag, _origin in clips if flag]
    plain = [(wav, False) for wav, flag, origin in clips if not flag and origin == 'barge_in'][:len(with_repeat)]
    return with_repeat + plain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--diagnostics-dir', type=Path, default=ROOT / 'data/runtime/stt-diagnostics')
    parser.add_argument('--lead-in-ms', default=f'{AS_RECORDED},0,120,300,600', help='comma-separated kept lead-in lengths; as_recorded sends the clip whole')
    parser.add_argument('--max-duration-ms', type=int, default=3000)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--max-calls', type=int, default=80)
    parser.add_argument('--max-usd', type=float, default=8.0)
    parser.add_argument('--ledger', type=Path, default=Path.home() / 'reports/sim/stt1-lead-in-probe.ledger.jsonl')
    args = parser.parse_args(argv)
    try:
        variants = args.lead_in_ms.split(',')
        if not variants or len(set(variants)) != len(variants):
            raise ValueError('lead-in lengths must be distinct')
        lengths: list[int | None] = []
        for value in variants:
            if value == AS_RECORDED:
                lengths.append(None)
            elif value.isdigit() and 0 <= int(value) <= 3000:
                lengths.append(int(value))
            else:
                raise ValueError('lead-in lengths are as_recorded or 0..3000 ms')
        if not args.diagnostics_dir.is_dir():
            raise ValueError('diagnostics folder not found')
        selected = select_clips(args.diagnostics_dir, args.max_duration_ms)
        plan = {
            'clips': len(selected), 'repeated_as_stored': sum(1 for _wav, flag in selected if flag),
            'variants': variants, 'requests_at_most': len(selected) * len(variants),
            'call_cap': args.max_calls, 'reserved_usd_cap': args.max_usd, 'external_requests_this_dry_run': 0,
        }
        print(json.dumps(plan))
        if not args.execute or not selected:
            return 0
        from voiney_lab import voice_providers as vp
        from voiney_lab.transcript_correction import REPEAT_COLLAPSED, correct_transcript
        settings = vp.SttProviderSettings.from_environment({**os.environ, vp.STT_PROVIDER_ENV: 'google_cloud_v2', vp.STT_MODEL_ENV: ''})
        counts = {variant: {'transcribed': 0, 'repeated': 0, 'collapsed_by_stage': 0, 'failures': 0} for variant in variants}
        args.ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with ExitStack() as stack:
            budget = Budget(args.max_calls, args.max_usd, .10, args.ledger)
            budget.guard(stack)
            for wav_path, _flag in selected:
                with wave.open(str(wav_path), 'rb') as handle:
                    pcm = handle.readframes(handle.getnframes())
                for variant, keep_ms in zip(variants, lengths):
                    if budget.stopped:
                        break
                    audio = pcm if keep_ms is None else trim_lead_in(pcm, keep_ms)
                    try:
                        text = vp.transcribe(audio, settings, language='ko').text
                    except Exception:  # provider failure: counted, never printed
                        counts[variant]['failures'] += 1
                        continue
                    counts[variant]['transcribed'] += 1
                    if repeated(text):
                        counts[variant]['repeated'] += 1
                        if REPEAT_COLLAPSED in correct_transcript(text).codes:
                            counts[variant]['collapsed_by_stage'] += 1
            print(json.dumps({'counts': counts, 'requests': budget.calls, 'reserved_usd': round(budget.usd, 2), 'budget_stopped': budget.stopped}))
            return 3 if budget.stopped else 0
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__, 'settings': failure_settings(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
