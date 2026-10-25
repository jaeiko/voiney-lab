"""Lane KNOW: the public tier in the problem help (lane HELP ③), decision 6's form.

After lane HELP merged (2026-10-25) the voice loop hands each session the
public tier the server loaded at start (``app.state.public_knowledge``), and
a problem's help shows and says what it found in decision 6's form: on the
screen "공개 자료 · 일반 참고 — <출처 이름>" with the title and place, the
piece's own words, its address and licence; said, that mark and the piece's
first sentence when it may be read (same language, short, a fact), else
where to see it; with the supplemental role on, a paraphrase checked by
``answer_checks.public_reference_violations`` in ③, or the fixed form when
it fails. Nothing moves; a public source that cannot be used is skipped.

The Moss client is a fake (``FakeClient``), the protocol is lane HELP's
fictional wash protocol and no provider is called: contract-tested.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_help_support import STEP_3, gel_wash_fixture, session_at
from tests.test_lane_know_public_knowledge import FakeClient, _doc
from tests.test_voice_pause_resume_persistence import PROTOCOL_ID, VoiceSessionHarness
from voiney_lab import knowledge
from voiney_lab import public_knowledge as pk
from voiney_lab import server as server_module
from voiney_lab.answer_checks import problem_help_violations
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.external_references import (
    PUBLIC_WIKI_ONLY,
    SupplementalKnowledgeSettings,
    public_reference_fallback,
    public_reference_part,
)
from voiney_lab.knowledge import CLOSING, NOTHING_FOUND, KnowledgeUnavailable
from voiney_lab.language import Transcription
from voiney_lab.public_knowledge import PublicKnowledgeSettings, PublicKnowledgeSource
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

CLOSING_KO = CLOSING["ko"]
NIOSH = "NIOSH Pocket Guide to Chemical Hazards"
NIOSH_TEXT = "Acetonitrile has a flash point of 42 °F and an exposure limit of 20 ppm TWA."
NIOSH_LABEL = f"공개 자료 · 일반 참고 — {NIOSH}"
NIOSH_POINTER = f"{NIOSH_LABEL}에 관련 내용이 있어요. 화면에서 확인해 주세요."


def _niosh(doc_id: str = "niosh-0017") -> pk.FoundDocument:
    return _doc(doc_id, "A", title="Acetonitrile (CAS 75-05-8)", text=NIOSH_TEXT, source_name=NIOSH)


def _wiki_ko(doc_id: str = "kowiki-1", text: str = "아세토나이트릴은 무색의 액체이다. 끓는점은 82 °C이다.") -> pk.FoundDocument:
    return _doc(doc_id, "C", title="아세토나이트릴", text=text, source_name="위키백과 (한국어)", language="ko")


async def _loaded(docs) -> tuple[PublicKnowledgeSource, FakeClient]:
    client = FakeClient(docs)
    source = PublicKnowledgeSource(PublicKnowledgeSettings(True, project_id="p", project_key="k"), client)
    await source.load()
    return source, client


def _source(docs) -> tuple[PublicKnowledgeSource, FakeClient]:
    return asyncio.run(_loaded(docs))


class _Unavailable:
    tier = "public"

    async def search(self, query, *, limit, context):
        raise KnowledgeUnavailable(pk.INDEX_SETTING, "public knowledge index is not loaded")


class _Slow:
    tier = "public"

    async def search(self, query, *, limit, context):
        await asyncio.sleep(5)
        return []


class ContractTests(unittest.TestCase):
    """Decision 7: the tier is lane HELP's contract, not a copy of it."""

    def test_the_contract_types_are_lane_help_s(self) -> None:
        self.assertIs(pk.KnowledgeHit, knowledge.KnowledgeHit)
        self.assertIs(pk.KnowledgeContext, knowledge.KnowledgeContext)
        self.assertIs(pk.KnowledgeUnavailable, knowledge.KnowledgeUnavailable)
        self.assertEqual(pk.KnowledgeHit.__module__, "voiney_lab.knowledge")

    def test_the_public_source_is_a_knowledge_source_of_the_public_tier(self) -> None:
        source, _client = _source([_niosh()])
        self.assertIsInstance(source, knowledge.KnowledgeSource)
        self.assertEqual(source.tier, "public")
        self.assertIn(source.tier, knowledge.TIERS)
        context = knowledge.KnowledgeContext("lab", "acc", "p1", "r1", "s1", "ko")
        found = asyncio.run(knowledge.search_tiers([source], "아세토니트릴", context=context))
        (hit,) = found.tier("public")
        self.assertIsInstance(hit, knowledge.KnowledgeHit)
        self.assertEqual(found.unavailable, ())


