"""Korean for every uploaded PDF, started when its analysis passes (lane PX).

Human decision 1 (2026-10-06):

* (가) A revision whose analysis passed is translated at once, in the
  background, purpose and safety first and then step by step from step 1.
  Development activation, approval and a session's first opening reuse the
  same stored Korean: one generation per revision, never called again.
* (나) Every place that shows or speaks a step's text uses the translation
  when there is one; the source stays under "원문".
* (다) Until the Korean exists the source is shown and the card says the
  translation is being prepared; the next guidance after it lands is Korean.
* (라) The current step's text is one size and one shape, Korean or source.

Two causes measured the same day are fixed with it: translations were keyed
by the catalog revision id alone ("pdf-1-analysis-1"), the same string for
every uploaded PDF, so one protocol's glossary and rows reached another; and
the check demanded every material and equipment name verbatim and counted a
"N단계: " opening as a quantity when the source step carried no number.
Every model here is fake; nothing is called.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.protocol_vocabulary_support import MINIPREP_STEPS
from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from tests.test_revision_translations import FakeTranslator, good_korean, rich_fixture
from tests.test_revision_translation_context import FakeGlossary
from tests.test_screen_cleanup import run_page_script
from tests.test_screen_text_rules import PAGE_SETUP
from voiney_lab import experiment_protocol as domain
from voiney_lab import protocol_catalog as catalog_module
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.intent_arbitration import arbitrate_request
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    ProtocolCatalogUnavailableError,
    SharedSecretApprovalPolicy,
)
from voiney_lab.protocol_translation import (
    GlossaryEntry,
    TranslationUnit,
    check_translation,
    generate_revision_translations,
    required_terms_for,
    term_stem,
    translation_revision_key,
    translation_units,
    with_stored_translations,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import curated_screen_fields
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "voiney_lab" / "static" / "index.html"
PENDING_NOTE = "한국어 번역을 준비하고 있습니다 · 준비되면 다음 안내부터 한국어로 보여 드립니다. 지금은 원문입니다."
NO_NOTE = "한국어 번역이 없어 원문으로 보여 드립니다."


def run(coroutine):
    return asyncio.run(coroutine)


class _AnalysedCatalog(unittest.TestCase):
    """A registered PDF whose analysis the fake model passes."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.ready: list[str] = []
        self.authorized: list[str] = []
        self.catalog = ProtocolCatalog(
            self.store,
            on_analysis_ready=lambda catalog, pid: self.ready.append(pid),
            on_execution_authorized=lambda catalog, pid: self.authorized.append(pid))
        self.pdf = self.root / "sample.pdf"
        write_text_pdf(
            self.pdf,
            "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test")
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="sample.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def analyse(self) -> None:
        draft = analysis_draft(self.pdf, self.protocol_id, "Protocol Test")
        with patch.object(
            catalog_module, "analyze_protocol_extraction", return_value=draft,
        ):
            self.catalog.analyze(self.protocol_id, object(), analysis_id="analysis-1")


