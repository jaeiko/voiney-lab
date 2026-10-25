"""Lane KAUDIT: the public knowledge coverage audit and its table (2026-10-25).

``scripts/public_knowledge_audit.py`` measures how far the public bundle
covers the wet lab -- it collects nothing, uploads nothing and calls no
model. These tests run it on a tiny bundle written here, with the offline
FTS5 client or a fake Moss client, and one problem's help through
``server.run_turn`` on lane HELP's fictional wash protocol with a fake public
source: contract-tested. The coverage table
``data/fixtures/evaluation/public_knowledge_coverage.json`` is checked for
the brief's cells and the shape of its questions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.lane_help_support import gel_wash_fixture
from tests.test_lane_know_public_knowledge import FakeClient
from voiney_lab import server as server_module
from voiney_lab.public_knowledge import (
    DEFAULT_ALPHA,
    DEFAULT_INDEX_NAME,
    FoundDocument,
    Glossary,
    PublicKnowledgeSettings,
    PublicKnowledgeSource,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "public_knowledge_audit.py"
COVERAGE = ROOT / "data" / "fixtures" / "evaluation" / "public_knowledge_coverage.json"
SOURCES = ROOT / "data" / "public_knowledge" / "sources.toml"


def _load_script():
    spec = importlib.util.spec_from_file_location("public_knowledge_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["public_knowledge_audit"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


audit = _load_script()

#: The brief's table (10/25): every cell, by area.
BRIEF_CELLS = {
    "safety": [
        "safety.ghs_sds", "safety.ppe", "safety.fume_hood", "safety.hazard_classes", "safety.peroxide_formers",
        "safety.cryogenic", "safety.compressed_gas", "safety.chemical_waste", "safety.spill_exposure",
        "safety.biosafety_levels", "safety.biowaste_autoclave", "safety.sharps", "safety.uv_laser",
        "safety.electrical", "safety.lithium_battery", "safety.nanomaterials",
    ],
    "equipment": [
        "equipment.pipette", "equipment.centrifuge", "equipment.vortex", "equipment.heating_block",
        "equipment.water_bath", "equipment.co2_incubator", "equipment.biosafety_cabinet", "equipment.autoclave",
        "equipment.pcr_machine", "equipment.electrophoresis", "equipment.western_transfer",
        "equipment.spectrophotometer", "equipment.plate_reader", "equipment.hplc", "equipment.gc",
        "equipment.lc_ms", "equipment.rotary_evaporator", "equipment.schlenk_glovebox", "equipment.vacuum_pump",
        "equipment.balance", "equipment.ph_meter", "equipment.sonicator", "equipment.microscope",
        "equipment.flow_cytometer", "equipment.speedvac", "equipment.lyophilizer", "equipment.coin_cell_crimper",
        "equipment.potentiostat", "equipment.furnace",
    ],
    "bio_technique": [
        "bio.cell_culture", "bio.transfection", "bio.pcr_qpcr", "bio.gel_electrophoresis", "bio.western_blot",
        "bio.elisa", "bio.protein_purification", "bio.protein_assay", "bio.dna_extraction", "bio.cloning",
        "bio.in_gel_digestion", "bio.immunostaining",
    ],
    "chem_technique": [
        "chem.synthesis_basics", "chem.titration", "chem.recrystallization", "chem.extraction",
        "chem.distillation", "chem.chromatography", "chem.solvent_drying", "chem.nmr_sample",
        "chem.inert_atmosphere",
    ],
    "materials": [
        "materials.electrode_slurry", "materials.coin_cell_assembly", "materials.electrolyte_handling",
        "materials.glovebox_work", "materials.xrd", "materials.sem_prep", "materials.calcination",
    ],
    "reagent": [
        "reagent.solvents", "reagent.acids_bases", "reagent.oxidizers", "reagent.biochem_toxic", "reagent.stains",
        "reagent.battery_materials", "reagent.culture_reagents",
    ],
    "troubleshooting": [
        "trouble.gel_not_polymerizing", "trouble.smeared_bands", "trouble.cell_contamination",
        "trouble.no_pcr_product", "trouble.precipitate", "trouble.foaming", "trouble.high_background",
        "trouble.low_yield",
    ],
}
SPEC_KEYS = {"source_id", "title_contains", "locator_contains", "text_contains"}


def _record(record_id: str, source_id: str, grade: str, title: str, text: str, *, language: str = "en",
            locator: str = "Section", domain: str = "chemistry") -> dict:
    return {
        "id": record_id, "tier": "public", "title": title, "text": text, "locator": locator,
        "url": f"https://example.org/{record_id}", "license": "CC BY 4.0", "language": language,
        "domain": domain, "source_id": source_id, "source_name": source_id, "grade": grade,
        "version": "v1", "fetched_at": "2026-10-25",
    }


CENTRIFUGE = _record("wiki:centrifuge", "wikipedia-en", "C", "Centrifuge",
                     "Before a run the centrifuge rotor must be balanced: tubes of equal mass go opposite each other.")
FUME_HOOD = _record("niosh:hood", "niosh-2007-107", "A", "Fume hoods",
                    "Keep the fume hood sash low; the face velocity of a fume hood should be checked every year.")
BSC = _record("bmbl:bsc", "cdc-bmbl6", "A", "Biological safety cabinets",
              "Class II biological safety cabinets protect the worker, the product and the environment.")
OXYGEN = _record("ostax:oxygen", "openstax-chemistry-1e", "B", "OpenStax Chemistry (1st edition, 2015)",
                 "Oxygen is the most abundant element in the earth's crust and reacts with most metals. "
                 "Chapter 18 | Representative Metals, Metalloids, and Nonmetals 1009 "
                 "This OpenStax book is available for free at http://cnx.org/content/col11760/1.9",
                 locator="p. 1019 · 1/2")
TABLE = _record("ostax:table", "openstax-chemistry-1e", "B", "OpenStax Chemistry (1st edition, 2015)",
                "Zn(s) 0 0 41.6 Zn(g) 130.73 95.14 160.98 Zn2+(aq) –153.9 –147.1 –112.1 W(s) 0 0 32.6 "
                "W(g) 849.4 807.1 174.0 WO3(s) –842.9 –764.0 75.9 Table G1 Appendix G 1243",
                locator="p. 1253")
REFERENCES = _record("bmbl:refs", "cdc-bmbl6", "A", "Biosafety in Microbiological and Biomedical Laboratories",
                     "1. Pike RM. Laboratory-associated infections. Annu Rev Microbiol. 1979;33:41–66. "
                     "2. Sewell DL et al. Clin Microbiol Rev. 1995;8:389–405. 3. Harding AL et al. (2006) "
                     "J. Biosafety Vol. 4 pp. 12-20. doi:10.1000/x 4. Byers KB et al. (2016) doi:10.1000/y")
KOREAN_MIXED = _record("kowiki:mixed", "wikipedia-ko", "C", "단추형 전지",
                       "coin cell button cell CR2032 LR44 SR626SW alkaline lithium silver oxide 전지",
                       language="ko", domain="materials")
KOREAN = _record("kowiki:hood", "wikipedia-ko", "C", "흄 후드",
                 "흄후드는 실험실에서 유해한 증기와 가스를 빨아들여 건물 밖으로 내보내는 국소 배기 장치이다. "
                 "작업할 때는 새시를 낮게 두고, 후드 안쪽 깊숙이 시약을 놓는다.",
                 language="ko", domain="safety")
SHORT = _record("wiki:short", "wikipedia-en", "C", "Chronic toxicity", "These include:")
DUPLICATE = dict(CENTRIFUGE, id="wiki:centrifuge-again", locator="Copy")
RECORDS = [CENTRIFUGE, FUME_HOOD, BSC, OXYGEN, TABLE, REFERENCES, KOREAN_MIXED, KOREAN, SHORT, DUPLICATE]
GLOSSARY = {"terms": {"원심분리기": ["Centrifuge"], "흄후드": ["Fume hood"], "생물안전작업대": ["Biosafety cabinet"]},
            "aliases": {}}


def _write_bundle(directory: Path, records=RECORDS) -> Path:
    bundle = directory / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "chunks.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                                         encoding="utf-8")
    (bundle / "glossary.json").write_text(json.dumps(GLOSSARY, ensure_ascii=False), encoding="utf-8")
    (bundle / "manifest.json").write_text(json.dumps({"generated_at": "2026-10-25T00:00:00Z",
                                                      "totals": {"chunks": len(records)}}), encoding="utf-8")
    glossary = directory / "glossary.json"
    glossary.write_text(json.dumps(GLOSSARY, ensure_ascii=False), encoding="utf-8")
    return glossary


def _table(cells: list[dict]) -> dict:
    return {"schema_version": 1, "areas": [{"id": "equipment", "name": "장비"}, {"id": "safety", "name": "안전"}],
            "cells": cells}


def _cell(cell_id: str, area: str, name: str, terms: list[str], questions: list[dict]) -> dict:
    return {"id": cell_id, "area": area, "name": name, "terms": terms,
            "importance": {"safety": 2, "use": 3}, "questions": questions}


TINY_TABLE = _table([
    _cell("equipment.centrifuge", "equipment", "원심분리기", ["centrifuge", "rotor", "원심분리기"], [
        {"id": "c.1", "text": "원심분리기 rotor balanced 어떻게", "language": "ko",
         "gold": [{"source_id": "wikipedia-en", "title_contains": "Centrifuge", "text_contains": "balanced"}]},
        {"id": "c.2", "text": "centrifuge rotor balance", "language": "en",
         "gold": [{"source_id": "*", "text_contains": "rotor must be balanced"}]},
    ]),
    _cell("safety.fume_hood", "safety", "흄후드", ["fume hood", "sash", "흄후드"], [
        {"id": "h.1", "text": "fume hood sash height", "language": "en",
         "gold": [{"source_id": "niosh-2007-107", "text_contains": "sash low"}]},
        {"id": "h.2", "text": "흄후드 점검 주기 알려줘", "language": "ko",
         "gold": [], "no_answer": "점검 주기 조각 없음"},
        {"id": "h.3", "text": "부엌 환풍기 청소", "language": "ko", "gold": [], "no_answer": "없음"},
    ]),
    _cell("equipment.cryostat", "equipment", "냉동 절편기", ["cryostat", "냉동 절편기"], [
        {"id": "y.1", "text": "냉동 절편기 칼날 각도", "language": "ko", "gold": [], "no_answer": "조각 없음"},
        {"id": "y.2", "text": "cryostat blade angle", "language": "en", "gold": [], "no_answer": "조각 없음"},
    ]),
    _cell("safety.bsc", "safety", "생물안전작업대", ["biological safety cabinet", "생물안전작업대"], [
        {"id": "b.1", "text": "zzz qqq", "language": "en",
         "gold": [{"source_id": "cdc-bmbl6", "text_contains": "protect the worker"}]},
    ]),
])


def _run(coroutine):
    return asyncio.run(coroutine)


class CoverageTableTests(unittest.TestCase):
    """The table a person reads and the script measures."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(COVERAGE.read_text(encoding="utf-8"))
        cls.cells = cls.payload["cells"]

    def test_every_cell_of_the_brief_is_there_once(self) -> None:
        by_area: dict[str, list[str]] = {}
        for cell in self.cells:
            by_area.setdefault(cell["area"], []).append(cell["id"])
        self.assertEqual({area: sorted(ids) for area, ids in by_area.items()},
                         {area: sorted(ids) for area, ids in BRIEF_CELLS.items()})
        self.assertEqual(len({cell["id"] for cell in self.cells}), len(self.cells))
        self.assertEqual({area["id"] for area in self.payload["areas"]}, set(BRIEF_CELLS))

    def test_three_to_five_questions_mostly_korean_some_without_a_question_mark(self) -> None:
        english = total = 0
        for cell in self.cells:
            questions = cell["questions"]
            with self.subTest(cell=cell["id"]):
                self.assertTrue(3 <= len(questions) <= 5)
                korean = [q for q in questions if q["language"] == "ko"]
                self.assertGreaterEqual(len(korean), 2)
                self.assertTrue(any("?" not in q["text"] for q in korean))
                self.assertTrue({q["language"] for q in questions} <= {"ko", "en"})
            english += sum(1 for q in questions if q["language"] == "en")
            total += len(questions)
        self.assertTrue(0.12 <= english / total <= 0.4, english / total)

    def test_question_ids_are_unique_and_carry_their_cell(self) -> None:
        ids = [q["id"] for cell in self.cells for q in cell["questions"]]
        self.assertEqual(len(ids), len(set(ids)))
        for cell in self.cells:
            for question in cell["questions"]:
                self.assertTrue(question["id"].startswith(f"{cell['id']}."), question["id"])

    def test_gold_names_a_listed_source_and_a_phrase_or_says_why_there_is_none(self) -> None:
        source_ids = {source["id"] for source in tomllib.loads(SOURCES.read_text(encoding="utf-8"))["source"]}
        for cell in self.cells:
            for question in cell["questions"]:
                with self.subTest(question=question["id"]):
                    if question["gold"]:
                        self.assertNotIn("no_answer", question)
                    else:
                        self.assertTrue(question.get("no_answer", "").strip())
                    for spec in question["gold"]:
                        self.assertTrue(set(spec) <= SPEC_KEYS, spec)
                        self.assertIn(spec.get("source_id"), source_ids | {"*"})
                        self.assertTrue(any(spec.get(key) for key in SPEC_KEYS - {"source_id"}), spec)

    def test_cells_carry_terms_importance_and_a_note(self) -> None:
        for cell in self.cells:
            with self.subTest(cell=cell["id"]):
                self.assertGreaterEqual(len(cell["terms"]), 4)
                self.assertIn(cell["importance"]["safety"], (1, 2, 3))
                self.assertIn(cell["importance"]["use"], (1, 2, 3))
                self.assertTrue(cell["coverage_note"].strip())

    def test_reagent_questions_say_whether_the_reagent_was_in_pubchem_s_list(self) -> None:
        marks = [q["in_pubchem_list"] for cell in self.cells if cell["area"] == "reagent" for q in cell["questions"]]
        self.assertTrue(all(isinstance(mark, bool) for mark in marks))
        self.assertTrue(any(marks) and not all(marks))

    def test_the_script_reads_the_table(self) -> None:
        table = audit.load_coverage(COVERAGE)
        self.assertEqual(len(table.cells), sum(len(ids) for ids in BRIEF_CELLS.values()))
        self.assertEqual(set(table.areas), set(BRIEF_CELLS))


class TinyBundleCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.glossary_path = _write_bundle(self.root)
        self.bundle = audit.Bundle.read(self.root)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _fts5(self):
        return _run(audit.open_source("fts5", self.bundle, glossary_path=self.glossary_path))


class InventoryTests(TinyBundleCase):
    def test_counts_by_source_grade_language_and_kind(self) -> None:
        result = audit.inventory(self.bundle, audit.pipeline.load_sources(SOURCES), sample_size=10, seed=1)
        self.assertEqual(result["totals"]["pieces"], len(RECORDS))
        self.assertEqual(result["totals"]["by_grade"], {"C": 5, "A": 3, "B": 2})
        self.assertEqual(result["totals"]["by_language"], {"en": 8, "ko": 2})
        self.assertEqual(result["sources"]["cdc-bmbl6"]["kind"], "guideline")
        self.assertEqual(result["sources"]["openstax-chemistry-1e"]["kind"], "textbook")
        self.assertEqual(result["sources"]["wikipedia-en"]["kind"], "encyclopedia article")
        self.assertEqual(result["sources"]["wikipedia-en"]["documents"], 2)  # the copy keeps its address

    def test_broken_pieces_and_duplicates_are_flagged(self) -> None:
        result = audit.inventory(self.bundle, audit.pipeline.load_sources(SOURCES), sample_size=10, seed=1)
        flags = {row["id"]: set(row["flags"]) for row in result["sample"]["rows"]}
        self.assertIn("table", flags["ostax:table"])
        self.assertIn("references", flags["bmbl:refs"])
        self.assertIn("mixed_language", flags["kowiki:mixed"])
        self.assertIn("short", flags["wiki:short"])
        self.assertEqual(flags["kowiki:hood"], set())
        self.assertEqual(flags["niosh:hood"], set())
        self.assertIn("near_duplicate", flags["wiki:centrifuge"])
        self.assertEqual(result["duplicates"]["exact_groups"], 1)
        self.assertEqual(result["sample"]["broken"], 4)

    def test_textbook_pieces_are_counted_by_chapter_from_the_page_footer(self) -> None:
        books = audit.textbook_chapters(RECORDS)
        book = books["openstax-chemistry-1e"]
        self.assertEqual(book["pieces"], 2)
        self.assertEqual(book["pieces_with_page_footer"], 1)
        self.assertEqual(book["chapters"], {"18 Representative Metals, Metalloids, and Nonmetals": 2})


