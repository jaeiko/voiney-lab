"""Lane RX4 (2026-10-26): the new screen polished after the person's second voice test.

Decision 1 -- the pause that always goes on or ends. [다시 시작] on the new screen was refused after a
few turns: a bench resume carried session.ready's generation (lane SAFE's contract), but the server's own
generation moves on with a joined utterance (lane HEAR, ``_join_continuation``) and a barge-in (the
superseding turn), each announced to the screen by a ``speech.start`` carrying the new number, and
``workflow.resume`` compares the control's generation with the server's own (``workflow.pause`` does not).
Both screens now send the newest generation the server has shown the connection (``voice-core``'s
``turnServerGenerations``); the server is unchanged. The tests here pin the server's fence as it stands
and the old screen's button; the new screen's side is Vitest (``runModel.test.ts``, ``RunScreen.test.tsx``)
and the Playwright walk (``app-rx2-walk.ts``), whose stand-in server keeps the same fence.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness
from tests.test_lane_ia_stage3_review import run_review
from tests.test_voice_pause_resume_persistence import SOURCE_PDF, VoiceSessionHarness


class OldScreenPauseButtonTests(unittest.TestCase):
    def test_pause_and_resume_carry_the_newest_generation_the_server_showed_this_connection(self) -> None:
        result = run_review(r"""
