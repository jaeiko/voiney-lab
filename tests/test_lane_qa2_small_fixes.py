"""Korean question particles and filler transport shutdown."""
import asyncio
import unittest

import pytest
from starlette.websockets import WebSocketDisconnect

from tests import test_cascade_filler as filler_tests
from tests.test_lane_on_start_check import _items
from voiney_lab.start_check import build_questions


@pytest.mark.parametrize("text,subject", [("Wait for 10 min.", "시간은"), ("Heat to 37 C.", "온도는"),
                                         ("Spin at 500 x g.", "속도는"), ("Add 2 mL.", "양은")])
def test_question_particles_match_the_source_value_kind(text, subject):
    question = build_questions(_items(text), {}, seed="particle")[0]["question_ko"]
    assert subject in question
    assert "은(는)" not in question


class FillerDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_is_a_failed_cue_with_no_unretrieved_task_exception(self):
        helper = filler_tests.CascadeFillerTests()
        filler, gate, _, _, _, _, _ = helper.make_filler()

        async def disconnected(*args, **kwargs):
            raise WebSocketDisconnect(1001)

        filler._send_event = disconnected
        filler.start()
        await asyncio.sleep(0)
        self.assertTrue(filler._task.done())
        self.assertIsNone(filler._task.exception())
        self.assertEqual(filler.snapshot.outcome, "failed")
        await filler.cancel()
