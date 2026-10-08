"""Where a document is stuck on its way to a start, said in Korean (lane PX, 4).

Human decision of 2026-10-06: when a document does not reach a start, the
review says in one Korean line where it stopped -- 원문 읽기, OCR, 분석, 근거
대조, 실행 준비, 번역 -- why, and what a person does; and the English reasons
and notices still on the review screen are given in Korean, the server's
wording by the server and the page's by the page. The English
``ReadinessReason.message`` the analysis records is unchanged; ``message_ko``
stands beside it. Under the MVP rule (lane DI, 2026-10-08) there is no 사람
확인 stage: a passed analysis with no execution blocker is 실행 가능, and the
experimenter's press of start is the one confirmation.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from tests.test_revision_translations import FakeTranslator
from tests.test_revision_translation_context import FakeGlossary
from tests.test_screen_cleanup import run_page_script
from voiney_lab import experiment_protocol as domain
from voiney_lab import server as server_module
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    PIPELINE_STAGE_KO,
    READINESS_REASON_KO,
    ProtocolCatalog,
    readiness_reason_korean,
)
from voiney_lab.protocol_translation import translation_revision_key

KOREAN = re.compile(r"[가-힣]")


def korean(text: object) -> bool:
    return isinstance(text, str) and bool(KOREAN.search(text))


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = self.root / "sample.pdf"
        write_text_pdf(
            self.pdf, "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
            title="Protocol Test")
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="sample.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def analysed(self):
        draft = analysis_draft(self.pdf, self.protocol_id, "Protocol Test")
        return self.store.append_analysis_revision(
            self.protocol_id, 1, "analysis-1", draft.protocol, draft.readiness,
            draft.capability_policy_id)

    def pipeline(self) -> dict:
        return self.catalog.pipeline_status(self.protocol_id)


class StageLineTests(_Case):
    def test_an_uploaded_document_waits_for_its_analysis(self) -> None:
        line = self.pipeline()
        self.assertEqual((line["stage"], line["blocked"]), ("analysis", False))
        self.assertEqual(line["stage_ko"], "분석")
        self.assertTrue(korean(line["message"]))

    def test_a_failed_analysis_says_why_and_what_to_do(self) -> None:
        self.catalog.fail_analysis_request(
            self.protocol_id, "analysis-1", failure_code="provider_configuration_missing")
        line = self.pipeline()
        self.assertEqual((line["stage"], line["blocked"]), ("analysis", True))
        self.assertIn("분석 모델 설정", line["message"])
        self.assertIn("VOINEY_LAB_ANALYSIS_PROVIDER", line["action"])
        self.assertEqual(line["failure_code"], "provider_configuration_missing")
        self.store.append_event(
            "analysis-failed-evidence", self.protocol_id, 1, "protocol_analysis_failed",
            {"status": "failed", "failure_code": "protocol_analysis_invalid_evidence"})
        line = self.pipeline()
        self.assertEqual((line["stage"], line["stage_ko"], line["blocked"]),
                         ("evidence", "근거 대조", True))
        self.assertIn("원문 쪽의 글과 맞지 않아", line["message"])
        self.assertIn("분석 다시 시도", line["action"])

    def test_a_passed_analysis_whose_only_reason_is_a_notice_is_ready(self) -> None:
        # Lane DI (2026-10-08): no_declared_safety_warnings is a notice, not a
        # gate. The analysis passed, so the line says the document may run and
        # what the experimenter does: read the safety statements and press start.
        self.analysed()
        line = self.pipeline()
        self.assertEqual((line["stage"], line["blocked"]), ("ready", False))
        self.assertTrue(korean(line["message"]))
        self.assertIn("이 프로토콜로 시작", line["action"])
        self.assertNotIn("검토자", line["action"])
        self.assertNotIn("remaining_reason_codes", line)

    def test_an_analysis_with_an_execution_blocker_is_stuck_at_readiness(self) -> None:
        self.analysed()
        analysis = self.catalog._latest_analysis(
            self.catalog._latest_protocol_revision(self.protocol_id))
        blocked = domain.ReadinessAssessment(
            status=domain.ReadinessStatus.ANALYSIS_REQUIRED,
            label=domain.ANALYSIS_REQUIRED_LABEL,
            reasons=(domain.ReadinessReason(
                code=domain.ReadinessReasonCode.NO_EXECUTABLE_STEPS,
                message="The structured Protocol contains no executable source steps."),))
        self.store.append_analysis_revision(
            self.protocol_id, 1, "analysis-blocked", analysis.protocol, blocked,
            analysis.capability_policy_id)
        line = self.pipeline()
        self.assertEqual((line["stage"], line["blocked"]), ("readiness", True))
        self.assertIn("실행할 단계를 원문에서 찾지 못했습니다", line["message"])
        self.assertIn("분석 다시 시도", line["action"])
        self.assertEqual(line["remaining_reason_codes"], ["no_executable_steps"])

    def test_a_document_without_text_is_stuck_at_ocr_until_the_ocr_text_is_in(self) -> None:
        from tests.test_lane_px_auto_ocr import MixedProvider

        scanned = self.root / "scanned.pdf"
        write_text_pdf(scanned, None, title="Scanned")
        protocol_id = self.catalog.register(
            scanned, source_filename="scanned.pdf", media_type="application/pdf").entry.protocol_id
        line = self.catalog.pipeline_status(protocol_id)
        self.assertEqual((line["stage"], line["stage_ko"], line["blocked"]), ("ocr", "OCR", True))
        self.assertIn("VOINEY_LAB_OCR_PROVIDERS", line["action"])
        self.catalog.run_ocr(protocol_id, MixedProvider(), ocr_id="ocr-1", accepted_automatically=True)
        line = self.catalog.pipeline_status(protocol_id)
        self.assertEqual((line["stage"], line["blocked"]), ("analysis", False))
        self.assertEqual(line["ocr_page_numbers"], [1])

    def test_every_stage_has_a_korean_name(self) -> None:
        for stage in ("extraction", "ocr", "analysis", "evidence", "readiness",
                      "translation", "ready"):
            self.assertTrue(korean(PIPELINE_STAGE_KO[stage]) or PIPELINE_STAGE_KO[stage] == "OCR", stage)


class KoreanReasonTests(_Case):
    def test_every_readiness_reason_code_has_a_korean_line(self) -> None:
        for code in domain.ReadinessReasonCode:
            with self.subTest(code=code.value):
                self.assertIn(code.value, READINESS_REASON_KO)
                self.assertTrue(korean(readiness_reason_korean(code.value, "")))

    def test_pages_are_carried_into_the_korean(self) -> None:
        self.assertIn("3, 4쪽", readiness_reason_korean(
            "source_page_requires_ocr",
            "Source page(s) 3, 4 have no usable text layer; their OCR text must be accepted."))
        self.assertIn("원문 2쪽", readiness_reason_korean(
            "source_page_not_fully_read",
            "The analysis left a stated value unaccounted on 2 source page(s)."))
        self.assertIsNone(readiness_reason_korean("reason_from_a_newer_server", "x"))

    def test_the_review_carries_korean_beside_every_english_reason(self) -> None:
        before = self.catalog.review(self.protocol_id)
        self.assertTrue(korean(before["readiness"]["label_ko"]))
        self.assertTrue(korean(before["readiness"]["reasons"][0]["message_ko"]))
        self.assertIn("pipeline", before)
        self.analysed()
        review = self.catalog.review(self.protocol_id)
        readiness = review["readiness"]
        self.assertEqual(readiness["label_ko"], "시작 전에 읽을 알림이 있음")
        for reason in readiness["reasons"]:
            self.assertTrue(korean(reason["message_ko"]), reason)
            # The recorded English is untouched.
            self.assertIn("A reviewer must confirm", reason["message"])
        # Lane DI: the reason is a notice the experimenter reads before
        # starting, not a blocker; the document is ready.
        self.assertEqual(review["execution_blockers"], [])
        self.assertTrue(korean(review["execution_notices"][0]["message_ko"]))
        self.assertEqual(review["execution_notices"][0]["kind"], "source_note")
        self.assertEqual(review["pipeline"]["stage"], "ready")


class TranslationStageTests(_Case):
    def tearDown(self) -> None:
        server_module._REVISION_TRANSLATIONS_RUNNING.clear()
        super().tearDown()

    def test_without_a_workspace_the_line_says_translation_is_off(self) -> None:
        self.analysed()
        review = self.catalog.review(self.protocol_id)
        with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
            line = server_module._pipeline_with_translation(self.catalog, self.protocol_id, review)
        self.assertEqual(line["stage"], "ready")
        self.assertIn("이 프로토콜로 시작", line["action"])
        self.assertEqual(line["translation"]["state"], "off")
        self.assertIn("원문으로 안내", line["translation"]["message"])

    def test_with_a_workspace_the_line_follows_the_korean_as_it_is_made(self) -> None:
        self.analysed()
        review = self.catalog.review(self.protocol_id)
        fixture = self.catalog.load_analysis_fixture(self.protocol_id)
        with tempfile.TemporaryDirectory() as workspace, patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": workspace,
            "VOINEY_LAB_TRANSLATION_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "offline-test-key",
        }):
            pending = server_module._pipeline_with_translation(
                self.catalog, self.protocol_id, review)["translation"]
            self.assertEqual(pending["state"], "pending")
            server_module._REVISION_TRANSLATIONS_RUNNING.add(translation_revision_key(fixture))
            running = server_module._pipeline_with_translation(
                self.catalog, self.protocol_id, review)["translation"]
            self.assertEqual(running["state"], "running")
            self.assertIn("번역 준비 중", running["message"])
            server_module._REVISION_TRANSLATIONS_RUNNING.clear()
            asyncio.run(server_module.store_revision_translations(
                fixture, FakeTranslator(), model="fake", make_glossary=FakeGlossary()))
            done = server_module._pipeline_with_translation(
                self.catalog, self.protocol_id, review)
            self.assertEqual(done["translation"]["state"], "done")
            self.assertEqual(
                (done["translation"]["steps_korean"], done["translation"]["steps_total"]), (1, 1))
            self.assertIn("번역 끝", done["translation"]["message"])
            self.assertIn("이 프로토콜로 시작", done["action"])


class PageTests(unittest.TestCase):
    def test_the_review_shows_the_stage_line_and_korean_reasons_only(self) -> None:
        result = run_page_script(r"""