class MapTests(TinyBundleCase):
    def setUp(self) -> None:
        super().setUp()
        self.coverage = self.root / "coverage.json"
        self.coverage.write_text(json.dumps(TINY_TABLE, ensure_ascii=False), encoding="utf-8")
        self.table = audit.load_coverage(self.coverage)

    def _map(self) -> dict:
        opened = self._fts5()
        return _run(audit.coverage_map(self.bundle, self.table, opened.source))

    def test_each_cell_is_marked_by_the_rule(self) -> None:
        cells = {cell["id"]: cell for cell in self._map()["cells"]}
        self.assertEqual(cells["equipment.centrifuge"]["verdict"], "✓")
        self.assertEqual(cells["safety.fume_hood"]["verdict"], "△")  # one of three found
        self.assertEqual(cells["equipment.cryostat"]["verdict"], "✗")  # nothing in the bundle
        self.assertEqual(cells["safety.bsc"]["verdict"], "△")  # material there, not found
        self.assertEqual((cells["safety.bsc"]["answerable"], cells["safety.bsc"]["found"]), (1, 0))

    def test_related_pieces_by_grade_and_the_wiki_only_mark(self) -> None:
        cells = {cell["id"]: cell for cell in self._map()["cells"]}
        self.assertEqual(cells["equipment.centrifuge"]["related_by_grade"], {"A": 0, "B": 0, "C": 2})
        self.assertTrue(cells["equipment.centrifuge"]["related_wiki_only"])
        self.assertTrue(cells["equipment.centrifuge"]["gold_wiki_only"])
        self.assertEqual(cells["safety.fume_hood"]["related_by_grade"], {"A": 1, "B": 0, "C": 1})
        self.assertFalse(cells["safety.fume_hood"]["related_wiki_only"])
        self.assertEqual(cells["equipment.cryostat"]["related"], 0)

    def test_korean_questions_are_scored_apart_and_unknown_words_listed(self) -> None:
        result = self._map()
        cells = {cell["id"]: cell for cell in result["cells"]}
        self.assertEqual((cells["equipment.centrifuge"]["korean_found"],
                          cells["equipment.centrifuge"]["korean_questions"]), (1, 1))
        self.assertEqual(cells["safety.fume_hood"]["korean_recall_at_5"], 0.0)
        self.assertEqual(result["totals"]["korean_questions"], 4)
        gaps = result["glossary_gaps"]["in_missed_questions"]
        self.assertIn("냉동", gaps)
        self.assertIn("환풍기", gaps)
        self.assertNotIn("흄후드", gaps)  # the glossary knows it
        self.assertNotIn("알려줘", gaps)  # a function word

    def test_a_gold_spec_matching_nothing_is_reported(self) -> None:
        broken = json.loads(json.dumps(TINY_TABLE))
        broken["cells"][0]["questions"][0]["gold"] = [{"source_id": "pubchem", "text_contains": "nowhere"}]
        self.coverage.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        result = _run(audit.coverage_map(self.bundle, audit.load_coverage(self.coverage), self._fts5().source))
        self.assertEqual(result["unresolved_gold"], ["c.1"])

    def test_the_summary_has_one_line_per_area_with_its_marks(self) -> None:
        summary = audit.summary_markdown(self._map())
        self.assertIn("| 장비 | 1 | 0 | 1 |", summary)
        self.assertIn("| 안전 | 0 | 2 | 0 |", summary)
        self.assertIn("✓원심분리기", summary)


