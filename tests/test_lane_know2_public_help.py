"""Lane KNOW2: the public tier's search fixed (human decision 2026-10-26, 1).

Lane KAUDIT measured the public pieces attached to a problem's help on the
Moss index (2026-10-26): of 33, one fitted, twelve were unclear and twenty
were off the point -- mostly a textbook or wiki piece that shared one
general word ("cell", "Gel", "Ab") with the problem -- and three server
faults were found:

(a) a one-letter line of a public piece ("p") was taken out of the help's
    own words wherever it stood, so the frame's "p.3" became "3" and the
    whole help was replaced by the fixed sentence (``number_not_in_sources``);
(b) the hits were ordered by grade first, so an A or B piece the index
    scored 0.0 came before a C piece it found relevant;
(c) one shared word was enough to attach a piece, and a Korean wiki
    definition was read aloud.

The person decided: a floor of relevance (the index's score and the meaning
words a piece shares) under which no public layer is attached at all; the
query made of the current step's key words and the problem's words, not the
whole sentence; the grade first only when scores are close; and, spoken,
only "공개 자료에 관련 내용이 있어요", the content shown with its source.

The Moss client is a fake and no provider is called: contract-tested.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_help_support import session_at
from tests.test_lane_know_public_knowledge import FakeClient, _doc
from voiney_lab import knowledge
from voiney_lab import public_knowledge as pk
from voiney_lab.answer_checks import problem_help_violations
from voiney_lab.external_references import PUBLIC_SPOKEN, SupplementalKnowledgeSettings, public_reference_part
from voiney_lab.knowledge import CLOSING
from voiney_lab.language import Transcription
from voiney_lab.public_knowledge import Glossary, PublicKnowledgeSettings, PublicKnowledgeSource, problem_query
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

CONTEXT = knowledge.KnowledgeContext("lab", "acc", "p1", "r1", "s1", "ko")
GLOSSARY = Glossary(terms={"아세토나이트릴": ("Acetonitrile",), "젤": ("Gel",), "세포": ("Cell",),
                           "항체": ("Antibody", "Ab")})
#: Asked at step 2 of lane HELP's wash protocol ("Wash the gel piece with 500 µL
#: wash buffer for 15 min at 37°C."): names a reagent and what went wrong.
ASKED = "wash buffer에 acetonitrile 거품이 왜 생기지?"
FOAM_TEXT = "Acetonitrile solutions foam when shaken; the bubbles settle within minutes."
SAFETY_TEXT = "Acetonitrile has a flash point of 42 °F and an exposure limit of 20 ppm TWA."


async def _loaded(docs) -> tuple[PublicKnowledgeSource, FakeClient]:
    client = FakeClient(docs)
    source = PublicKnowledgeSource(PublicKnowledgeSettings(True, project_id="p", project_key="k"), client,
                                   glossary=GLOSSARY)
    await source.load()
    return source, client


def _source(docs) -> tuple[PublicKnowledgeSource, FakeClient]:
    return asyncio.run(_loaded(docs))


class OneLetterQuotedLineTests(unittest.TestCase):
    """(a) A short quoted line is taken out only where it stands as a line or a word."""

    def test_a_one_letter_line_of_a_piece_leaves_the_frame_s_page_alone(self) -> None:
        shown = "원문 · p.3 · 7단계 · 단계\n“Buffer capacity\np\nis the amount of acid.”"
        quoted = ["Buffer capacity", "p", "is the amount of acid."]
        self.assertEqual(problem_help_violations(shown, quoted=quoted), ())

    def test_a_quoted_digit_line_is_still_taken_out(self) -> None:
        shown = "원문 · p.3 · 2단계\n“Table 1\n3\nwashes”"
        self.assertEqual(problem_help_violations(shown, quoted=["Table 1", "3", "washes"]), ())

    def test_a_number_of_the_help_s_own_words_is_still_refused(self) -> None:
        shown = "원문 · p.3 · 2단계\n“p”\n세척을 3번 더 하세요."
        self.assertIn("number_not_in_sources", problem_help_violations(shown, quoted=["p"]))


class OrderTests(unittest.TestCase):
    """(b) The index's score first; a better grade only where the scores are close."""

    def test_a_piece_the_index_scored_zero_does_not_pass_a_relevant_one_by_grade(self) -> None:
        docs = [_doc("c1", "C", source_name="Wikipedia (English)", score=0.82),
                _doc("a0", "A", score=0.0),
                _doc("b1", "B", source_name="OpenStax Chemistry", score=0.55)]
        source, _client = _source(docs)
        hits = asyncio.run(source.search("acetonitrile flash point", limit=3, context=CONTEXT))
        self.assertEqual([hit.detail("doc_id") for hit in hits], ["c1", "b1", "a0"])

    def test_a_better_grade_goes_first_when_the_scores_are_close(self) -> None:
        docs = [_doc("c1", "C", source_name="Wikipedia (English)", score=0.81), _doc("a1", "A", score=0.80)]
        source, _client = _source(docs)
        hits = asyncio.run(source.search("acetonitrile flash point", limit=2, context=CONTEXT))
        self.assertEqual([hit.detail("doc_id") for hit in hits], ["a1", "c1"])