class AnalysisPassStartsTranslationTests(_AnalysedCatalog):
    """(가) The catalog hands a passed analysis over the moment it passes."""

    def test_a_passed_analysis_is_handed_over_once_and_before_any_authority(self) -> None:
        self.assertEqual(self.ready, [])
        self.analyse()
        self.assertEqual(self.ready, [self.protocol_id])
        self.assertEqual(self.authorized, [])
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.analysis_status, "review_required")
        self.assertFalse(entry.available_for_execution)

    def test_the_translation_fixture_is_the_one_the_session_will_run(self) -> None:
        self.analyse()
        draft_fixture = self.catalog.load_analysis_fixture(self.protocol_id)
        self.assertEqual(draft_fixture.status, "analysis_draft")
        self.catalog.acknowledge_readiness_gate(
            self.protocol_id, draft_fixture.revision_id,
            reason_code=domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            actor_principal_id="reviewer@example.org", actor_role="reviewer",
            comment="Warnings reviewed against the source.")
        self.catalog.activate_development(self.protocol_id)
        executable = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(executable.status, "approved_revision")
        self.assertEqual(draft_fixture.revision_id, executable.revision_id)
        self.assertEqual(draft_fixture.fixture_sha256, executable.fixture_sha256)
        self.assertEqual(
            translation_revision_key(draft_fixture),
            translation_revision_key(executable))
        self.assertEqual(
            [unit.fact_key for unit in translation_units(draft_fixture)],
            [unit.fact_key for unit in translation_units(executable)])
        # Activation still hands the revision over; the second pass makes no
        # call because every row is already stored (tested below).
        self.assertEqual(self.authorized, [self.protocol_id])

    def test_no_fixture_for_translation_before_an_analysis_passed(self) -> None:
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_analysis_fixture(self.protocol_id)

    def test_a_failing_hook_leaves_the_passed_analysis_as_it_is(self) -> None:
        def broken(catalog, protocol_id):
            raise RuntimeError("translator down")

        self.catalog.on_analysis_ready = broken
        self.analyse()
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.analysis_status, "review_required")
        self.assertEqual(entry.step_count, 1)

    def test_the_server_starts_it_with_the_analysis_fixture(self) -> None:
        self.analyse()
        seen = []
        saved = getattr(server_module.app.state, "revision_translation_runner", None)
        server_module.app.state.revision_translation_runner = seen.append
        try:
            server_module._translate_analyzed_revision(self.catalog, self.protocol_id)
        finally:
            server_module.app.state.revision_translation_runner = saved
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].status, "analysis_draft")
        self.assertEqual(
            seen[0].revision_id, self.catalog.get_entry(self.protocol_id).revision_id)

    def test_the_server_wires_the_hook_into_its_catalog(self) -> None:
        with patch.dict(os.environ, {
            "VOINEY_LAB_PROTOCOL_ENABLED": "true",
            "VOINEY_LAB_PROTOCOL_DATA_DIR": str(self.root / "server-catalog"),
        }):
            catalog, store = server_module._open_protocol_catalog()
        try:
            self.assertIs(catalog.on_analysis_ready, server_module._translate_analyzed_revision)
            self.assertIs(
                catalog.on_execution_authorized, server_module._translate_authorized_revision)
        finally:
            store.close()