class GlossaryGapTests(unittest.TestCase):
    def test_particles_come_off_and_known_or_function_words_are_left_out(self) -> None:
        glossary = Glossary(terms={"원심분리기": ("Centrifuge",)})
        self.assertEqual(audit.glossary_gaps(glossary, "원심분리기를 돌리는데 로터가 흔들려 왜 그래?"), ["로터"])
        self.assertEqual(audit.glossary_gaps(glossary, "튜브 뚜껑 보관할 때"), ["튜브", "뚜껑"])
        self.assertEqual(audit.glossary_gaps(glossary, "원심분리기 어떻게 해"), [])


class BackendTests(TinyBundleCase):
    def test_the_offline_source_is_the_server_s_source_with_grade_order(self) -> None:
        opened = self._fts5()
        self.assertIsInstance(opened.source, PublicKnowledgeSource)
        self.assertEqual(opened.source.settings.alpha, DEFAULT_ALPHA)
        hits = _run(audit.search(opened.source, "흄후드 sash"))
        grades = [hit.detail("grade") for hit in hits]
        self.assertEqual(grades, sorted(grades))
        self.assertEqual(hits[0].detail("doc_id"), "niosh:hood")
        self.assertEqual(opened.moss_loads, 0)

    def test_moss_is_loaded_once_through_the_server_s_start_with_only_its_two_settings_read(self) -> None:
        env_file = self.root / "keys.env"
        env_file.write_text("MOSS_PROJECT_ID=project-1\nMOSS_PROJECT_KEY=key-1\nOTHER_PROVIDER_KEY=secret\n",
                            encoding="utf-8")
        built: list[PublicKnowledgeSettings] = []
        doc = FoundDocument(id="niosh:hood", text="Fume hood · Section\nKeep the sash low.", score=0.9,
                            metadata={"title": "Fume hood", "grade": "A", "source_id": "niosh-2007-107"})
        client = FakeClient([doc])

        def factory(settings: PublicKnowledgeSettings) -> FakeClient:
            built.append(settings)
            return client

        with patch.dict(os.environ, {}, clear=False):
            for name in ("MOSS_PROJECT_ID", "MOSS_PROJECT_KEY", "VOINEY_LAB_PUBLIC_KNOWLEDGE_INDEX"):
                os.environ.pop(name, None)
            opened = _run(audit.open_source("moss", self.bundle, env_file=str(env_file), client_factory=factory,
                                            glossary_path=self.glossary_path))
            self.assertNotIn("OTHER_PROVIDER_KEY", os.environ)
            self.assertNotIn("MOSS_PROJECT_KEY", os.environ)
        self.assertEqual(opened.moss_loads, 1)
        self.assertEqual(client.loaded, [DEFAULT_INDEX_NAME])
        self.assertEqual((built[0].project_id, built[0].project_key), ("project-1", "key-1"))
        self.assertNotIn("key-1", repr(built[0]))
        hits = _run(audit.search(opened.source, "흄후드"))
        self.assertEqual(client.queries[0][3], DEFAULT_ALPHA)
        self.assertIn("Fume hood", client.queries[0][1])  # the glossary's English name joined the query
        self.assertEqual(hits[0].detail("shares"), "Fume hood")

    def test_the_shell_s_credentials_come_before_the_file_s(self) -> None:
        env_file = self.root / "keys.env"
        env_file.write_text("MOSS_PROJECT_ID=from-file\nMOSS_PROJECT_KEY=key-file\n", encoding="utf-8")
        with patch.dict(os.environ, {"MOSS_PROJECT_ID": "from-shell"}, clear=False):
            os.environ.pop("MOSS_PROJECT_KEY", None)
            values = audit._moss_credentials(str(env_file))
        self.assertEqual(values, {"MOSS_PROJECT_ID": "from-shell", "MOSS_PROJECT_KEY": "key-file"})