class ProblemQueryTests(unittest.TestCase):
    """The query is the step's key words and the problem's words, not the whole sentence."""

    def test_the_query_holds_the_problem_and_the_names_not_the_sentence(self) -> None:
        query = problem_query(ASKED, "2 Wash the gel piece with 500 µL wash buffer for 15 min at 37°C.", GLOSSARY)
        assert query is not None
        self.assertEqual(query.problem, ("foam",))
        self.assertIn("acetonitrile", [name.casefold() for name in query.subjects])
        self.assertIn("foam", query.text)
        self.assertIn("acetonitrile", query.text.casefold())
        self.assertNotIn("왜", query.text)
        self.assertNotIn("생기지", query.text)
        # General words name nothing a piece could be about.
        for general in ("gel", "buffer", "wash", "piece"):
            self.assertNotIn(general, [name.casefold() for name in query.subjects])

    def test_the_step_s_names_join_the_problem_s(self) -> None:
        query = problem_query("세포가 안 떨어져", "4 Add 2 mL TrypLE Express and incubate for 5 min at 37 °C.", GLOSSARY)
        assert query is not None
        self.assertEqual(query.problem, ("detach",))
        self.assertIn("TrypLE", query.subjects)
        self.assertNotIn("Cell", query.subjects)  # "cell" is a general word

    def test_nothing_to_look_for_is_no_query(self) -> None:
        step = "2 Wash the gel piece with 500 µL wash buffer for 15 min at 37°C."
        self.assertIsNone(problem_query("왜 안 되지?", step, GLOSSARY))
        # A problem word with nothing it is about, and a thing with no problem.
        self.assertIsNone(problem_query("거품이 생겼어", "2 Wash the piece for 15 min.", GLOSSARY))
        self.assertIsNone(problem_query("acetonitrile 넣었어", step, GLOSSARY))


class RelevanceFloorTests(unittest.TestCase):
    """(c) A piece is attached only when it shares a problem word and a name, above the score floor."""

    def _hits(self, docs, words: str, step: str):
        source, client = _source(docs)
        query = problem_query(words, step, GLOSSARY)
        assert query is not None
        hits = asyncio.run(source.for_problem(query).search(words, limit=4, context=CONTEXT))
        return hits, client

    def test_one_shared_general_word_attaches_nothing(self) -> None:
        cells = _doc("ox-bio", "B", title="OpenStax Biology", text="T cells detach from the cell and the cell divides.",
                     source_name="OpenStax Biology", score=0.9)
        hits, _client = self._hits([cells], "세포가 안 떨어져", "4 Add 2 mL TrypLE Express and incubate for 5 min.")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].detail("relevant"), "")
        self.assertIsNone(public_reference_part(hits, language="ko"))

    def test_a_piece_with_the_problem_and_the_name_above_the_floor_is_attached(self) -> None:
        good = _doc("epmc-1", "A", title="Passaging organoids",
                    text="TrypLE was applied for 10 min until the cells detached from the plate.",
                    source_name="Europe PMC", score=0.7)
        low = _doc("epmc-2", "A", title="Passaging organoids",
                   text="TrypLE was applied until the cells detached.", source_name="Europe PMC", score=0.05)
        hits, client = self._hits([good, low], "세포가 안 떨어져", "4 Add 2 mL TrypLE Express and incubate for 5 min.")
        self.assertEqual([hit.detail("relevant") for hit in hits], ["yes", ""])
        self.assertEqual(hits[0].detail("problem_shares"), "detach")
        self.assertEqual(hits[0].detail("subject_shares"), "TrypLE")
        self.assertNotIn("세포가 안 떨어져", client.queries[0][1])
        part = public_reference_part(hits, language="ko")
        assert part is not None
        self.assertEqual(part.count, 1)

    def test_the_same_word_is_not_both_the_problem_and_the_name(self) -> None:
        # "trypsin" is one of the detach concept's words and the step's reagent:
        # a piece that only names trypsin says nothing about cells not detaching.
        trypsin = _doc("wiki-trypsin", "C", title="Trypsin-EDTA", text="Trypsin-EDTA is a serine protease solution.",
                       source_name="Wikipedia (English)", score=0.9)
        hits, _client = self._hits([trypsin], "세포가 안 떨어져", "4 Add 2 mL trypsin-EDTA and incubate for 5 min.")
        self.assertEqual(hits[0].detail("relevant"), "")


