"""Lane KNOW2: the hand-written glossary (person's decision 2026-10-26, 2).

Lane KAUDIT found that 133 of the 151 Korean questions the public search
missed held a word the glossary did not know -- 흄후드, 시료, 튜브, 버퍼, 배지,
전기영동, 폐액, 보안경 -- because the glossary held Wikidata's labels only
(퓸후드, 겔 전기영동). ``data/public_knowledge/glossary_words.txt`` is a list a
person reads and edits: Korean forms = English names, by kind; [현상] entries
say what went wrong (a problem's query and relevance floor), [일반] entries
are general words that never count as a meaning word. The server reads it
with ``glossary.json`` when the tier loads; the pipeline puts its Korean names
beside the English titles of the pieces it indexes.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_lane_know_public_knowledge import FakeClient, _doc
from voiney_lab import knowledge
from voiney_lab.public_knowledge import (
    Glossary,
    PublicKnowledgeSettings,
    PublicKnowledgeSource,
    default_words_path,
    load_glossary,
    problem_query,
    read_glossary_words,
    start_public_knowledge,
)

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = knowledge.KnowledgeContext("lab", "acc", "p1", "r1", "s1", "ko")
SAMPLE = """# a comment
[장비]
흄후드; 흄 후드; 퓸후드 = fume hood; fume cupboard
히팅블록 = heating block; dry bath   # an inline note
[현상]
굳지 않; 안 굳 = polymerization; polymerize
[일반]
튜브 = tube
this line has no equals sign
[모름]
뭔가 = something
"""
#: Lane KAUDIT's glossary gaps (report section 3, 2026-10-26): every one must be known.
KAUDIT_GAPS = (
    "흄후드", "후드", "새시", "보호구", "보안경", "마스크", "니트릴", "귀마개", "폐액", "폐액통", "락스",
    "주사바늘", "바늘", "깨진 유리", "라벨", "해골", "생식독성", "할로겐", "수은", "아세틸렌", "레귤레이터",
    "실린더", "탱크", "멀티탭",
    "튜브", "컬럼", "히팅블록", "수조", "콜드트랩", "퍼니스", "스피드백", "코인셀", "크림퍼", "크림핑", "동결건조",
    "동결건조기", "볼텍서", "클린벤치", "회전증발기", "로타뱁", "슐렝크 라인", "안티챔버", "저울", "분석저울",
    "현미경", "대물렌즈", "소니케이터", "트랜스일루미네이터", "나노드랍", "플레이트", "노즐",
    "시료", "샘플", "버퍼", "배지", "이동상", "슬러리", "파우더", "가루", "에테르", "에티디움 브로마이드",
    "트리클로로아세트산", "과염소산", "구아니딘 티오시아네이트", "스킴밀크", "증류수", "카본 테이프", "중수소",
    "전기영동", "밴드", "배경", "발현", "인큐베이션", "워싱", "탈색", "당량점", "종말점", "메니스커스",
    "분액깔때기", "유기층", "분별증류", "극성", "승온", "오염", "곰팡이",
)


def _words_file(text: str = SAMPLE) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "glossary_words.txt"
    path.write_text(text, encoding="utf-8")
    return path


class ReadTests(unittest.TestCase):
    def test_the_list_is_read_by_kind_with_comments_and_bad_lines_left_out(self) -> None:
        words = read_glossary_words(_words_file())
        self.assertEqual(words.terms["흄후드"], ("fume hood", "fume cupboard"))
        self.assertEqual(words.terms["퓸후드"], ("fume hood", "fume cupboard"))
        self.assertEqual(words.terms["히팅블록"], ("heating block", "dry bath"))
        self.assertEqual(words.aliases["fume hood"], ("흄후드", "흄 후드", "퓸후드"))
        self.assertIn("튜브", words.general)
        self.assertIn("tube", words.general)
        (symptom,) = words.symptoms
        self.assertEqual(symptom.name, "polymerization")
        self.assertEqual(symptom.korean, ("굳지 않", "안 굳"))
        self.assertEqual(symptom.english, ("polymerization", "polymerize"))
        self.assertEqual(words.problems, (
            "line 9: no '=' (한국어 = English)", "line 10: unknown kind [모름]"))
        self.assertNotIn("뭔가", words.terms)

    def test_the_committed_list_reads_without_a_problem(self) -> None:
        words = read_glossary_words(default_words_path())
        self.assertEqual(default_words_path(), ROOT / "data" / "public_knowledge" / "glossary_words.txt")
        self.assertEqual(words.problems, ())
        self.assertGreaterEqual(len(words.terms), 250)

    def test_every_word_lane_kaudit_found_missing_is_known(self) -> None:
        glossary = load_glossary()
        missing = [word for word in KAUDIT_GAPS if not glossary.matches(word) and not glossary.symptoms_in(word)]
        self.assertEqual(missing, [])


class GlossaryTests(unittest.TestCase):
    def _glossary(self) -> Glossary:
        return Glossary(terms={"현미경": ("Optical microscope",)}).with_words(_words_file())

    def test_the_list_s_names_come_first_and_join_the_wikidata_ones(self) -> None:
        glossary = Glossary(terms={"흄후드": ("Fume hood (wiki)",)}).with_words(_words_file())
        self.assertEqual(glossary.lookup("흄후드에서"), ("fume hood", "fume cupboard", "Fume hood (wiki)"))
        self.assertEqual(glossary.matches("흄 후드 새시가 안 내려가"),
                         {"흄 후드": ("fume hood", "fume cupboard", "Fume hood (wiki)")})
        self.assertEqual(self._glossary().lookup("현미경"), ("Optical microscope",))

    def test_a_question_is_joined_by_the_list_s_english_and_its_problem_words(self) -> None:
        glossary = self._glossary()
        self.assertEqual(glossary.expand("히팅블록 온도가 안 올라가"), "히팅블록 온도가 안 올라가 heating block dry bath")
        self.assertEqual(glossary.expand("겔이 굳지 않아"), "겔이 굳지 않아 polymerization polymerize")

    def test_a_general_word_is_not_a_name_of_a_problem(self) -> None:
        glossary = self._glossary()
        query = problem_query("튜브 안 겔이 굳지 않아", "3 Add 10 µL APS and 5 µL TEMED to the tube.", glossary)
        assert query is not None
        self.assertEqual(query.problem, ("polymerization",))
        self.assertIn("APS", query.subjects)
        self.assertIn("TEMED", query.subjects)
        self.assertNotIn("튜브", query.subjects)
        self.assertNotIn("tube", [name.casefold() for name in query.subjects])
        self.assertIn("polymerization", query.text)

    def test_a_piece_about_the_listed_problem_is_attached(self) -> None:
        glossary = self._glossary()
        good = _doc("wiki-aps", "C", title="Ammonium persulfate",
                    text="Too little APS or old TEMED prevents the polymerization of acrylamide gels.",
                    source_name="Wikipedia (English)", score=0.7)
        other = _doc("wiki-tube", "C", title="Test tube", text="A tube holds the gel and APS.",
                     source_name="Wikipedia (English)", score=0.7)

        async def run():
            source = PublicKnowledgeSource(PublicKnowledgeSettings(True, project_id="p", project_key="k"),
                                           FakeClient([good, other]), glossary=glossary)
            await source.load()
            query = problem_query("겔이 굳지 않아", "3 Add 10 µL APS and 5 µL TEMED.", glossary)
            return await source.for_problem(query).search("겔이 굳지 않아", limit=2, context=CONTEXT)

        hits = asyncio.run(run())
        self.assertEqual([(hit.detail("doc_id"), hit.detail("relevant")) for hit in hits],
                         [("wiki-aps", "yes"), ("wiki-tube", "")])
        self.assertEqual(hits[0].detail("problem_shares"), "polymerization")


class LoadTests(unittest.TestCase):
    def test_the_server_reads_the_list_with_the_wikidata_glossary(self) -> None:
        settings = PublicKnowledgeSettings(enabled=True, project_id="p", project_key="k")
        source = asyncio.run(start_public_knowledge(settings, client_factory=lambda s: FakeClient()))
        assert source is not None
        wikidata = Glossary.load(ROOT / "data" / "public_knowledge" / "glossary.json")
        self.assertGreater(len(source.glossary), len(wikidata))
        self.assertEqual(source.glossary.lookup("흄후드")[0], "fume hood")
        self.assertTrue(source.glossary.symptoms)

    def test_a_glossary_given_by_path_reads_only_the_words_given(self) -> None:
        directory = Path(tempfile.mkdtemp())
        path = directory / "glossary.json"
        path.write_text(json.dumps({"terms": {"인화점": ["flash point"]}}), encoding="utf-8")
        self.assertEqual(len(load_glossary(path)), 1)
        self.assertEqual(len(load_glossary(path, _words_file())), 5)
        self.assertEqual(len(load_glossary(directory / "missing.json", directory / "missing.txt")), 0)


class PipelineTests(unittest.TestCase):
    def test_the_list_s_korean_names_go_beside_an_english_title(self) -> None:
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location("public_knowledge_pipeline",
                                                      ROOT / "scripts" / "public_knowledge_pipeline.py")
        pipeline = sys.modules.get("public_knowledge_pipeline") or importlib.util.module_from_spec(spec)
        if "public_knowledge_pipeline" not in sys.modules:
            sys.modules["public_knowledge_pipeline"] = pipeline
            spec.loader.exec_module(pipeline)
        runtime = Path(tempfile.mkdtemp())
        (runtime / "bundle").mkdir()
        (runtime / "bundle" / "glossary.json").write_text(
            json.dumps({"terms": {"퓸후드": ["Fume hood"]}, "aliases": {"Fume hood": ["퓸후드"]}}), encoding="utf-8")
        glossary = pipeline.load_bundle_glossary(runtime, _words_file())
        (document,) = pipeline.documents_for(
            [{"id": "w:1", "title": "Fume hood", "locator": "Use", "text": "Keep the sash low."}], glossary)
        self.assertTrue(document["text"].startswith("Fume hood (퓸후드 · 흄후드 · 흄 후드) · Use\n"))


if __name__ == "__main__":
    unittest.main()