class SpokenFormTests(unittest.TestCase):
    """Decision 6: the model-free spoken form and the screen block."""

    def test_an_english_piece_in_a_korean_turn_is_pointed_to_not_read(self) -> None:
        said = public_reference_fallback(NIOSH_TEXT, source_name=NIOSH, language="ko", source_language="en")
        self.assertEqual(said, NIOSH_POINTER)

    def test_a_short_fact_in_the_turn_s_language_is_read_after_the_mark(self) -> None:
        said = public_reference_fallback("아세토나이트릴은 무색의 액체이다. 끓는점은 82 °C이다.",
                                         source_name="위키백과 (한국어)", language="ko", source_language="ko")
        self.assertEqual(said, "공개 자료 · 일반 참고 — 위키백과 (한국어): 아세토나이트릴은 무색의 액체이다.")

    def test_an_instruction_or_a_long_sentence_is_not_read(self) -> None:
        instruction = public_reference_fallback("보호 장갑을 끼세요. 환기하세요.", source_name="S",
                                                language="ko", source_language="ko")
        self.assertEqual(instruction, "공개 자료 · 일반 참고 — S에 관련 내용이 있어요. 화면에서 확인해 주세요.")
        permission = public_reference_fallback("It is safe to continue.", source_name="S", language="en",
                                               source_language="en")
        self.assertEqual(permission, "공개 자료 · 일반 참고 — S has something on it; see the screen.")
        long_fact = "아세토나이트릴은 실험실에서 고성능 액체 크로마토그래피의 이동상으로 가장 널리 쓰이는 극성 비양성자성 용매 가운데 하나로 알려져 있다."
        self.assertGreaterEqual(len(long_fact), 60)
        self.assertTrue(public_reference_fallback(long_fact, source_name="S", language="ko",
                                                  source_language="ko").endswith("화면에서 확인해 주세요."))

    def test_the_screen_block_names_the_source_place_licence_and_address(self) -> None:
        source, _client = _source([_niosh()])
        context = knowledge.KnowledgeContext("lab", "acc", "p1", "r1", "s1", "ko")
        hits = asyncio.run(source.search("아세토니트릴", limit=2, context=context))
        part = public_reference_part(hits, language="ko")
        self.assertIsNotNone(part)
        lines = part.shown.splitlines()
        self.assertEqual(lines[0], NIOSH_LABEL)
        self.assertEqual(lines[1], "Acetonitrile (CAS 75-05-8) · Safety")
        self.assertEqual(lines[2], f"“{NIOSH_TEXT}”")
        self.assertEqual(lines[3], "https://example.org/niosh-0017 · Public domain")
        self.assertEqual(part.spoken, NIOSH_POINTER)
        self.assertFalse(part.wiki_only)
        self.assertEqual(part.sources[0]["license"], "Public domain")
        self.assertEqual(part.sources[0]["grade"], "A")
        # Every line naming the source is quoted: the help's own words are only
        # its fixed sentences, which state no number and no address.
        self.assertEqual(problem_help_violations(f"{part.shown}\n{part.spoken}", quoted=part.quoted), ())

    def test_a_wiki_only_ground_says_so_and_none_found_is_none(self) -> None:
        source, _client = _source([_wiki_ko()])
        context = knowledge.KnowledgeContext("lab", "acc", "p1", "r1", "s1", "ko")
        part = public_reference_part(asyncio.run(source.search("아세토나이트릴", limit=2, context=context)),
                                     language="ko")
        self.assertTrue(part.wiki_only)
        self.assertTrue(part.shown.endswith(PUBLIC_WIKI_ONLY["ko"]))
        self.assertEqual(part.spoken, "공개 자료 · 일반 참고 — 위키백과 (한국어): 아세토나이트릴은 무색의 액체이다. "
                                      + PUBLIC_WIKI_ONLY["ko"])
        self.assertIsNone(public_reference_part((), language="ko"))


