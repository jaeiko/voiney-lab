"""Lane NET, decision 1 (2026-10-18): a lost connection is seen within three seconds.

The page sees the loss three ways: the socket closing or failing, the
browser saying it is offline, and the server's heartbeat stopping. The
server sends a heartbeat once a second while a session runs -- from its own
task, so a turn being read never holds it up -- with its clock and the
page's last "client.ping" as received; the page sends that ping once a
second. A ping is read before the control parser and changes nothing; a
malformed one is dropped, never ending the session. The server also notes
when it last heard the connection an experiment continues on (decision 5
reads it).
"""

from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness, error_message
from tests.lane_net_support import CONNECTION, run_connection_script
from tests.static_page import page_script

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "voiney_lab" / "static" / "index.html"


class _Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def text(self, kind: str, **fields) -> None:
        self.sent.append((kind, fields))


class _Session:
    active = False


class HeartbeatTaskTests(unittest.TestCase):
    """The heartbeat itself: only while a session runs, on its own sleep."""

    def run_beats(self, session, *, seconds: float) -> _Sender:
        sender, heartbeat = _Sender(), server_module._Heartbeat()

        async def run() -> None:
            task = asyncio.create_task(
                server_module._send_heartbeats(session, sender, heartbeat))
            await asyncio.sleep(seconds)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        with patch.object(server_module, "HEARTBEAT_SECONDS", 0.01):
            asyncio.run(run())
        return sender

    def test_no_heartbeat_before_a_session_runs(self) -> None:
        self.assertEqual(self.run_beats(_Session(), seconds=0.1).sent, [])

    def test_a_running_session_gets_one_beat_per_period_with_the_rule_of_the_last_minute(self) -> None:
        session = _Session()
        session.active = True
        sent = self.run_beats(session, seconds=0.1).sent
        self.assertGreaterEqual(len(sent), 3)
        kind, fields = sent[0]
        self.assertEqual(kind, "server.heartbeat")
        self.assertAlmostEqual(fields["server_time_ms"] / 1000, time.time(), delta=5)
        self.assertEqual(fields["timer_last_minute_from_seconds"], 300)
        self.assertEqual(fields["timer_last_minute_seconds"], 60)
        self.assertNotIn("ping_seq", fields)

    def test_a_replaced_asyncio_sleep_does_not_make_it_spin(self) -> None:
        async def hurried(*_args, **_kwargs):
            return None

        class Counting(_Sender):
            async def text(self, kind: str, **fields) -> None:
                await super().text(kind, **fields)
                if len(self.sent) > 50:
                    raise RuntimeError("the heartbeat spun")

        session = _Session()
        session.active = True

        async def run() -> int:
            sender, heartbeat = Counting(), server_module._Heartbeat()
            task = asyncio.create_task(
                server_module._send_heartbeats(session, sender, heartbeat))
            with patch("voiney_lab.server.asyncio.sleep", side_effect=hurried):
                await server_module._heartbeat_sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return len(sender.sent)

        with patch.object(server_module, "HEARTBEAT_SECONDS", 0.02):
            beats = asyncio.run(run())
        self.assertLessEqual(beats, 4)

    def test_a_ping_is_read_as_a_ping_and_nothing_else(self) -> None:
        read = server_module._client_ping
        self.assertEqual(
            read('{"type":"client.ping","seq":4,"sent_at_ms":1760752800000.5}'),
            {"seq": 4, "sent_at_ms": 1760752800000.5})
        for broken in (
            '{"type":"client.ping"}', '{"type":"client.ping","seq":-1,"sent_at_ms":1}',
            '{"type":"client.ping","seq":true,"sent_at_ms":1}',
            '{"type":"client.ping","seq":1,"sent_at_ms":"soon"}',
        ):
            with self.subTest(broken=broken):
                self.assertEqual(read(broken), {})
        for other in ('{"type":"session.stop"}', '{"type":"x","note":"client.ping"}', "client.ping"):
            with self.subTest(other=other):
                self.assertIsNone(read(other))


