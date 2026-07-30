import sqlite3
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.procedure_store import ProcedureStore, ProcedureTransitionError


class ProcedureStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/"runtime.sqlite"
        self.store=ProcedureStore(self.path)
    def tearDown(self): self.store.close(); self.tmp.cleanup()

    def test_session_and_events_persist_after_reopen(self):
        row=self.store.create_session("p","1")
        self.store.complete_step(row["session_id"],"s1",0,1,final=False)
        self.store.close(); self.store=ProcedureStore(self.path)
        self.assertEqual(self.store.get_session(row["session_id"])["current_step_index"],1)
        self.assertEqual(len(self.store.list_events(row["session_id"])),2)

    def test_completion_is_unique_and_failed_transition_rolls_back(self):
        row=self.store.create_session("p","1")
        self.store.complete_step(row["session_id"],"s1",0,1,final=False)
        before=self.store.list_events(row["session_id"])
        with self.assertRaises(ProcedureTransitionError):
            self.store.complete_step(row["session_id"],"s2",0,1,final=False)
        self.assertEqual(self.store.list_events(row["session_id"]),before)
        with self.assertRaises(ProcedureTransitionError):
            self.store.complete_step(row["session_id"],"s1",1,2,final=False)

    def test_foreign_keys_final_timestamp_and_append_only_events(self):
        self.assertEqual(self.store.foreign_keys_enabled(),1)
        row=self.store.create_session("p","1")
        done=self.store.complete_step(row["session_id"],"s1",0,1,final=True)
        self.assertIsNotNone(done["completed_at"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._connection.execute(
                "INSERT INTO procedure_step_events(session_id,event_type,previous_step_index,resulting_step_index,event_at) VALUES (?,?,?,?,?)",
                ("missing","started",0,0,"2026-01-01T00:00:00+00:00"))
        with self.assertRaises(sqlite3.DatabaseError):
            self.store._connection.execute("UPDATE procedure_step_events SET event_type='x'")

    def test_invalid_direct_transition_parameters_are_rejected_without_mutation(self):
        row=self.store.create_session("p","1")
        before_row=self.store.get_session(row["session_id"])
        before_events=self.store.list_events(row["session_id"])
        for step_id,expected,resulting in (("",0,1),("s",-1,0),("s",0,0),("s",0,2)):
            with self.assertRaises(ProcedureTransitionError):
                self.store.complete_step(row["session_id"],step_id,expected,resulting,final=False)
            self.assertEqual(self.store.get_session(row["session_id"]),before_row)
            self.assertEqual(self.store.list_events(row["session_id"]),before_events)

    def test_started_and_completed_events_have_partial_uniqueness(self):
        row=self.store.create_session("p","1")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connection:
                self.store._connection.execute(
                    """INSERT INTO procedure_step_events
                    (session_id,step_id,event_type,previous_step_index,resulting_step_index,event_at)
                    VALUES (?,NULL,'started',0,0,?)""",(row["session_id"],"2026-01-01"))

    def test_observation_timer_and_handoff_are_durable_append_only_events(self):
        row=self.store.create_session("workflow","2")
        session_id=row["session_id"]
        observation=self.store.record_observation(
            session_id,"observe",{"display":"red"})
        timer,idempotent=self.store.start_timer(
            session_id,"wait",10,now_epoch=1000)
        replay,replayed=self.store.start_timer(
            session_id,"wait",10,now_epoch=1005)
        handoff,blocked_replay=self.store.block_for_handoff(
            session_id,"observe","SR-20260722-A1B2C3","reported anomaly")

        self.assertFalse(idempotent)
        self.assertTrue(replayed)
        self.assertEqual(timer,replay)
        self.assertEqual(
            self.store.list_observations(session_id)[0]["value"],
            {"display":"red"})
        self.assertEqual(
            self.store.get_timer(session_id,"wait")["deadline_epoch"],1010)
        self.assertEqual(handoff["report_id"],"SR-20260722-A1B2C3")
        replay_handoff,replayed_handoff=self.store.block_for_handoff(
            session_id,"observe","SR-20260722-A1B2C3","reported anomaly")
        self.assertTrue(replayed_handoff)
        self.assertEqual(replay_handoff,handoff)
        with self.assertRaises(ProcedureTransitionError):
            self.store.complete_step(session_id,"observe",0,1,final=False)
        with self.assertRaises(ProcedureTransitionError):
            self.store.record_observation(session_id,"observe","green")
        self.store.close(); self.store=ProcedureStore(self.path)
        self.assertEqual(
            self.store.list_observations(session_id)[0]["event_id"],
            observation["event_id"])
        self.assertEqual(
            self.store.get_handoff(session_id)["report_id"],
            "SR-20260722-A1B2C3")

        for statement in (
            "UPDATE procedure_observations SET value_json='null'",
            "DELETE FROM procedure_step_timers",
            "UPDATE procedure_handoffs SET reason='changed'",
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                self.store._connection.execute(statement)
            self.store._connection.rollback()