class OneGenerationPerRevisionTests(unittest.TestCase):
    """(가) Stored under protocol/revision; a second start makes no call."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": self.temp.name,
        })
        self.env.start()

    def tearDown(self) -> None:
        server_module._REVISION_TRANSLATIONS_RUNNING.clear()
        self.env.stop()
        self.temp.cleanup()

    def _store(self):
        return initialize_workspace_store(WorkspaceSettings(True, Path(self.temp.name)))

    def test_rows_and_glossary_are_keyed_by_protocol_and_revision(self) -> None:
        fixture = rich_fixture()
        run(server_module.store_revision_translations(
            fixture, FakeTranslator(), model="fake", make_glossary=FakeGlossary()))
        key = translation_revision_key(fixture)
        self.assertEqual(key, f"{fixture.protocol_id}/{fixture.revision_id}")
        store = self._store()
        try:
            self.assertTrue(store.fact_translations(key, "ko"))
            self.assertEqual(store.fact_translations(fixture.revision_id, "ko"), ())
            self.assertIsNotNone(store.translation_glossary(key, "ko"))
            self.assertIsNone(store.translation_glossary(fixture.revision_id, "ko"))
        finally:
            store.close()

    def test_the_same_revision_started_again_calls_nothing(self) -> None:
        fixture = rich_fixture()
        first = run(server_module.store_revision_translations(
            fixture, FakeTranslator(), model="fake", make_glossary=FakeGlossary()))
        self.assertGreater(first.calls, 0)
        again = FakeTranslator()
        glossary = FakeGlossary()
        second = run(server_module.store_revision_translations(
            fixture, again, model="fake", make_glossary=glossary))
        self.assertEqual((second.calls, second.glossary_calls), (0, 0))
        self.assertEqual((again.batches, glossary.calls), ([], []))
        attached = server_module._with_revision_translations(fixture)
        self.assertEqual(
            attached.localized_fact("step-2", "current_step"), good_korean(MINIPREP_STEPS[1]))

    def test_two_protocols_with_the_same_catalog_revision_id_are_kept_apart(self) -> None:
        first = rich_fixture("pdf-1-analysis-1")
        other_protocol = replace(
            first.draft.protocol, protocol_id="protocol-other-upload")
        second = replace(first, draft=replace(first.draft, protocol=other_protocol))
        self.assertEqual(first.revision_id, second.revision_id)
        self.assertNotEqual(
            translation_revision_key(first), translation_revision_key(second))
        glossary = FakeGlossary()
        run(server_module.store_revision_translations(
            first, FakeTranslator(), model="fake", make_glossary=glossary))
        report = run(server_module.store_revision_translations(
            second, FakeTranslator(), model="fake", make_glossary=glossary))
        # The second protocol got its own glossary and its own rows.
        self.assertEqual(len(glossary.calls), 2)
        self.assertEqual(report.glossary_calls, 1)
        self.assertGreater(report.calls, 0)
        store = self._store()
        try:
            first_rows = store.fact_translations(translation_revision_key(first), "ko")
            second_rows = store.fact_translations(translation_revision_key(second), "ko")
        finally:
            store.close()
        self.assertTrue(first_rows and second_rows)
        self.assertEqual({row.revision_id for row in first_rows},
                         {translation_revision_key(first)})
        self.assertIs(with_stored_translations(second, first_rows), second)


class RequiredTermsTests(unittest.TestCase):
    """The names a Korean sentence must keep: the glossary's, singular or plural."""

    GLOSSARY = (
        GlossaryEntry("Falcon tube", "Falcon 튜브(Falcon tube)", True),
        GlossaryEntry("LB broth", "LB broth", True),
        GlossaryEntry("Petri dish", "페트리 접시", False),
        GlossaryEntry("agar", "한천", False),
        GlossaryEntry("glass Pasteur pipette", "유리 파스퇴르 피펫", False),
    )

    def test_stems_are_lower_case_singular_prefixes(self) -> None:
        self.assertEqual(term_stem("Porapak tubes"), "porapak tube")
        self.assertEqual(term_stem("Petri dishes"), "petri dish")
        self.assertEqual(term_stem("rubber septa"), "rubber septa")
        self.assertEqual(term_stem("glass"), "glass")
        self.assertEqual(term_stem("(10 mL)"), "(10 ml)")
        self.assertEqual(term_stem("Ringer's solution"), "ringer's solution")

    def test_glossary_names_are_required_by_their_name_words_and_glossary_words_are_free(self) -> None:
        unit = TranslationUnit(
            "step-6/current_step", "step",
            "6 Transfer 10 mL of autoclaved LB into two 50 mL falcon tubes on agar in Petri dishes.",
            ("falcon tubes", "LB", "agar", "Petri dishes", "glass Pasteur pipette"),
            step_index=5, step_label="6")
        # "Falcon tube" is kept in English by the glossary: its name word is
        # what the Korean must hold ("Falcon 튜브"). "LB" is a name on its
        # own. "agar", "Petri dishes" and the Pasteur pipette are Korean words
        # by the glossary.
        self.assertEqual(required_terms_for(unit, self.GLOSSARY), ("falcon", "lb"))

    def test_an_entry_that_merely_contains_the_term_says_nothing_about_it(self) -> None:
        glossary = (
            GlossaryEntry("LB agar", "LB agar", True),
            GlossaryEntry("Macherey Nagel Screw Caps", "Macherey Nagel Screw Caps", True),
            GlossaryEntry("agar", "한천", False),
        )
        unit = TranslationUnit("step-1/current_step", "step", "x", ("agar", "screw", "LB agar"))
        self.assertEqual(required_terms_for(unit, glossary), ("lb",))

    def test_without_a_glossary_only_name_like_terms_are_required(self) -> None:
        unit = TranslationUnit(
            "step-1/current_step", "step", "x",
            ("falcon tubes", "LB", "agar", "Petri dishes", "(10 mL)", "screw", "air entrainment kit"))
        self.assertEqual(required_terms_for(unit), ("lb", "petri", "ml"))

    def test_a_possessive_name_and_a_brand_before_a_korean_noun_pass(self) -> None:
        glossary = (
            GlossaryEntry("Ringer's solution", "링거액(Ringer's solution)", True),
            GlossaryEntry("Porapak tube", "Porapak 튜브(Porapak tube)", True),
        )
        ringer = TranslationUnit(
            "step-14/current_step", "step",
            "14 Transfer 10 mL of Ringer's solution into the falcon tube containing the bacterial pellet.",
            ("Ringer's solution",), step_index=13, step_label="14")
        self.assertEqual(check_translation(
            ringer, "14단계: 링거액(Ringer's solution) 10 mL를 세균 펠릿이 들어 있는 Falcon tube에 옮깁니다.",
            glossary), "passed")
        porapak = TranslationUnit(
            "step-36/current_step", "step",
            "36 In a fume hood, clamp the Porapak tubes so they are suspended.",
            ("Porapak tubes",), step_index=35, step_label="36")
        self.assertEqual(check_translation(
            porapak, "36단계: 흄 후드 안에서 Porapak 튜브가 매달리도록 클램프로 고정합니다.", glossary),
            "passed")
        self.assertEqual(check_translation(
            porapak, "36단계: 흄 후드 안에서 포라팍 튜브가 매달리도록 클램프로 고정합니다.", glossary),
            "term_missing")

    def test_a_korean_sentence_naming_an_instrument_in_english_is_korean(self) -> None:
        from voiney_lab.protocol_translation import is_korean

        self.assertTrue(is_korean("1. 안정화를 위해서 Seahorse XFe/XF Analyzer 를 켜서 예열합니다."))
        self.assertTrue(is_korean(
            "3. 센서 카트리지(sensor cartridge)를 Seahorse XF Calibrant (37°C in a non-CO2 incubator)에서 "
            "오버나잇 동안 hydrate 합니다."))
        self.assertFalse(is_korean("Add 5 mL buffer (완충액)."))
        self.assertFalse(is_korean("API ZYM"))
        self.assertFalse(is_korean("1 Prepare 400 mL of LB broth (25 g per litre)."))

    def test_the_headspace_sentences_refused_on_2026_10_06_now_pass(self) -> None:
        cases = (
            ("6 Transfer 10 mL of autoclaved LB into two 50 mL falcon tubes.",
             ("falcon tubes", "LB"),
             "6단계: 고압멸균한 LB 10 mL를 50 mL Falcon 튜브(Falcon tube) 2개에 옮깁니다."),
            ("10 Pour the autoclaved LB agar onto the required number of Petri dishes and leave to dry, until the agar has solidified.",
             ("agar", "Petri dishes", "LB"),
             "10단계: 고압멸균한 LB agar를 필요한 수의 페트리 접시에 붓고, 한천이 굳을 때까지 건조되도록 둡니다."),
            ("45 Place the glass chamber on top of the agar plate.", ("agar",),
             "45단계: 한천 배지 플레이트 위에 유리 챔버를 올려놓습니다."),
        )
        for source, terms, korean in cases:
            with self.subTest(source=source[:20]):
                label = source.split(" ", 1)[0]
                unit = TranslationUnit(
                    f"step-{label}/current_step", "step", source, terms,
                    step_index=int(label) - 1, step_label=label)
                self.assertEqual(check_translation(unit, korean, self.GLOSSARY), "passed")

    def test_a_dropped_glossary_name_or_a_changed_number_still_fails(self) -> None:
        unit = TranslationUnit(
            "step-6/current_step", "step",
            "6 Transfer 10 mL of autoclaved LB into two 50 mL falcon tubes.",
            ("falcon tubes", "LB"), step_index=5, step_label="6")
        self.assertEqual(
            check_translation(
                unit, "6단계: 고압멸균한 배지 10 mL를 50 mL Falcon 튜브(Falcon tube) 2개에 옮깁니다.",
                self.GLOSSARY),
            "term_missing")
        self.assertEqual(
            check_translation(
                unit, "6단계: 고압멸균한 LB 10 mL를 500 mL Falcon 튜브(Falcon tube) 2개에 옮깁니다.",
                self.GLOSSARY),
            "quantities_changed")

    def test_a_step_without_its_number_in_the_source_may_open_with_its_label(self) -> None:
        # ANKOM (protocols.io): the step text carries no number, the Korean
        # opens with "1단계: " as the style guide asks. Measured 2026-10-06,
        # every one of its 67 steps was refused as quantities_changed.
        unit = TranslationUnit(
            "step-1/current_step", "step",
            "Use a solvent resistant marker to label the filter bags to be used in the analysis.",
            (), step_index=0, step_label="1")
        self.assertEqual(check_translation(
            unit, "1단계: 내용매성 마커를 사용하여 분석에 사용할 filter bag에 라벨을 표시합니다."),
            "passed")
        note = TranslationUnit(
            "step-1/note_1", "note", "Keep the tube at 4 °C.", (), step_index=0, step_label="1")
        # A note is not a step: an added number stays an added number.
        self.assertEqual(check_translation(note, "1단계: 튜브를 4 °C에 둡니다."), "quantities_changed")

    def test_a_stored_row_is_rechecked_with_the_same_glossary(self) -> None:
        unit_fixture = rich_fixture()
        wrong_glossary = (GlossaryEntry("Tris-HCl buffer", "트리스 완충액", False),)
        translator = FakeTranslator()
        report = run(generate_revision_translations(
            unit_fixture, translator, model="fake", glossary=None))
        # Every row passed when made; read back with no glossary and with one,
        # the same rows hold, because the check is run the same way.
        reviewed, machine = __import__(
            "voiney_lab.protocol_translation", fromlist=["stored_localizations"],
        ).stored_localizations(unit_fixture, report.records)
        self.assertEqual(len(machine), report.passed)
        _, with_glossary = __import__(
            "voiney_lab.protocol_translation", fromlist=["stored_localizations"],
        ).stored_localizations(unit_fixture, report.records, wrong_glossary)
        self.assertEqual(len(with_glossary), report.passed)