class HeartbeatSessionTests(VoiceNotesHarness, unittest.TestCase):
    """The production socket: pings go before the parser, beats come back."""

    def test_the_heartbeat_carries_the_server_clock_and_the_last_ping(self) -> None:
        seen: dict = {}

        async def scenario(socket, listener, say):
            socket.control({"type": "client.ping", "seq": 7, "sent_at_ms": 1234.5})
            socket.control({"type": "client.ping", "seq": "broken"})
            beat = await socket.wait_for(
                lambda item: item["type"] == "server.heartbeat" and item.get("ping_seq") == 7)
            seen["beat"] = beat
            seen["active"] = listener.active
            entry = server_module._HEARD_CONNECTIONS.get(listener.session_id)
            seen["bound"] = entry is not None and entry.connection_id == listener.voice_connection_id
            seen["heard"] = entry.heard_at if entry else None

        before = time.time()
        socket = self.voice(scenario, patch.object(server_module, "HEARTBEAT_SECONDS", 0.05))
        self.assertIsNone(error_message(socket))
        self.assertTrue(seen["active"], "a ping ended the session")
        beat = seen["beat"]
        self.assertEqual(beat["ping_sent_at_ms"], 1234.5)
        self.assertGreaterEqual(beat["ping_received_ms"], round(before * 1000))
        self.assertGreaterEqual(beat["server_time_ms"], beat["ping_received_ms"])
        # The experiment's connection is the one heard, and it was heard just now.
        self.assertTrue(seen["bound"])
        self.assertGreaterEqual(seen["heard"], before)


class PageServedTests(unittest.TestCase):

    def test_connection_js_is_served_after_the_page_script(self) -> None:
        from voiney_lab.server import app

        mount = next(route for route in app.routes if getattr(route, "name", None) == "static")
        path, stat_result = mount.app.lookup_path("connection.js")
        self.assertTrue(Path(path).is_file())
        self.assertIsNotNone(stat_result)
        html = INDEX.read_text(encoding="utf-8")
        self.assertLess(html.index("<script>"), html.index('<script src="/connection.js"></script>'))
        # Lane RX2 (decision 4): the page's socket code is voice-core.js, loaded just before the page's own script.
        self.assertLess(html.index('<script src="/voice-core.js"></script>'), html.index("<script>"))
        self.assertIn("voineyConnection?.watch(current)", page_script(html))
        self.assertTrue(CONNECTION.read_text(encoding="utf-8").startswith("// Lane NET"))


class PageDetectionTests(unittest.TestCase):
    """The page's half, under a fake clock."""

    def run_page(self, body: str) -> None:
        result = run_connection_script(body)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_page_pings_once_a_second_with_its_own_clock(self) -> None:
        self.run_page(r"""
const ws=await openRun();
const before=ws.texts("client.ping").length;
await advance(3000);
const pings=ws.texts("client.ping").slice(before);
assert(pings.length===3,`pings in 3 s: ${JSON.stringify(pings)}`);
assert(pings[1].seq===pings[0].seq+1&&pings[2].seq===pings[1].seq+1,"ping numbers do not count up");
assert(pings[2].sent_at_ms===Date.now(),"a ping does not carry the page's clock");
""")

    def test_a_silent_server_is_lost_within_three_seconds_and_not_before_its_time(self) -> None:
        self.run_page(r"""
const ws=await openRun();
await advance(2600);
assert(sessionActive&&socket===ws,"lost after 2.6 s of silence");
await advance(400);
assert(socket!==ws,"still on the silent socket after 3 s");
assert(ws.closedWith?.code===4000,`the silent socket was not let go: ${JSON.stringify(ws.closedWith)}`);
""")

    def test_messages_and_audio_keep_it_alive(self) -> None:
        self.run_page(r"""
const ws=await openRun();
for(let i=0;i<10;i++){await advance(1000);await ws.deliver(i%2?new ArrayBuffer(640):{type:"server.heartbeat",server_time_ms:Date.now()});}
assert(sessionActive&&socket===ws,"lost while the server kept talking");
""")

    def test_the_browser_going_offline_is_lost_at_once(self) -> None:
        self.run_page(r"""
const ws=await openRun();
fire("offline");
assert(socket!==ws&&ws.closedWith?.code===4000,"offline did not let the socket go");
""")

    def test_a_close_is_lost_at_once(self) -> None:
        self.run_page(r"""
const ws=await openRun();
ws.drop();await settle();
assert(socket!==ws||!sessionActive,"a closed socket was kept as the run's");
""")

    def test_without_a_running_session_nothing_is_counted_lost(self) -> None:
        self.run_page(r"""
const pending=init(sessionGeneration),ws=opened.at(-1);ws.open();await pending;
await advance(10000);fire("offline");
assert(socket===ws&&ws.closedWith===null,"a socket without a session was let go");
""")


if __name__ == "__main__":
    unittest.main()