await boot(base());
const ws=running();acceptedSessionConfiguration={...acceptedSessionConfiguration,server_generation:4};sessionGeneration=11;
// Two turns of this connection came with newer generations (a joined utterance, a barge-in); one of an earlier connection is not ours.
turnServerGenerations.set("11:3",6);turnServerGenerations.set("11:4",5);turnServerGenerations.set("10:9",9);
togglePauseSession();
const sent=JSON.parse(ws.sent.at(-1));
assert(sent.type==="workflow.pause"&&sent.configuration_id===5&&sent.generation===6,`sent: ${JSON.stringify(sent)}`);
turnServerGenerations.clear();
togglePauseSession();
assert(JSON.parse(ws.sent.at(-1)).generation===4,`without a newer turn, session.ready's goes: ${ws.sent.at(-1)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class JoinMovesTheGenerationTests(unittest.TestCase):
    """The premise of the person's decision 3 (2026-10-26), on the listener itself: a joined utterance (lane HEAR,
    ``_join_continuation``) moves the server's generation past the one session.ready sent the screen."""

    def test_a_joined_utterance_moves_the_generation_on(self) -> None:
        from tests import test_lane_hear_drops as drops
        from tests import test_lane_hear_join as hear
        from voiney_lab.server import CONTINUATION_JOIN_SECONDS, ListenerSession
        from voiney_lab.vad import EndpointDetector

        clock = drops.Clock()
        listener = ListenerSession(EndpointDetector(drops.CONFIG, classifier=drops.voiced_if_loud, listening_onset=True), clock=clock)
        listener.start()
        listener.accept_configuration(41, "cascade", "ko", "lane-rx4")
        listener.curated_protocol_session = hear._Curated()
        ready_generation = listener.generation

        def feed(pcm: bytes) -> list:
            events = []
            for at in range(0, len(pcm), hear.FRAME_BYTES):
                events.extend(listener.accept_chunk(pcm[at:at + hear.FRAME_BYTES]))
            return events

        feed(hear.run(7) + drops.SILENT * 3)
        clock.now += CONTINUATION_JOIN_SECONDS - 0.5
        kinds = [event.kind for event in feed(hear.run(11) + drops.SILENT * 3)]
        self.assertEqual(kinds[:2], ["assistant.interrupted", "speech.start"], kinds)
        self.assertGreater(listener.generation, ready_generation)


@unittest.skipUnless(SOURCE_PDF.is_file(), f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}")
class ResumeFenceTests(VoiceSessionHarness, unittest.TestCase):
    """The person's decision 3 (2026-10-26): workflow.resume is fenced as workflow.pause is -- by the accepted
    configuration only. A resume carrying session.ready's generation after the server's own moved on (a joined
    utterance, a barge-in, a reset) resumes; one from another configuration is still refused."""

    def _paused_session(self, scenario_after_pause):
        seen: dict = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            seen["ready_generation"] = listener.generation
            configuration = listener.accepted_configuration_id
            socket.control({"type": "workflow.pause", "configuration_id": configuration, "generation": listener.generation})
            await socket.wait_for(lambda item: item["type"] == "protocol.fixture.state" and item.get("action") == "pause")
            seen["paused"] = self._snapshot(listener)
            await scenario_after_pause(socket, listener, configuration, seen)

        socket, _ = self._session(scenario)
        return socket, seen

    def test_a_resume_after_a_joined_utterance_moved_the_generation_resumes(self) -> None:
        async def after_pause(socket, listener, configuration, seen):
            # What _join_continuation does to the server's generation (JoinMovesTheGenerationTests shows a real join
            # does it); the screen still holds session.ready's number.
            listener.generation += 1
            seen["moved"] = listener.generation > seen["ready_generation"]
            socket.control({"type": "workflow.resume", "configuration_id": configuration, "generation": seen["ready_generation"]})
            await socket.wait_for(
                lambda item: (item["type"] == "protocol.fixture.state" and item.get("action") == "resume")
                or item["type"] == "workflow.control.refused")
            seen["end"] = self._snapshot(listener)

        socket, seen = self._paused_session(after_pause)
        self.assertEqual(self._errors(socket), [])
        self.assertTrue(seen["moved"])
        self.assertEqual(seen["paused"]["workflow_status"], "paused")
        self.assertEqual([item for item in socket.sent if item["type"] == "workflow.control.refused"], [])
        self.assertEqual(seen["end"]["workflow_status"], "active")
        self.assertEqual(seen["end"]["durable_status"], "in_progress")

    def test_a_resume_with_no_generation_after_a_reset_resumes_and_another_configuration_is_refused(self) -> None:
        async def after_pause(socket, listener, configuration, seen):
            socket.control({"type": "session.reset"})
            await socket.wait_for(lambda item: item["type"] == "session.reset")
            seen["moved"] = listener.generation > seen["ready_generation"]
            socket.control({"type": "workflow.resume", "configuration_id": configuration + 1, "generation": listener.generation})
            await socket.wait_for(lambda item: item["type"] == "workflow.control.refused" and item.get("action") == "resume")
            seen["refused"] = self._snapshot(listener)
            # A resume that names its configuration but no generation at all is a bench resume too.
            socket.control({"type": "workflow.resume", "configuration_id": configuration})
            await socket.wait_for(lambda item: item["type"] == "protocol.fixture.state" and item.get("action") == "resume")
            seen["end"] = self._snapshot(listener)

        socket, seen = self._paused_session(after_pause)
        self.assertEqual(self._errors(socket), [])
        self.assertTrue(seen["moved"])
        refused = [item for item in socket.sent if item["type"] == "workflow.control.refused"]
        self.assertEqual([item["reason"] for item in refused], ["stale_configuration"])
        self.assertEqual(refused[0]["message_ko"], "현재 연결의 설정으로 다시 요청해 주세요.")
        self.assertEqual(seen["refused"]["workflow_status"], "paused")
        self.assertEqual(seen["end"]["workflow_status"], "active")


# --- Decision 3: the end screen's chips, kept with lane LOG's answer as tags ------------------------

# The pilot_feedback table as lane LOG first made it (no tags column): an older file is opened as it is.
OLD_FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_feedback(
 id INTEGER PRIMARY KEY,
 recorded_at TEXT NOT NULL,
 account_id TEXT NOT NULL,
 report_id TEXT NOT NULL,
 rating INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 5),
 skipped INTEGER NOT NULL CHECK(skipped IN (0,1)),
 note TEXT,
 CHECK((skipped=1 AND rating IS NULL) OR (skipped=0 AND rating IS NOT NULL))
);
"""


class FeedbackTagsTests(unittest.TestCase):
    def test_tags_are_codes_from_the_list_kept_in_its_order_never_doubled_and_never_with_a_skip(self) -> None:
        from voiney_lab.pilot_metrics import FEEDBACK_TAGS, FeedbackAnswer

        self.assertEqual(
            FeedbackAnswer.from_request({"report_id": "ER-1", "rating": 4, "tags": ["timer_easy", "heard_well"]}).tags,
            ("heard_well", "timer_easy"))
        self.assertEqual(FeedbackAnswer.from_request({"report_id": "ER-1", "rating": 4}).tags, ())
        self.assertEqual(FeedbackAnswer.from_request({"report_id": "ER-1", "skipped": True, "tags": []}).tags, ())
        for tags in (["loud"], ["heard_well", "heard_well"], "heard_well", [1], {"heard_well": True}):
            with self.assertRaises(ValueError, msg=repr(tags)):
                FeedbackAnswer.from_request({"report_id": "ER-1", "rating": 4, "tags": tags})
        with self.assertRaises(ValueError):
            FeedbackAnswer.from_request({"report_id": "ER-1", "skipped": True, "tags": ["heard_well"]})
        self.assertEqual(len(FEEDBACK_TAGS), 6)

    def test_tags_are_kept_with_the_answer_and_read_back_by_the_notes_export_also_on_an_older_file(self) -> None:
        from voiney_lab.pilot_metrics import NOTE_COLUMNS, FeedbackAnswer, PilotMetricsStore, PilotSources, note_rows

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pilot_metrics.sqlite"
            older = sqlite3.connect(path)
            older.executescript(OLD_FEEDBACK_SCHEMA)
            older.execute(
                "INSERT INTO pilot_feedback(recorded_at,account_id,report_id,rating,skipped,note) VALUES(?,?,?,?,?,?)",
                ("2026-10-20T01:00:00+00:00", "a1", "ER-0", 3, 0, None))
            older.commit()
            older.close()
            store = PilotMetricsStore(path)
            store.record_feedback(
                account_id="a1",
                answer=FeedbackAnswer.from_request({"report_id": "ER-1", "rating": 4, "note": "소리가 작았어요", "tags": ["timer_easy", "heard_well"]}))
            store.record_feedback(account_id="a1", answer=FeedbackAnswer.from_request({"report_id": "ER-2", "skipped": True}))
            store.record_feedback(account_id="a1", answer=FeedbackAnswer.from_request({"report_id": "ER-3", "rating": 5, "tags": ["figure_helped"]}))
            kept = store.feedback()
            self.assertEqual([row["report_id"] for row in kept], ["ER-0", "ER-1", "ER-2", "ER-3"])
            self.assertEqual([row["tags"] for row in kept], [None, "heard_well,timer_easy", None, "figure_helped"])
            # The notes export reads what the team reads: a memo or chips; a bare rating and a skip stay out of it.
            notes = note_rows(PilotSources(metrics_db=path, reports_db=None, workspace_db=None))
            self.assertEqual([tuple(row.keys()) for row in notes], [NOTE_COLUMNS] * 2)
            self.assertEqual([(row["report_id"], row["rating"], row["tags"], row["note"]) for row in notes],
                             [("ER-1", 4, "heard_well,timer_easy", "소리가 작았어요"), ("ER-3", 5, "figure_helped", None)])


@unittest.skipUnless(SOURCE_PDF.is_file(), f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}")
class FeedbackRouteTagsTests(VoiceNotesHarness, unittest.TestCase):
    """POST /api/pilot/feedback as the new screen sends it: the chips travel as tags and come back in the store."""

    def setUp(self) -> None:
        super().setUp()
        self.turns("프로토콜 시작해줘", "1단계 완료했어")
        self.report_id = self.report()["report_id"]

    def _post(self, body: dict):
        with patch.dict("os.environ", self.environment, clear=False):
            return TestClient(server_module.app).post("/api/pilot/feedback", json=body)

    def test_the_chips_are_kept_with_the_rating_and_a_wrong_chip_is_refused(self) -> None:
        import voiney_lab.pilot_metrics as pilot

        body = {"report_id": self.report_id, "rating": 4, "skipped": False, "note": "소리가 조금 작았어요", "tags": ["heard_well", "timer_easy"]}
        response = self._post(body)
        self.assertEqual(response.status_code, 201, response.text)
        (kept,) = pilot.PilotMetricsStore(self.workspace_dir / pilot.PILOT_METRICS_FILENAME).feedback()
        self.assertEqual((kept["rating"], kept["tags"], kept["note"]), (4, "heard_well,timer_easy", "소리가 조금 작았어요"))
        self.assertEqual(self._post({**body, "tags": ["loud"]}).status_code, 400)
        self.assertEqual(self._post({"report_id": self.report_id, "rating": None, "skipped": True, "note": None, "tags": []}).status_code, 201)


if __name__ == "__main__":
    unittest.main()
