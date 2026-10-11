"""An analysis stored before the analyser improved says so (lane RN, 3).

Human decision 3 of 2026-10-11. Lane EV2 made the analysis state how a
source puts a repeat count (``repeat_count_kind``); an analysis stored before
that holds fixed repeats without the kind, and its count may be one round
short. Such an analysis is still served and still runs, but the catalog
entry carries ``analysis_outdated_codes`` (``repeat_count_kind_missing``)
and one Korean line, "분석기 개선 전 분석 · 다시 분석 권장", which the protocol
list and the start screen show. An analysis with no fixed repeat, or whose
repeats carry a kind, carries nothing.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_catalog import write_text_pdf
from tests.test_screen_cleanup import run_page_script
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ANALYSIS_OUTDATED_KO, ProtocolCatalog

LABEL = "분석기 개선 전 분석 · 다시 분석 권장"
PDF_TEXT = "Plug wash protocol 1. Add the plugs. 2. Wash the plugs. 3. Repeat step 2 twice. 4. Store the plugs."


def evidence(excerpt: str) -> dict:
    return {"source_page_number": 1, "source_excerpt": excerpt}


class _Model:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        return self.reply


class OutdatedAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = self.root / "wash.pdf"
        write_text_pdf(self.pdf, PDF_TEXT, title="Plug wash")
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="wash.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def analyze(self, *, repetition: bool, kind: str | None, with_kind: bool = True) -> None:
        extraction = extract_protocol_pdf(self.pdf)
        constructs = []
        if repetition:
            construct = {
                "type": "fixed_range_repetition", "repetition_id": "repeat-2",
                "start_step_id": "step-2", "end_step_id": "step-2",
                "range_source_text": "Repeat step 2 twice.",
                "evidence": evidence("3. Repeat step 2 twice."), "repeat_count": 2, "step_id": "step-3",
            }
            if with_kind:
                construct["repeat_count_kind"] = kind
            constructs.append(construct)
        reply = {
            "analysis_schema_version": 1, "pdf_sha256": extraction.sha256,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "protocol_id": self.protocol_id,
                "metadata": {"title": "Plug wash protocol", "original_language": "en",
                             "evidence": evidence("Plug wash protocol")},
                "before_start": [], "materials": [], "equipment": [],
                "sections": [{
                    "section_id": "wash", "title_source_text": "Plug wash protocol",
                    "evidence": evidence("Plug wash protocol"),
                    "steps": [
                        {"step_id": f"step-{n}", "source_label": str(n), "instruction_source_text": text,
                         "evidence": evidence(f"{n}. {text}")}
                        for n, text in ((1, "Add the plugs."), (2, "Wash the plugs."),
                                        (3, "Repeat step 2 twice."), (4, "Store the plugs."))
                    ],
                }],
                "constructs": constructs, "description": None,
            },
        }
        analysis_id = "analysis-" + "f" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        self.catalog.analyze(self.protocol_id, _Model(json.dumps(reply)), analysis_id=analysis_id)

    def test_a_fixed_repeat_without_a_kind_marks_the_analysis_outdated(self) -> None:
        # The stored shape of every analysis before lane EV2.
        self.analyze(repetition=True, kind=None, with_kind=False)
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.analysis_outdated_codes, ("repeat_count_kind_missing",))
        self.assertEqual(entry.analysis_outdated_ko, LABEL)
        self.assertEqual(ANALYSIS_OUTDATED_KO, LABEL)
        # It still runs; the line is advice, not a block.
        self.assertTrue(entry.available_for_execution)
        public = entry.public_dict()
        self.assertEqual(public["analysis_outdated_codes"], ["repeat_count_kind_missing"])
        self.assertEqual(public["analysis_outdated_ko"], LABEL)
        listed = [item.public_dict() for item in self.catalog.list_entries()]
        self.assertEqual([item["analysis_outdated_ko"] for item in listed], [LABEL])
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["analysis_outdated_ko"], LABEL)
        self.assertEqual(review["analysis_outdated_codes"], ["repeat_count_kind_missing"])

    def test_a_repeat_with_its_kind_is_current(self) -> None:
        self.analyze(repetition=True, kind="total")
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.analysis_outdated_codes, ())
        self.assertIsNone(entry.analysis_outdated_ko)
        self.assertIsNone(self.catalog.review(self.protocol_id)["analysis_outdated_ko"])

    def test_an_analysis_without_a_fixed_repeat_is_current(self) -> None:
        self.analyze(repetition=False, kind=None)
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.analysis_outdated_codes, ())
        self.assertIsNone(entry.analysis_outdated_ko)

    def test_before_any_analysis_there_is_nothing_to_say(self) -> None:
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.analysis_outdated_codes, ())
        self.assertIsNone(entry.analysis_outdated_ko)
        self.assertIsNone(self.catalog.review(self.protocol_id)["analysis_outdated_ko"])


class OutdatedAnalysisOnScreenTests(unittest.TestCase):
    def run_page(self, body: str) -> None:
        result = run_page_script(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_protocol_list_says_an_analysis_is_from_before_the_improvement(self) -> None:
        self.run_page(r"""