const review={protocol_id:"protocol-x",title:"Headspace",revision_id:"pdf-1-analysis-1",lifecycle_state:"blocked",readiness_status:"analysis_required",available_for_execution:false,analysis_available:true,
 source:{filename:"headspace.pdf",sha256:"a".repeat(64),page_count:16},
 pipeline:{stage:"readiness",stage_ko:"실행 준비",blocked:true,message:"실행할 단계를 원문에서 찾지 못했습니다. 원문을 확인하고 '분석 다시 시도'를 누르세요.",action:"원문을 확인하고 '분석 다시 시도'를 누르세요.",ocr_page_numbers:[3],translation:{state:"running",message:"번역 준비 중 · 한국어 단계 12/62 · 준비되면 다음 안내부터 씁니다."}},
 readiness:{status:"analysis_required",label:"Protocol analysis required",label_ko:"시작 전에 읽을 알림이 있음",reasons:[
  {code:"unresolved_ambiguity",message:"A source ambiguity remains unresolved.",message_ko:"원문에 서로 다른 두 서술이 있어 어느 쪽이 맞는지 정해지지 않았습니다."},
  {code:"no_executable_steps",message:"The structured Protocol contains no executable source steps.",message_ko:"실행할 단계를 원문에서 찾지 못했습니다."}]},
 execution_blockers:[{code:"no_executable_steps",message:"The structured Protocol contains no executable source steps.",message_ko:"실행할 단계를 원문에서 찾지 못했습니다.",kind:"blocking",source_page_number:null,source_excerpt:null,step_id:null}],
 execution_notices:[{code:"unresolved_ambiguity",message:"A source ambiguity remains unresolved.",message_ko:"원문에 서로 다른 두 서술이 있어 어느 쪽이 맞는지 정해지지 않았습니다.",kind:"source_note",source_page_number:4,source_excerpt:"20 µL or 30 µL",step_id:"step-4"}],
 safety_notices:[],sections:[]};