class _TurnHarness:
    def _session(self, public, *, supplemental: bool = False) -> ListenerSession:
        workflow = session_at("2")
        session = ListenerSession(tool_context=ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only"),
                                  curated_protocol_session=workflow)
        session.active = True
        session.active_turn_id = 2
        session.next_turn_id = 3
        session.turn_generations[2] = session.generation
        session.accept_configuration(41, "cascade", "ko", workflow.fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        session.supplemental_knowledge_settings = SupplementalKnowledgeSettings(supplemental, "offline-supplement", 2.0)
        session.public_knowledge = public
        return session

    def _turn(self, session, said: str):
        sent: list[dict] = []
        spoken: list[str] = []
        asked: list[str] = []

        class Socket:
            async def send_text(self, value):
                sent.append(json.loads(value))

            async def send_bytes(self, value):
                pass

        class Explainer:
            def __init__(self, *args):
                pass

            async def explain_possible_causes(self, problem, *, step_text, source_lines=(), public_lines=(), language):
                asked.append("causes")
                return {"status": "success", "answer": "세척 횟수가 부족했을 수 있어요.",
                        "backend": "supplemental_possible_causes"}

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        def synthesize(text, *args, **kwargs):
            spoken.append(text)
            return b"\0\0" * 400

        with patch("voiney_lab.server.transcribe", return_value=Transcription(said, "ko")), \
                patch("voiney_lab.server.synthesize", side_effect=synthesize), \
                patch("voiney_lab.server.XaiSupplementalKnowledge", Explainer), \
                patch("voiney_lab.server._role_client", return_value=SimpleNamespace()), \
                patch.dict("os.environ", {"VOINEY_LAB_SUPPLEMENTAL_PROVIDER": "xai",
                                          "VOINEY_LAB_SUPPLEMENTAL_MODEL": "offline-supplement"}), \
                patch("voiney_lab.server.asyncio.to_thread", side_effect=immediate):
            asyncio.run(run_turn(Socket(), session, b"\0\0", 2, 1))
        replies = [item for item in sent if item["type"] == "reply.complete"]
        return (replies[-1]["text"] if replies else ""), spoken, asked


class HelpTurnTests(_TurnHarness, unittest.TestCase):
    """The production boundary: one problem turn through ``run_turn``."""

    def test_a_piece_sharing_only_the_reagent_is_not_attached(self) -> None:
        source, client = _source([_doc("niosh-1", "A", title="Acetonitrile", text=SAFETY_TEXT,
                                       source_name="NIOSH Pocket Guide", score=0.8)])
        reply, spoken, _asked = self._turn(self._session(source), ASKED)
        self.assertTrue(client.queries)
        self.assertIn("원문 · ", reply)
        self.assertNotIn("공개 자료", reply)
        self.assertNotIn("공개 자료", " ".join(spoken))
        self.assertTrue(reply.endswith(CLOSING["ko"]))

    def test_an_attached_piece_is_shown_with_its_source_and_only_the_fixed_sentence_is_said(self) -> None:
        source, client = _source([_doc("pc-1", "A", title="Acetonitrile", text=FOAM_TEXT,
                                       source_name="PubChem", score=0.7)])
        session = self._session(source)
        reply, spoken, _asked = self._turn(session, ASKED)
        self.assertNotIn("왜 생기지", client.queries[0][1])
        self.assertIn("공개 자료 · 일반 참고 — PubChem", reply)
        self.assertIn(f"“{FOAM_TEXT}”", reply)
        self.assertIn("https://example.org/pc-1 · Public domain", reply)
        said = " ".join(spoken)
        self.assertIn(PUBLIC_SPOKEN["ko"], said)
        self.assertEqual(said.count("공개 자료"), 1)
        self.assertNotIn("PubChem", said)
        self.assertNotIn("foam", said)
        self.assertEqual(session.curated_protocol_session.current_index, 1)

    def test_with_the_role_on_the_piece_is_not_read_or_paraphrased(self) -> None:
        source, _client = _source([_doc("kowiki-1", "C", title="아세토나이트릴",
                                        text="아세토나이트릴 용액은 흔들면 거품이 생긴다.",
                                        source_name="위키백과 (한국어)", score=0.7, language="ko")])
        reply, spoken, asked = self._turn(self._session(source, supplemental=True), ASKED)
        self.assertIn("공개 자료 · 일반 참고 — 위키백과 (한국어)", reply)
        said = " ".join(spoken)
        self.assertIn(PUBLIC_SPOKEN["ko"], said)
        self.assertNotIn("흔들면", said)
        self.assertNotIn("위키백과", said)  # the wiki-only note is on the screen
        self.assertEqual(asked, ["causes"])
        self.assertTrue(said.endswith(CLOSING["ko"]))

    def test_a_relevant_piece_with_a_one_letter_line_keeps_the_whole_help(self) -> None:
        text = "Buffer foam\np\nAcetonitrile solutions foam when shaken."
        source, _client = _source([_doc("wiki-1", "C", title="Acetonitrile", text=text,
                                        source_name="Wikipedia (English)", score=0.7)])
        reply, _spoken, _asked = self._turn(self._session(source), ASKED)
        self.assertIn("원문 · ", reply)
        self.assertIn("공개 자료 · 일반 참고 — Wikipedia (English)", reply)


if __name__ == "__main__":
    unittest.main()