class PendingTranslationTests(unittest.TestCase):
    """(다) The card says the Korean is being prepared, then shows it."""

    def tearDown(self) -> None:
        server_module._REVISION_TRANSLATIONS_RUNNING.clear()

    def test_the_screen_field_says_when_the_revision_is_being_translated(self) -> None:
        fixture = rich_fixture()
        curated = CuratedProtocolSession(fixture)
        curated.activate_configured()
        self.assertFalse(curated_screen_fields(curated)["translation_pending"])
        server_module._REVISION_TRANSLATIONS_RUNNING.add(translation_revision_key(fixture))
        self.assertTrue(curated_screen_fields(curated)["translation_pending"])
        server_module._REVISION_TRANSLATIONS_RUNNING.clear()
        self.assertFalse(curated_screen_fields(curated)["translation_pending"])

    def test_the_card_says_preparing_then_no_translation_then_korean(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await send({...baseState,primary_summary:null},{safety_items:[],translation_source:"none",translation_pending:true});
const note=node("procedure-translation-note");
assert(note.textContent===""" + repr(PENDING_NOTE) + r"""&&!note.hidden,"pending line missing: "+note.textContent);
assert(node("procedure-primary").textContent==="3 Cut the band into 1 mm cubes.","source is not the body while pending");
assert(node("procedure-source-toggle").hidden===true,"an empty fold is shown while pending");
await send({...baseState,primary_summary:null,revision:2},{safety_items:[],translation_source:"none",translation_pending:false});
assert(note.textContent===""" + repr(NO_NOTE) + r""","no-translation line missing once nothing is pending: "+note.textContent);
await send({...baseState,revision:3},{safety_items:[],translation_source:"machine",translation_pending:false});
assert(node("procedure-primary").textContent==="3단계: 밴드를 1 mm 크기로 자릅니다.","the Korean did not take the body");
assert(node("procedure-source-toggle").hidden===false,"the source is not folded under the Korean");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_korean_and_source_share_the_one_step_body(self) -> None:
        # (라) The same element and class carry the step's text either way.
        result = run_page_script(PAGE_SETUP + r"""
await send(baseState,{safety_items:[],translation_source:"reviewed"});
const korean=node("procedure-primary");
assert(korean.textContent.startsWith("3단계"),"Korean body: "+korean.textContent);
await send({...baseState,primary_summary:null,revision:2},{safety_items:[],translation_source:"none"});
const source=node("procedure-primary");
assert(source===korean&&source.textContent==="3 Cut the band into 1 mm cubes.","source body differs: "+source.textContent);
assert(node("procedure-instruction").textContent==="","the source is also in the small fold");
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('<p id="procedure-primary" class="step-body"></p>', html)
        css = (ROOT / "src" / "voiney_lab" / "static" / "app.css").read_text(encoding="utf-8")
        # The card's own paragraph rule (.procedure-card p) outranks a bare
        # class, so the step body is named with the card to keep its size.
        self.assertIn(".procedure-card p.step-body", css)


class StepTextUsesTheTranslationTests(unittest.TestCase):
    """(나) The preview, the start and the card speak the stored Korean."""

    def _translated_session(self) -> CuratedProtocolSession:
        fixture = rich_fixture()
        report = run(generate_revision_translations(fixture, FakeTranslator(), model="fake"))
        attached = with_stored_translations(fixture, report.records)
        session = CuratedProtocolSession(attached)
        session.activate_configured()
        return session

    def _turn(self, session, said: str, turn_id: int):
        return route_curated_runtime_turn(
            session, said, turn_id=turn_id, language="ko", configuration_id=1,
            generation=1, arbitration=arbitrate_request(said)).plan

    def test_next_step_preview_and_the_card_use_the_korean(self) -> None:
        session = self._translated_session()
        self._turn(session, "네, 시작해", 1)
        self.assertEqual(session.state()["primary_summary"], good_korean(MINIPREP_STEPS[0]))
        preview = self._turn(session, "다음 단계 미리보기", 2)
        self.assertIn(good_korean(MINIPREP_STEPS[1]).replace("한국어 번역: ", ""), preview.primary_text)
        self.assertNotIn(MINIPREP_STEPS[1], preview.primary_text)
        self.assertNotIn(MINIPREP_STEPS[1], preview.speech_text or "")

    def test_without_a_translation_the_source_is_spoken_as_before(self) -> None:
        session = CuratedProtocolSession(rich_fixture())
        session.activate_configured()
        self._turn(session, "네, 시작해", 1)
        preview = self._turn(session, "다음 단계 미리보기", 2)
        self.assertIn(MINIPREP_STEPS[1], preview.primary_text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