const entries=[
 {protocol_id:"p-head",title:"Headspace",revision_id:"pdf-1-analysis-1",analysis_status:"review_required",lifecycle_state:"ready",available_for_execution:true,analysis_outdated_codes:["repeat_count_kind_missing"],analysis_outdated_ko:"분석기 개선 전 분석 · 다시 분석 권장"},
 {protocol_id:"p-new",title:"Fresh",revision_id:"pdf-1-analysis-1",analysis_status:"review_required",lifecycle_state:"ready",available_for_execution:true,analysis_outdated_codes:[],analysis_outdated_ko:null},
 {protocol_id:"p-old",title:"Older",revision_id:"pdf-1",analysis_status:"review_required",lifecycle_state:"ready",available_for_execution:true}];
globalThis.fetch=async(url)=>{url=String(url);if(url==="/api/protocols")return json({protocols:entries});throw new Error(`unexpected ${url}`);};
await loadProtocolCatalog();
const labels=Object.fromEntries(node("protocol-id").options.filter(option=>option.value).map(option=>[option.value,option.textContent]));
assert(labels["p-head"]==="Headspace · 분석 통과 · 실행 가능 · 분석기 개선 전 분석 · 다시 분석 권장",`the list does not say it: ${labels["p-head"]}`);
assert(labels["p-new"]==="Fresh · 분석 통과 · 실행 가능",`a current analysis is marked: ${labels["p-new"]}`);
assert(labels["p-old"]==="Older · 분석 통과 · 실행 가능",`an entry from an older server is marked: ${labels["p-old"]}`);
assert(catalogStatus(entries[0]).kind==="ready","an outdated analysis is no longer runnable on screen");
""")

    def test_the_start_screen_says_it_in_its_own_line(self) -> None:
        self.run_page(r"""
const review=(extra)=>({protocol_id:"p-head",title:"Headspace",revision_id:"pdf-1-analysis-1",analysis_status:"review_required",lifecycle_state:"ready",available_for_execution:true,analysis_available:true,readiness:{status:"guidance_ready",reasons:[]},source:{filename:"headspace.pdf",sha256:"a".repeat(64),page_count:12},sections:[],execution_blockers:[],execution_notices:[],safety_notices:[],repeat_count_checks_ko:["반복 횟수 확인 필요 · 12~15단계 · 원문 “Repeat steps 12-15 twice more” · 분석이 2회로 읽음(처음을 포함한 총 횟수인지 원문을 확인하세요)"],...extra});
sessionActive=false;
renderProtocolReview(review({analysis_outdated_codes:["repeat_count_kind_missing"],analysis_outdated_ko:"분석기 개선 전 분석 · 다시 분석 권장"}));
const line=node("protocol-start-outdated");
assert(!line.hidden&&line.textContent==="분석기 개선 전 분석 · 다시 분석 권장",`the start screen line is missing: ${line.hidden} ${line.textContent}`);
assert(!node("protocol-start").hidden,"the start button is gone from an outdated analysis");
assert(!node("protocol-analysis-retry").hidden&&node("protocol-analysis-retry").textContent==="다시 분석","no 다시 분석 button beside the advice");
renderProtocolReview(review({analysis_outdated_codes:[],analysis_outdated_ko:null}));
assert(line.hidden,"the line stays for a current analysis");
renderProtocolReview(review({}));
assert(line.hidden,"the line shows for a review from an older server");
""")


if __name__ == "__main__":
    unittest.main()
