"""Lane FX3, decision 3: the screen's web-search code goes, and the server stops sending its entry.

Lane DOC3 deleted the xAI web reference search but left three things on the
screen -- the "authoritative_web" progress label, the tool label of
``search_authoritative_web`` and ``webReferencesOn()`` -- and, because the
screen dropped its whole capability display without it, the server kept
sending ``research_capabilities.external_text`` as "disabled". These tests
pin that the names are gone, that the capability display stands without the
entry, and that a reference check's limit stays in the turn's developer
details as it did with the search off.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, ROOT, run_page_script

SERVER = ROOT / "src" / "voiney_lab" / "server.py"


class TheNamesAreGoneTests(unittest.TestCase):
    def test_the_page_names_none_of_the_search(self) -> None:
        page = INDEX.read_text(encoding="utf-8")
        for word in (
            "authoritative_web", "search_authoritative_web", "webReferencesOn",
            "external_text", "research-text-capability",
        ):
            with self.subTest(word=word):
                self.assertFalse(word in page, f"{word} is still on the page")

    def test_the_server_no_longer_sends_the_entry(self) -> None:
        self.assertFalse("external_text" in SERVER.read_text(encoding="utf-8"))


class TheCapabilityDisplayStandsTests(unittest.TestCase):
    def test_capabilities_without_the_entry_are_drawn(self) -> None:
        result = run_page_script(r"""
assert(renderResearchCapabilities({supplemental_model:{status:"enabled",authority:"supplemental_model_knowledge"},multi_brain:{status:"disabled"}})===true,
 "the capabilities were dropped without external_text");
assert(node("supplemental-knowledge-capability").textContent==="사용 가능 · 권위 근거 아님",node("supplemental-knowledge-capability").textContent);
assert(node("multi-brain-capability").textContent==="사용 안 함 · 상태 변경 권한 없음",node("multi-brain-capability").textContent);
assert(renderResearchCapabilities({supplemental_model:{status:"bogus"}})===false,"an invalid status was drawn");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_reference_limit_stays_in_the_developer_details(self) -> None:
        # Even a server that still says the search is on does not bring the
        # visible "웹 참고 자료 확인 …" card back.
        result = run_page_script(r"""
acceptedSessionConfiguration={configuration_id:7,mode:"cascade",language:"ko",protocol_id:"p-1",revision_id:"r-1"};sessionActive=true;
renderResearchCapabilities({external_text:{status:"enabled"},supplemental_model:{status:"enabled"}});
const send=payload=>onMessage({data:JSON.stringify({configuration_id:7,...payload})},sessionGeneration,socket);
await send({type:"speech.start",turn_id:4,generation:0});
await send({type:"reply.complete",turn_id:4,generation:0,text:"PDF에서 확인할 수 없어요."});
await send({type:"research.state",turn_id:4,generation:0,status:"running",phase:"supplemental_model"});
await send({type:"research.result",turn_id:4,generation:0,status:"unavailable",terminal_status:"timeout",limitation:"시간 안에 찾지 못했습니다."});
const card=turnNode(4,sessionGeneration);
assert(!visibleText(card.querySelector(".reply")).includes("웹 참고 자료"),`a web card is on the screen: ${visibleText(card.querySelector(".reply"))}`);
assert(card.querySelector(".turn-diagnostics-body").textContent.includes("timeout"),"the limit is not in the developer details");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
