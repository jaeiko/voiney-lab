"""Lane AN3, stage ③: the page OCR card and the numbering-seam card on the card screen.

Human decision 2026-10-18: lane RV2's card screen (stage ②) draws any card from
its fields with one function, so lane AN3's ``ocr_page`` and ``numbering_seam``
cards are drawn by it as they are; the screen code is not changed here. These
tests take the cards the server itself builds (the catalog, with fake OCR
engines and fake models), draw them with the page's own script under node, press
their buttons, and hand the requests the screen sent back to the catalog -- so a
card the server makes and the request the screen sends for it are checked
against each other. Nothing here is "live-tested".
"""

from __future__ import annotations

import json
import unittest

from tests.test_lane_an3_numbering_seams import _PiecesCase
from tests.test_lane_an3_ocr_two_engines import SCAN, _ScanCase, provider
from tests.test_lane_rv2_card_screen import SETUP
from tests.test_screen_cleanup import run_page_script

#: The screen's posts, printed for the test to hand back to the catalog.
REPORT = r"""
console.log("POSTS:"+JSON.stringify(posts().map(call=>({url:call.url,body:call.body}))));
"""


def screen(draft: dict, body: str):
    """Run ``body`` with the server's draft served as the review draft."""

    served = json.dumps(draft, ensure_ascii=False)
    script = (SETUP + f"\nconst serverDraft={served};\n" + body + REPORT)
    return run_page_script(script)


def posts_of(result) -> list[dict]:
    line = next(line for line in result.stdout.splitlines() if line.startswith("POSTS:"))
    return json.loads(line[len("POSTS:"):])