renderProtocolReview(review);
const hosts=["protocol-review-content","protocol-blockers","protocol-start-summary","protocol-start-overview","protocol-execution-blockers","protocol-safety-notices","protocol-execution-notices"].map(node);
const visible=hosts.map(visibleText).join(" "),dev=hosts.map(devText).join(" ");
assert(visible.includes("막힘 · 실행 준비 · 실행할 단계를 원문에서 찾지 못했습니다"),`stage line missing: ${visible}`);
assert(visible.includes("할 일 · 원문을 확인하고 '분석 다시 시도'를 누르세요."),`action missing: ${visible}`);
assert(visible.includes("OCR 쪽 · p.3"),`OCR pages missing: ${visible}`);
assert(visible.includes("번역 · 번역 준비 중 · 한국어 단계 12/62"),`translation line missing: ${visible}`);
assert(visible.includes("원문에 서로 다른 두 서술이 있어"),`Korean reason missing: ${visible}`);
assert(visible.includes("실행을 막는 사유 1건")&&visible.includes("시작 전 알림 1건"),`execution lists missing: ${visible}`);
assert(node("protocol-start").hidden,"a blocked protocol offered the start button");
for(const english of ["A source ambiguity remains unresolved.","The structured Protocol contains no executable source steps."]){
 assert(!visible.includes(english),`English still on screen: ${english}`);
 assert(dev.includes(english),`English not kept in 개발 상세 정보: ${english}`);
}
for(const word of ["검토자","승인"])assert(!visible.includes(word),`${word} still on the screen: ${visible}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
