"""Lane IA, decision ⑧ (2026-10-19): lane AN3's four screen fixes on the cards.

Lane AN3 (report, section 9) found four things on the card screen and left
them for a later lane: ① a seam card's step line said its number twice
("2. 2. Incubate…": choiceStepWords put the label before a text that already
starts with it); ② a step whose source has no number (label_printed false)
showed its given number like a printed one; ③ a page card had no way to open
the whole page; ④ a value's buttons did not say which engine read each value
(the server sends ``engine`` on each). Drawn from the card's fields alone, as
lane RV2's one function does. Shares lane RV2's fake page and cards.
"""

from __future__ import annotations

import unittest

from tests.test_lane_rv2_card_screen import SETUP
from tests.test_screen_cleanup import INDEX, run_page_script

CARDS = r"""
const page3Engines={...page3,values:[{...page3.values[0],choices:[
 {...page3.values[0].choices[0],engine:"google"},{...page3.values[0].choices[1],engine:"clova"},page3.values[0].choices[2]]}]};
const seamPrinted={...seam,steps:[
 {step_id:"p1.s2",label:"2",label_shown:"2",label_printed:true,text:"2. Incubate the gel pieces for 30 min.",page:1,lines:{page:1,first_line:4,last_line:4}},
 {step_id:"p2.s3",label:"",label_shown:"3",label_printed:false,text:"Wash the pellet twice.",page:2,lines:{page:2,first_line:2,last_line:2}}]};
"""


def run(body: str):
    return run_page_script(SETUP + CARDS + body)


class StepNumberTests(unittest.TestCase):
    def test_a_step_whose_text_starts_with_its_number_is_not_numbered_twice(self) -> None:
        result = run(r"""
serve([["/review-draft",draft({cards:[seamPrinted],next_card:0})]]);
await loadReviewDraft("p-1");await tick();
const steps=walk(card()).filter(el=>el.tagName==="li").map(visibleText);
assert(steps.length===2,`steps: ${steps.length}`);
assert(steps[0].startsWith("2. Incubate the gel pieces for 30 min.")&&!steps[0].includes("2. 2."),steps[0]);
assert(choiceStepWords({label:"12",label_printed:true,text:"12. Wash twice."})==="12. Wash twice.","a printed number at the start of the text was doubled");
assert(choiceStepWords({label:"12",label_printed:true,text:"Wash twice."})==="12. Wash twice.","a printed number not in the text was dropped");
assert(choiceStepWords({label:"4",text:"4. Spin at 500 x g",page:2,lines:{page:2,first_line:3,last_line:3}})==="4. Spin at 500 x g · 원문 p.2 3줄","the readings' step was doubled");
assert(choiceStepWords({label:"1",label_printed:true,text:"10 mL of buffer"})==="1. 10 mL of buffer","a number that only begins a longer number was taken as the label");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_number_the_source_does_not_print_is_shown_in_brackets_and_said_once(self) -> None:
        result = run(r"""
serve([["/review-draft",draft({cards:[seamPrinted],next_card:0})]]);
await loadReviewDraft("p-1");await tick();
const steps=walk(card()).filter(el=>el.tagName==="li").map(visibleText);
assert(steps[1].startsWith("[3] Wash the pellet twice.")&&steps[1].includes("원문 p.2 2줄"),steps[1]);
const notes=walk(card()).filter(el=>el.className==="choice-card-note").map(visibleText);
assert(notes.some(note=>note.includes("[ ] 번호는 원문에 없는 번호")),`no note on the given number: ${notes}`);
// A label without label_printed but with an empty label is the same case (the server's older shape).
assert(choiceStepWords({label:"",label_shown:"13",text:"Dry the pellet."})==="[13] Dry the pellet.","an unprinted number was shown as printed");
// Nothing to say when every number is printed.
serve([["/review-draft",draft({cards:[readings],next_card:0})]]);
await loadReviewDraft("p-1");await tick();
assert(!walk(card()).some(el=>el.className==="choice-card-note"&&visibleText(el).includes("원문에 없는 번호")),"the note shows without an unprinted number");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class PageCardTests(unittest.TestCase):
    def test_a_page_card_opens_the_whole_page(self) -> None:
        result = run(r"""
serve([["/review-draft",draft({cards:[page3Engines],next_card:0})]]);
await loadReviewDraft("p-1");await tick();
const links=walk(card()).filter(el=>el.tagName==="a"&&el.className==="compare-page-link");
assert(links.length===1,`page links: ${links.length}`);
assert(links[0].textContent==="원본 쪽 보기 (p.3)",links[0].textContent);
assert(links[0].href==="/api/protocols/p-1/review-draft/source-pages/3/image"&&links[0].target==="_blank"&&links[0].rel==="noopener",`${links[0].href} ${links[0].target}`);
// A card with no values of its own (one value, its piece beside it) keeps the page to its lines slot.
serve([["/review-draft",draft({cards:[ocrValue],next_card:0})]]);
await loadReviewDraft("p-1");await tick();
assert(walk(card()).filter(el=>el.tagName==="a"&&el.className==="compare-page-link").length===0,"a single-value card grew a page link");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_values_buttons_say_which_engine_read_it(self) -> None:
        result = run(r"""
serve([["/review-draft",draft({cards:[page3Engines],next_card:0})]]);
await loadReviewDraft("p-1");await tick();
const rows=walk(card()).filter(el=>el.className==="choice-card-values-row");
assert(JSON.stringify(labels(rows[0]))===JSON.stringify(["5 min","6 min","직접 입력"]),`buttons: ${labels(rows[0])}`);
const tags=walk(rows[0]).filter(el=>el.className==="choice-card-engine").map(el=>el.textContent);
assert(JSON.stringify(tags)===JSON.stringify(["Google","CLOVA"]),`engine tags: ${tags}`);
const buttons=buttonsIn(rows[0]);
assert(buttons[0].attributes["aria-label"]==="5 min · Google 읽기"&&buttons[1].attributes["aria-label"]==="6 min · CLOVA 읽기",`${buttons[0].attributes["aria-label"]} / ${buttons[1].attributes["aria-label"]}`);
assert(buttons[2].attributes["aria-label"]===undefined,"a button without an engine got one");
await press(rows[0],"6 min");
assert(JSON.stringify(posts()[0].body)===JSON.stringify({item_id:"ocr_value:3:5min",action:"fix",text:"6 min",target}),JSON.stringify(posts()[0].body));
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_markup_keeps_the_card_function_free_of_card_types(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        drawer = html.split("function renderChoiceCard(", 1)[1].split("\n}\n", 1)[0]
        for branch in ("card.type", "card?.type"):
            self.assertNotIn(branch, drawer)
        self.assertIn(".choice-card-engine{", html)
        self.assertIn("@media (max-width:600px){.choice-card-choices button{flex-basis:100%}}", html)


if __name__ == "__main__":
    unittest.main()