class _TurnHarness:
    """One problem turn through ``run_turn`` at step 2 of the wash protocol."""

    def _session(self, *, supplemental: bool, public) -> ListenerSession:
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        workflow = session_at("2")
        session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
        session.active = True
        session.active_turn_id = 2
        session.next_turn_id = 3
        session.turn_generations[2] = session.generation
        session.accept_configuration(41, "cascade", "ko", workflow.fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        session.supplemental_knowledge_settings = SupplementalKnowledgeSettings(
            supplemental, "offline-supplement", 2.0)
        session.public_knowledge = public
        return session

    def _turn(self, session, said: str, *, causes: str = "세척 횟수가 부족했을 수 있어요.",
              paraphrase: dict | None = None):
        sent: list[dict] = []
        spoken: list[str] = []
        paraphrased: list[dict] = []

        class Socket:
            async def send_text(self, value):
                sent.append(json.loads(value))

            async def send_bytes(self, value):
                pass

        class Explainer:
            def __init__(self, *args):
                pass

            async def explain_possible_causes(self, problem, *, step_text, source_lines=(),
                                              public_lines=(), language):
                return {"status": "success", "answer": causes, "backend": "supplemental_possible_causes"}

        async def fake_paraphrase(explainer, question, *, source_text, source_name, language):
            paraphrased.append({"question": question, "source_text": source_text,
                                "source_name": source_name, "language": language})
            return paraphrase or {"status": "response_rejected", "violations": ["answer_permits_continuing"]}

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        def synthesize(text, *args, **kwargs):
            spoken.append(text)
            return b"\0\0" * 400

        with patch("voiney_lab.server.transcribe", return_value=Transcription(said, "ko")), \
                patch("voiney_lab.server.synthesize", side_effect=synthesize), \
                patch("voiney_lab.server.XaiSupplementalKnowledge", Explainer), \
                patch("voiney_lab.server.paraphrase_public_reference", side_effect=fake_paraphrase), \
                patch("voiney_lab.server._role_client", return_value=SimpleNamespace()), \
                patch.dict("os.environ", {"VOINEY_LAB_SUPPLEMENTAL_PROVIDER": "xai",
                                          "VOINEY_LAB_SUPPLEMENTAL_MODEL": "offline-supplement"}), \
                patch("voiney_lab.server.asyncio.to_thread", side_effect=immediate):
            asyncio.run(run_turn(Socket(), session, b"\0\0", 2, 1))
        replies = [item for item in sent if item["type"] == "reply.complete"]
        return (replies[-1]["text"] if replies else ""), spoken, paraphrased, sent


class HelpTurnTests(_TurnHarness, unittest.TestCase):
    """The help of a problem asked at step 2, with the public tier connected."""

    def test_with_the_role_off_the_public_piece_is_shown_and_its_mark_said(self) -> None:
        source, client = _source([_niosh()])
        session = self._session(supplemental=False, public=source)
        reply, spoken, paraphrased, _sent = self._turn(session, "왜 안 되지?")
        self.assertTrue(client.queries, "the public tier was searched")
        self.assertIn("원문 · ", reply)  # the protocol's lines stay first
        self.assertIn(NIOSH_LABEL, reply)
        self.assertIn("Acetonitrile (CAS 75-05-8) · Safety", reply)
        self.assertIn(f"“{NIOSH_TEXT}”", reply)
        self.assertIn("https://example.org/niosh-0017 · Public domain", reply)
        self.assertLess(reply.index("원문 · "), reply.index(NIOSH_LABEL))
        self.assertTrue(reply.endswith(CLOSING_KO))
        said = " ".join(spoken)
        self.assertIn(NIOSH_POINTER, said)
        self.assertNotIn("공개 자료에도 관련 내용이 있어요", said)
        self.assertNotIn("42", said)
        self.assertTrue(said.endswith(CLOSING_KO))
        self.assertEqual(paraphrased, [])
        self.assertEqual(session.curated_protocol_session.current_index, 1)

    def test_with_the_role_on_a_checked_paraphrase_is_said_in_the_third_place(self) -> None:
        source, _client = _source([_niosh()])
        session = self._session(supplemental=True, public=source)
        reply, spoken, paraphrased, _sent = self._turn(
            session, "왜 안 되지?",
            paraphrase={"status": "success", "answer": "아세토니트릴은 인화점이 42 °F로 알려져 있어요.",
                        "label": NIOSH_LABEL})
        self.assertEqual(paraphrased[0]["source_name"], NIOSH)
        self.assertEqual(paraphrased[0]["source_text"], NIOSH_TEXT)
        self.assertIn(NIOSH_LABEL, reply)
        self.assertNotIn(NIOSH_POINTER, " ".join(spoken[:-1]))
        self.assertEqual(
            spoken[-1],
            f"{NIOSH_LABEL}: 아세토니트릴은 인화점이 42 °F로 알려져 있어요. "
            f"AI 일반 지식으로는, 세척 횟수가 부족했을 수 있어요. {CLOSING_KO}")
        self.assertEqual(session.curated_protocol_session.current_index, 1)

    def test_the_server_checks_the_paraphrase_again(self) -> None:
        # Even a sentence returned as a success is the server's to judge: one
        # that permits going on, or states a number the piece does not, is
        # replaced by the fixed form.
        for answer in ("계속해도 돼요.", "아세토니트릴은 인화점이 15 °F로 알려져 있어요."):
            with self.subTest(answer=answer):
                source, _client = _source([_niosh()])
                session = self._session(supplemental=True, public=source)
                _reply, spoken, _paraphrased, _sent = self._turn(
                    session, "왜 안 되지?", paraphrase={"status": "success", "answer": answer, "label": "x"})
                self.assertTrue(spoken[-1].startswith(NIOSH_POINTER))
                self.assertNotIn(answer, " ".join(spoken))

    def test_a_paraphrase_that_fails_a_check_leaves_the_fixed_form(self) -> None:
        source, _client = _source([_niosh()])
        session = self._session(supplemental=True, public=source)
        _reply, spoken, paraphrased, _sent = self._turn(session, "왜 안 되지?")
        self.assertEqual(len(paraphrased), 1)
        self.assertEqual(
            spoken[-1], f"{NIOSH_POINTER} AI 일반 지식으로는, 세척 횟수가 부족했을 수 있어요. {CLOSING_KO}")

    def test_only_public_material_says_the_sources_had_none_then_the_piece(self) -> None:
        source, _client = _source([_wiki_ko()])
        session = self._session(supplemental=False, public=source)
        reply, spoken, _paraphrased, _sent = self._turn(session, "침전이 생겼는데 어떡해")
        self.assertIn(NOTHING_FOUND["ko"], reply)
        self.assertLess(reply.index(NOTHING_FOUND["ko"]), reply.index("공개 자료 · 일반 참고 — 위키백과"))
        self.assertIn(PUBLIC_WIKI_ONLY["ko"], reply)
        self.assertIn(PUBLIC_WIKI_ONLY["ko"], " ".join(spoken))

    def test_a_public_source_that_cannot_be_used_is_skipped(self) -> None:
        for public in (_Unavailable(), _Slow()):
            with self.subTest(public=type(public).__name__):
                session = self._session(supplemental=False, public=public)
                with patch("voiney_lab.server.PROBLEM_HELP_SEARCH_SECONDS", 0.05):
                    reply, spoken, _paraphrased, _sent = self._turn(session, "왜 안 되지?")
                self.assertIn("원문 · ", reply)
                self.assertNotIn("공개 자료", reply)
                self.assertTrue(" ".join(spoken).endswith(CLOSING_KO))
                self.assertEqual(session.curated_protocol_session.current_index, 1)

    def test_a_spill_says_the_fixed_form_at_once_and_asks_no_model(self) -> None:
        source, _client = _source([_niosh()])
        session = self._session(supplemental=True, public=source)
        _reply, spoken, paraphrased, _sent = self._turn(session, "아세토니트릴을 흘렸어")
        self.assertEqual(paraphrased, [])
        said = " ".join(spoken)
        self.assertIn(NIOSH_POINTER, said)
        self.assertLess(said.index(NIOSH_POINTER), said.index(CLOSING_KO))
        self.assertTrue(said.endswith(CLOSING_KO))


class VoiceSocketWiringTests(VoiceSessionHarness, unittest.TestCase):
    """The production boundary: ``voice_socket`` hands the session the loaded tier."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = gel_wash_fixture(PROTOCOL_ID)

    def _run(self, public):
        spoken: list[str] = []
        held: list[ListenerSession] = []

        def synthesize(text, *args, **kwargs):
            spoken.append(text)
            return b"\0\0"

        async def scenario(socket, listener, say):
            held.append(listener)
            for turn, words in enumerate(("시작", "넘어가", "다음", "다음", "젤이 아직 파래"), 1):
                await say(turn, words)

        with patch.object(server_module.app.state, "public_knowledge", public, create=True):
            socket, _listener = self._session(
                scenario, patch("voiney_lab.server.synthesize", side_effect=synthesize))
        return socket, held[0], spoken

    def test_the_session_gets_the_tier_loaded_at_start_and_the_help_records_it(self) -> None:
        source, client = _source([_niosh()])
        socket, listener, spoken = self._run(source)
        self.assertIs(listener.public_knowledge, source)
        self.assertTrue(client.queries)
        reply = socket.reply(5)
        self.assertIn(f"“{STEP_3}”", reply)
        self.assertIn(NIOSH_LABEL, reply)
        self.assertIn("https://example.org/niosh-0017 · Public domain", reply)
        self.assertIn(NIOSH_POINTER, " ".join(spoken))
        store = ExperimentReportStore(self.report_db)
        (report,) = store.list_reports()
        (helped,) = [event for event in store.get_report(report["report_id"])["events"]
                     if event["event_type"] == "problem_help"]
        self.assertEqual(helped["payload"]["found"]["public"], 1)
        (public,) = [item for item in helped["payload"]["sources"] if item["tier"] == "public"]
        self.assertEqual((public["license"], public["grade"], public["source_name"]),
                         ("Public domain", "A", NIOSH))
        self.assertFalse(helped["payload"]["state_mutation"])

    def test_with_the_tier_off_the_session_has_none_and_the_help_is_lane_help_s(self) -> None:
        socket, listener, _spoken = self._run(None)
        self.assertIsNone(listener.public_knowledge)
        reply = socket.reply(5)
        self.assertIn(f"“{STEP_3}”", reply)
        self.assertNotIn("공개 자료", reply)


if __name__ == "__main__":
    unittest.main()
