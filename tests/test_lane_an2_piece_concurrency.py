"""Lane AN2, decision 3: the pieces of one document run as many at a time as the
setting says, and fewer once the provider answers 429.

Lane AN's long documents ran eight pieces at once (``ANALYSIS_PIECE_CONCURRENCY``)
and, beside other runs, drew 23 HTTP 429. The number is now the setting
VOINEY_LAB_ANALYSIS_PIECE_CONCURRENCY (the old constant is its default), and a
429 seen while a document's pieces are being read halves the number of pieces
still to start -- for that document only, never below one. Pieces already
running are left alone. Nothing about what a piece must pass changes.

Contract-tested: fake models and fake provider clients, no request leaves this
machine.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from unittest import mock

from tests.lane_an2_support import Clock, SequencedGemini, StatusError, gemini_reply
from tests.test_lane_ld_pieces import PieceModel, _CatalogCase, page_texts
from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab import model_providers
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_PIECE_BACKOFF_WINDOW_SECONDS,
    ANALYSIS_PIECE_CONCURRENCY,
    OpenAICompatibleProtocolAnalysisModel,
    PieceConcurrencyGate,
    analysis_piece_concurrency,
)
from voiney_lab.model_providers import ProviderRetryEvent, RoleModel, chat_client

SETTING = "VOINEY_LAB_ANALYSIS_PIECE_CONCURRENCY"


def _rate_limit(role: str = "analysis") -> ProviderRetryEvent:
    return ProviderRetryEvent("google", role, "gemini-3.8-flash", "rate_limited", 429, 1, 2.0, None, True)


class TheSettingTests(unittest.TestCase):
    def test_the_old_constant_is_the_default(self) -> None:
        self.assertEqual(analysis_piece_concurrency({}), ANALYSIS_PIECE_CONCURRENCY)
        self.assertGreaterEqual(ANALYSIS_PIECE_CONCURRENCY, 1)

    def test_the_setting_names_the_number(self) -> None:
        self.assertEqual(analysis_piece_concurrency({SETTING: "3"}), 3)
        self.assertEqual(analysis_piece_concurrency({SETTING: " 1 "}), 1)

    def test_a_value_that_is_not_a_whole_number_of_one_or_more_is_the_default(self) -> None:
        for raw in ("", "many", "0", "-2", "2.5"):
            with self.subTest(raw=raw):
                self.assertEqual(analysis_piece_concurrency({SETTING: raw}), ANALYSIS_PIECE_CONCURRENCY)

    def test_a_very_large_value_is_cut_to_the_upper_bound(self) -> None:
        self.assertEqual(analysis_piece_concurrency({SETTING: "500"}), 16)

    def test_the_process_environment_is_read_when_none_is_given(self) -> None:
        with mock.patch.dict(os.environ, {SETTING: "2"}):
            self.assertEqual(analysis_piece_concurrency(), 2)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TheGateTests(unittest.TestCase):
    def test_no_more_than_the_limit_are_inside_at_once(self) -> None:
        gate = PieceConcurrencyGate(2)
        inside, peak, lock = 0, 0, threading.Lock()
        release = threading.Event()

        def work() -> None:
            nonlocal inside, peak
            with gate:
                with lock:
                    inside += 1
                    peak = max(peak, inside)
                release.wait(0.05)
                with lock:
                    inside -= 1

        threads = [threading.Thread(target=work) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(peak, 2)

    def test_a_429_halves_the_limit_down_to_one(self) -> None:
        clock = _FakeClock()
        gate = PieceConcurrencyGate(8, window_seconds=20, clock=clock)
        seen = []
        for _ in range(5):
            seen.append(gate.rate_limited())
            clock.now += 21
        self.assertEqual(seen, [4, 2, 1, None, None])
        self.assertEqual(gate.limit, 1)

    def test_a_burst_of_429_is_one_incident(self) -> None:
        clock = _FakeClock()
        gate = PieceConcurrencyGate(8, window_seconds=20, clock=clock)
        self.assertEqual(gate.rate_limited(), 4)
        clock.now += 5
        self.assertIsNone(gate.rate_limited())  # the other pieces' 429s of the same moment
        self.assertEqual(gate.limit, 4)
        clock.now += 16
        self.assertEqual(gate.rate_limited(), 2)

    def test_the_default_window_is_the_documented_one(self) -> None:
        gate = PieceConcurrencyGate(8)
        self.assertEqual(gate.window_seconds, ANALYSIS_PIECE_BACKOFF_WINDOW_SECONDS)

    def test_a_limit_below_one_is_one(self) -> None:
        self.assertEqual(PieceConcurrencyGate(0).limit, 1)

    def test_lowering_leaves_the_running_alone_and_holds_back_the_waiting(self) -> None:
        gate = PieceConcurrencyGate(3, window_seconds=0)
        entered: list[int] = []
        lock = threading.Lock()
        release = {n: threading.Event() for n in range(5)}
        peak_after = 0
        inside = 0

        def work(n: int) -> None:
            nonlocal inside, peak_after
            with gate:
                with lock:
                    inside += 1
                    entered.append(n)
                    if gate.limit < 3:
                        peak_after = max(peak_after, inside)
                release[n].wait(10)
                with lock:
                    inside -= 1

        threads = [threading.Thread(target=work, args=(n,)) for n in range(5)]
        for n in range(3):
            threads[n].start()
        for _ in range(200):
            with lock:
                if len(entered) == 3:
                    break
            threading.Event().wait(0.01)
        self.assertEqual(sorted(entered), [0, 1, 2])
        self.assertEqual(gate.rate_limited(), 1)  # 3 -> 1; all three keep running
        threads[3].start()
        threads[4].start()
        threading.Event().wait(0.1)
        with lock:
            self.assertEqual(sorted(entered), [0, 1, 2])  # the new two wait
        for n in (0, 1):
            release[n].set()
        threading.Event().wait(0.1)
        with lock:
            self.assertEqual(sorted(entered), [0, 1, 2])  # one still inside: 1 >= limit 1
        release[2].set()
        for n in (3, 4):
            release[n].set()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(entered), [0, 1, 2, 3, 4])
        self.assertEqual(peak_after, 1)  # what entered after the cut never exceeded the new limit


class _Probe(PieceModel):
    """A PieceModel that counts the calls in flight and can hold them."""

    def __init__(self, source, *, hold_all: float = 0.0, barrier: threading.Barrier | None = None,
                 on_call=None, **kwargs) -> None:
        super().__init__(source, **kwargs)
        self.in_flight = 0
        self.peak = 0
        self.entry_counts: list[tuple[int, int]] = []
        self.hold_all = hold_all
        self.barrier = barrier
        self.on_call = on_call

    def analyze(self, *, system_prompt, input_json, response_schema):
        first = json.loads(input_json)["pages"][0]["source_page_number"]
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.entry_counts.append((first, self.in_flight))
        try:
            if self.barrier is not None:
                self.barrier.wait(10)
            if self.on_call is not None:
                self.on_call(first)
            if self.hold_all:
                threading.Event().wait(self.hold_all)
            return super().analyze(
                system_prompt=system_prompt, input_json=input_json, response_schema=response_schema)
        finally:
            with self.lock:
                self.in_flight -= 1


class CatalogConcurrencyTests(_CatalogCase):
    def test_the_setting_caps_the_pieces_read_at_once(self) -> None:
        protocol_id = self.register(page_texts(61))
        model = _Probe(self.extraction, hold_all=0.05)
        with mock.patch.dict(os.environ, {SETTING: "2"}):
            self.assertIsNone(self.analyze(protocol_id, model))
        self.assertEqual(model.peak, 2)
        self.assertEqual(len(model.calls), 5)

    def test_a_setting_of_one_reads_the_pieces_one_after_the_other(self) -> None:
        protocol_id = self.register(page_texts(61))
        model = _Probe(self.extraction, hold_all=0.02)
        with mock.patch.dict(os.environ, {SETTING: "1"}):
            self.assertIsNone(self.analyze(protocol_id, model))
        self.assertEqual(model.peak, 1)

    def test_without_the_setting_the_default_number_run_together(self) -> None:
        protocol_id = self.register(page_texts(61))
        barrier = threading.Barrier(5)  # five pieces: all five must be in flight at once
        model = _Probe(self.extraction, barrier=barrier)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(SETTING, None)
            self.assertIsNone(self.analyze(protocol_id, model))
        self.assertEqual(model.peak, 5)

    def test_a_429_seen_in_a_piece_lowers_the_pieces_not_yet_started(self) -> None:
        protocol_id = self.register(page_texts(61))
        second_started = threading.Event()
        first_done = threading.Event()

        def on_call(first_page: int) -> None:
            if first_page == 1:
                second_started.wait(10)
                model_providers._notify(_rate_limit())
                first_done.set()
            elif first_page == 13:
                second_started.set()
                first_done.wait(10)

        model = _Probe(self.extraction, on_call=on_call)
        with mock.patch.dict(os.environ, {SETTING: "2"}):
            self.assertIsNone(self.analyze(protocol_id, model))
        counts = dict(model.entry_counts)
        # Pieces 3, 4 and 5 started after the 429 cut the limit from 2 to 1.
        self.assertEqual([counts[first] for first in (25, 37, 49)], [1, 1, 1])
        self.assertEqual(self.catalog.get_entry(protocol_id).step_count, 122)

    def test_the_429s_are_written_into_the_piece_events_and_the_cut_is_named(self) -> None:
        protocol_id = self.register(page_texts(61))
        second_started = threading.Event()

        def on_call(first_page: int) -> None:
            if first_page == 1:
                second_started.wait(10)
                model_providers._notify(_rate_limit())
            elif first_page == 13:
                second_started.set()

        model = _Probe(self.extraction, on_call=on_call)
        with mock.patch.dict(os.environ, {SETTING: "2"}):
            self.assertIsNone(self.analyze(protocol_id, model))
        completed = {
            event.payload["chunk_id"]: event.payload
            for event in self.events(protocol_id, "protocol_chunk_analysis_completed")
        }
        self.assertEqual(completed["piece-1"]["provider_retries"], [{
            "reason": "rate_limited", "status_code": 429, "attempt": 1,
            "wait_seconds": 2.0, "retry_after": None, "will_retry": True}])
        self.assertEqual(completed["piece-1"]["concurrency_lowered_to"], 1)
        for chunk in ("piece-2", "piece-3", "piece-4", "piece-5"):
            self.assertNotIn("provider_retries", completed[chunk])
            self.assertNotIn("concurrency_lowered_to", completed[chunk])

    def test_a_piece_that_fails_after_its_429s_still_reports_them(self) -> None:
        protocol_id = self.register(page_texts(61))
        model = _Probe(self.extraction, on_call=lambda first: (
            model_providers._notify(_rate_limit()) if first == 25 else None), broken={25: 2})
        self.assertIsNotNone(self.analyze(protocol_id, model))
        (failed,) = self.events(protocol_id, "protocol_chunk_analysis_failed")
        self.assertEqual(failed.payload["chunk_id"], "piece-3")
        self.assertEqual(len(failed.payload["provider_retries"]), 2)  # one per send of the piece


class ThroughTheRealAdapterTests(_CatalogCase):
    """A 429 from the provider, retried by the adapter, lowers the document's concurrency."""

    def test_a_429_the_adapter_retried_is_seen_by_the_catalog_and_the_document_passes(self) -> None:
        protocol_id = self.register(page_texts(61))  # five pieces
        piece_model = PieceModel(self.extraction)
        gate_cuts: list[int] = []
        real = analysis_module.PieceConcurrencyGate.rate_limited

        def counting(gate):
            cut = real(gate)
            if cut is not None:
                gate_cuts.append(cut)
            return cut

        lock = threading.Lock()
        sent = {"n": 0}

        class Provider(SequencedGemini):
            def __init__(self) -> None:
                super().__init__()
                self.sent = 0

            def _next(self, params):
                self.requests.append(params)
                with lock:
                    sent["n"] += 1
                    number = sent["n"]
                if number == 1:
                    raise StatusError(429, headers={"Retry-After": "1"})
                text = params["contents"][-1]["parts"][0]["text"]
                begin, end = analysis_module._DOCUMENT_BEGIN, analysis_module._DOCUMENT_END
                text = text.split(begin + "\n", 1)[1].rsplit("\n" + end, 1)[0]
                answer = piece_model.analyze(system_prompt="", input_json=text, response_schema={})
                return gemini_reply(answer)

        provider = Provider()
        client = chat_client(
            RoleModel("analysis", "google", "gemini-3.8-flash", "low"), asynchronous=False,
            sdk_client=provider, environment={})
        model = OpenAICompatibleProtocolAnalysisModel(
            client=client, model="gemini-3.8-flash", reasoning_effort="low")
        with Clock(), mock.patch.object(
            analysis_module.PieceConcurrencyGate, "rate_limited", counting
        ), mock.patch.dict(os.environ, {SETTING: "3"}):
            self.assertIsNone(self.analyze(protocol_id, model))
        self.assertEqual(len(provider.requests), 5 + 1)  # five pieces, one of them sent twice
        self.assertEqual(gate_cuts, [1])
        self.assertEqual(self.catalog.get_entry(protocol_id).step_count, 122)
        retried = [
            event.payload for event in self.events(protocol_id, "protocol_chunk_analysis_completed")
            if event.payload.get("provider_retries")
        ]
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["provider_retries"][0]["retry_after"], 1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