class HelpTurnTests(unittest.TestCase):
    """One problem's help through ``server.run_turn`` with a fake public source (lane HELP's wash protocol)."""

    ASKED = "wash buffer에 acetonitrile 거품이 왜 생기지?"

    def _source(self, docs) -> PublicKnowledgeSource:
        source = PublicKnowledgeSource(PublicKnowledgeSettings(True, project_id="p", project_key="k"),
                                       FakeClient(docs), glossary=Glossary(terms={"아세토나이트릴": ("Acetonitrile",)}))
        _run(source.load())
        return source

    def _case(self, utterance: str) -> dict:
        return {"id": "T1", "protocol": "fictional", "step": "2", "utterance": utterance, "expect_help": True,
                "expect_record": False, "set": "test"}

    def test_the_attached_public_piece_and_the_spoken_form_are_recorded(self) -> None:
        doc = FoundDocument(
            id="niosh-0017", text="Acetonitrile · Safety\nAcetonitrile has a flash point of 42 °F.", score=0.9,
            metadata={"title": "Acetonitrile", "locator": "Safety", "url": "https://example.org/niosh-0017",
                      "license": "Public domain", "language": "en", "source_id": "niosh-npg",
                      "source_name": "NIOSH Pocket Guide to Chemical Hazards", "grade": "A"})
        public = audit.RecordingSource(self._source([doc]))
        workflow_fixture = gel_wash_fixture()
        row = _run(audit._help_case(server_module, self._case(self.ASKED), workflow_fixture, public))
        self.assertTrue(row["help_shown"])
        self.assertTrue(row["searched"])
        self.assertTrue(row["public_in_reply"])
        self.assertEqual(row["pieces"][0]["title"], "Acetonitrile")
        self.assertEqual(row["pieces"][0]["shares"].casefold(), "acetonitrile")
        self.assertIn("NIOSH Pocket Guide to Chemical Hazards", row["spoken_public"])
        self.assertEqual(row["spoken_kind"], "pointer")  # an English piece in a Korean turn is pointed to
        self.assertEqual(row["public_rule_violations"], [])
        self.assertEqual(row["help_dropped"], [])
        self.assertEqual(len(public.calls), 1)

    def test_a_piece_sharing_no_word_with_the_problem_is_not_attached(self) -> None:
        doc = FoundDocument(
            id="wiki-h2", text="Hydrogen · Intro\nHydrogen is the lightest element.", score=0.9,
            metadata={"title": "Hydrogen", "locator": "Intro", "language": "en", "source_id": "wikipedia-en",
                      "source_name": "Wikipedia (English)", "grade": "C"})
        public = audit.RecordingSource(self._source([doc]))
        row = _run(audit._help_case(server_module, self._case("왜 안 되지?"), gel_wash_fixture(), public))
        self.assertFalse(row["public_in_reply"])
        self.assertEqual(row["pieces"], [])


class CommandLineTests(TinyBundleCase):
    def test_map_writes_the_table_and_returns_zero(self) -> None:
        coverage = self.root / "coverage.json"
        coverage.write_text(json.dumps(TINY_TABLE, ensure_ascii=False), encoding="utf-8")
        out, markdown = self.root / "map.json", self.root / "map.md"
        with patch.object(audit, "default_glossary_path", return_value=self.glossary_path):
            code = audit.main(["map", "--runtime-dir", str(self.root), "--backend", "fts5",
                               "--coverage", str(coverage), "--out", str(out), "--out-md", str(markdown)])
        self.assertEqual(code, 0)
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["totals"]["cells"], 4)
        self.assertEqual(result["load"]["moss_loads"], 0)
        self.assertIn("| 장비 |", markdown.read_text(encoding="utf-8"))

    def test_inventory_writes_counts_and_the_sample(self) -> None:
        out = self.root / "inventory.json"
        code = audit.main(["--runtime-dir", str(self.root), "inventory", "--sample-size", "5", "--out", str(out)])
        self.assertEqual(code, 0)
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["sample"]["size"], 5)
        self.assertEqual(result["totals"]["pieces"], len(RECORDS))


if __name__ == "__main__":
    unittest.main()