class PageOcrCardOnTheScreenTests(_ScanCase):
    def setUp(self) -> None:
        super().setUp()
        other = [line.replace("4000 rpm", "400 rpm").replace("4 C", "8 C") for line in SCAN]
        self.analyze(provider(SCAN, other))
        self.draft = self.catalog.review_draft(self.protocol_id)

    def test_the_page_card_shows_each_value_its_engines_readings_and_picture(self) -> None:
        result = screen(self.draft, r"""
serve([["/review-draft",serverDraft]]);
await loadReviewDraft(serverDraft.protocol_id);await tick();
assert(node("review-card-progress").textContent==="1 / 1",node("review-card-progress").textContent);
const text=visibleText(card());
// Round 2, decision ②: 2 of the page's 7 numbers differ, so 5 min (read alike) is asked too.
for(const words of ["OCR 값","원문 2쪽 그림의 값 3개를 확인해 주세요."])assert(text.includes(words),`${words}: ${text}`);
const rows=walk(card()).filter(el=>el.className==="choice-card-values-row");
assert(rows.length===3,`rows: ${rows.length}`);
assert(JSON.stringify(rows.map(row=>row.dataset.valueId))===JSON.stringify(["ocr_value:2:4000rpm","ocr_value:2:5min","ocr_value:2:4"]),rows.map(row=>row.dataset.valueId).join("|"));
assert(visibleText(rows[1]).includes("두 OCR 엔진이 이 쪽 숫자의 15% 이상을 다르게 읽어, 같게 읽은 값도 확인해요"),visibleText(rows[1]));
assert(JSON.stringify(labels(rows[1]))===JSON.stringify(["5 min","직접 입력"]),`5 min buttons: ${labels(rows[1])}`);
assert(walk(card()).filter(el=>el.className==="choice-card-reasons").length===0,"the card repeats its values' reasons");
const first=visibleText(rows[0]);
for(const words of ["4000 rpm","두 OCR 엔진이 이 값을 다르게 읽음","Google · 4000 rpm  /  CLOVA · 400 rpm"])assert(first.includes(words),`${words}: ${first}`);
assert(visibleText(rows[2]).includes("Google · 4  /  CLOVA · 8"),visibleText(rows[2]));
assert(JSON.stringify(labels(rows[0]))===JSON.stringify(["4000 rpm","400 rpm","직접 입력"]),`value buttons: ${labels(rows[0])}`);
const images=walk(rows[0]).filter(el=>el.tagName==="img");
assert(images.length>=1&&images.every(image=>/\/review-draft\/source-pages\/2\/value-boxes\/[0-9]+\/image$/.test(image.src)),images.map(image=>image.src).join("|"));
const top=buttonsIn(card()).filter(button=>!rows.some(row=>walk(row).includes(button))).map(button=>button.textContent);
assert(JSON.stringify(top)===JSON.stringify(["모두 맞아요"]),`card buttons: ${top}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        cards = self.draft["cards"]
        boxes = cards[0]["values"][0]["value_boxes"]
        self.assertTrue(boxes, "the server named no value box for the first value")

    def test_all_correct_sends_the_cards_request_and_the_server_takes_it(self) -> None:
        result = screen(self.draft, r"""
const after={...serverDraft,cards:serverDraft.cards.map(each=>({...each,decided:true})),next_card:null,registrable:true};
serve([["/review-decisions",answer(after.cards,null)],["/review-draft",serverDraft]]);
await loadReviewDraft(serverDraft.protocol_id);await tick();
await press(card(),"모두 맞아요");
assert(posts().length===1,`posts: ${posts().length}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        (post,) = posts_of(result)
        self.assertEqual(post["url"], f"/api/protocols/{self.protocol_id}/review-decisions")
        body = post["body"]
        self.assertEqual({key: body[key] for key in ("item_id", "action")},
                         {"item_id": "ocr_page:2", "action": "confirm"})
        answer = self.catalog.record_review_decision(self.protocol_id, body)
        self.assertTrue(answer["registrable"])
        self.assertTrue(answer["cards"][0]["decided"])

    def test_the_other_engines_reading_sends_a_fix_with_its_words(self) -> None:
        result = screen(self.draft, r"""
serve([["/review-decisions",answer(serverDraft.cards,0)],["/review-draft",serverDraft]]);
await loadReviewDraft(serverDraft.protocol_id);await tick();
const rows=walk(card()).filter(el=>el.className==="choice-card-values-row");
await press(rows[0],"400 rpm");
assert(posts().length===1,`posts: ${posts().length}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        (post,) = posts_of(result)
        body = post["body"]
        self.assertEqual({key: body[key] for key in ("item_id", "action", "text")},
                         {"item_id": "ocr_value:2:4000rpm", "action": "fix", "text": "400 rpm"})
        answer = self.catalog.record_review_decision(self.protocol_id, body)
        changed = [card for card in answer["cards"] if card["type"] == "value_changed"]
        self.assertEqual([card["item_id"] for card in changed], ["step:s2"])


class NumberingSeamCardOnTheScreenTests(_PiecesCase):
    def setUp(self) -> None:
        super().setUp()
        self.mixed()
        self.draft = self.catalog.review_draft(self.protocol_id)

    def test_the_seam_card_shows_both_steps_with_their_lines_and_two_buttons(self) -> None:
        result = screen(self.draft, r"""
serve([["/review-draft",serverDraft]]);
await loadReviewDraft(serverDraft.protocol_id);await tick();
assert(node("review-card-progress").textContent==="1 / 2",node("review-card-progress").textContent);
const text=visibleText(card());
for(const words of ["조각 경계","번호를 붙인 부분과 붙이지 않은 부분이 만나는 곳이에요. 앞 단계 다음에 뒤 단계가 이어지나요?"])assert(text.includes(words),`${words}: ${text}`);
assert(text.split("번호 방식이 바뀌는 경계").length===2,`the reason is said more than once: ${text}`);
const steps=walk(card()).filter(el=>el.tagName==="li").map(visibleText);
assert(steps.length===2,`steps: ${steps.join(" || ")}`);
assert(steps[0].includes("Incubate for 10 min at 37 C.")&&steps[0].includes("원문 p.1 4줄"),steps[0]);
assert(steps[1].startsWith("3. ")&&steps[1].includes("Wash the pellet twice.")&&steps[1].includes("원문 p.2 2줄"),steps[1]);
assert(JSON.stringify(labels(card()).filter(label=>label!=="메모 남기기"))===JSON.stringify(["이어져요","아니요"]),`buttons: ${labels(card())}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_follows_on_is_a_decision_the_server_takes_and_no_asks_for_a_reanalysis(self) -> None:
        result = screen(self.draft, r"""
serve([["/review-decisions",answer(serverDraft.cards.map((each,index)=>index===0?{...each,decided:true}:each),1)],["/review-draft",serverDraft]]);
let asked=null;analyzeRegisteredProtocol=async(id,options)=>{asked={id,...options};};loadProtocolCatalog=async()=>{};
globalThis.confirm=()=>true;
await loadReviewDraft(serverDraft.protocol_id);await tick();
await press(card(),"이어져요");
assert(posts().length===1,`posts: ${posts().length}`);
assert(node("review-card-progress").textContent==="2 / 2",node("review-card-progress").textContent);
await press(card(),"아니요");
assert(posts().length===1,"아니요 sent a decision");
assert(asked&&asked.id===serverDraft.protocol_id&&asked.reanalysis===true,JSON.stringify(asked));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        (post,) = posts_of(result)
        body = post["body"]
        self.assertEqual({key: body[key] for key in ("item_id", "action")},
                         {"item_id": "seam:p1.s2+p2.s3", "action": "confirm"})
        answer = self.catalog.record_review_decision(self.protocol_id, body)
        self.assertEqual([card["decided"] for card in answer["cards"]], [True, False])
        self.assertFalse(answer["registrable"])


if __name__ == "__main__":
    unittest.main()
