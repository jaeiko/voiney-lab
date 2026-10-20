"""Lane FX5, decision 3 (2026-10-20): the admin metrics screen opens for an admin account without the token.

``static/admin-metrics.html`` used to refuse to send a request while the
token box was empty, so an administrator account that the server lets in
without a token (lane FX4) still met "관리자 토큰을 넣으세요". Now the page
asks the server on opening, and again at "불러오기", with or without a token
(the session cookie goes with the request); only a 401 or 403 makes it ask
for the token -- the box is shown then, not before. Numbers only, as before.
The page runs here in node on a fake DOM; no server and no network.
"""

from __future__ import annotations

import re
import subprocess
import unittest

from tests.test_lane_log_export import PAGE, PRELUDE, ROOT


def run_page(before: str, body: str) -> subprocess.CompletedProcess:
    """The page's script with ``before`` run first (a fetch for the opening request) and ``body`` after it."""

    html = PAGE.read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = (
        PRELUDE
        + "globalThis.URLSearchParams=URLSearchParams;\n"
        # The fake DOM does not read the markup, where the token box starts hidden.
        + 'node("metrics-token-label").hidden=true;\n'
        + before + "\n"
        + script
        + "\n(async()=>{\n" + body
        + "\n})().catch(error=>{console.error(error);process.exit(1)});\n"
    )
    return subprocess.run(["node", "-"], cwd=ROOT, text=True, input=harness, capture_output=True)


DAYS = r"""({ok:true,status:200,json:async()=>({
 columns:["day","experiments"],days:[{day:"2026-10-20",experiments:3}],totals:{experiments:3}})})"""


class AdminPageWithoutATokenTests(unittest.TestCase):
    def test_opening_the_page_asks_the_server_with_no_token_and_draws_the_days(self) -> None:
        result = run_page(
            f"const calls=[];globalThis.fetch=async(url,options)=>{{calls.push({{url,options}});return {DAYS}}};",
            r"""
await new Promise(resolve=>setTimeout(resolve,0));
assert(calls.length===1,"one request on opening: "+calls.length);
assert(calls[0].url.startsWith("/api/admin/pilot-metrics?from="),calls[0].url);
assert(!("X-Voice-Workflow-Admin-Token" in (calls[0].options.headers||{})),"no token header");
assert(calls[0].options.credentials==="same-origin","the session cookie goes with it");
assert(visibleText(node("metrics-body")).includes("2026-10-20"),"the days are drawn");
assert(node("metrics-token-label").hidden===true,"an admin account is never asked for the token");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_button_sends_the_request_with_an_empty_token_box(self) -> None:
        result = run_page(
            f"const calls=[];globalThis.fetch=async(url,options)=>{{calls.push({{url,options}});return {DAYS}}};",
            r"""
await new Promise(resolve=>setTimeout(resolve,0));
calls.length=0;
node("metrics-token").value="";
await loadMetrics();
assert(calls.length===1,"a request even with the token box empty");
assert(!("X-Voice-Workflow-Admin-Token" in (calls[0].options.headers||{})),"and no token header");
assert(!visibleText(node("metrics-status")).includes("토큰을 넣으세요."),visibleText(node("metrics-status")));
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_typed_token_still_goes_as_the_header(self) -> None:
        result = run_page(
            f"const calls=[];globalThis.fetch=async(url,options)=>{{calls.push({{url,options}});return {DAYS}}};",
            r"""
await new Promise(resolve=>setTimeout(resolve,0));
calls.length=0;
node("metrics-token").value="  secret  ";
await loadMetrics();
assert(calls[0].options.headers["X-Voice-Workflow-Admin-Token"]==="secret","trimmed token header");
assert(calls[0].options.credentials==="same-origin","cookie too");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class AdminPageAsksForTheTokenTests(unittest.TestCase):
    def _refused(self, status: int, token: str) -> subprocess.CompletedProcess:
        return run_page(
            f"globalThis.fetch=async()=>({{ok:false,status:{status},json:async()=>({{}})}});",
            f"""
await new Promise(resolve=>setTimeout(resolve,0));
node("metrics-token").value={token!r};
await loadMetrics();
const status=node("metrics-status");
const label=node("metrics-token-label");
process.stdout.write(JSON.stringify({{text:status.textContent,kind:status.dataset.kind,askedForToken:label.hidden===false}}));
""")

    def _answer(self, status: int, token: str = ""):
        import json

        result = self._refused(status, token)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_403_without_a_token_asks_for_the_token(self) -> None:
        answer = self._answer(403)
        self.assertEqual(answer["kind"], "error")
        self.assertTrue(answer["askedForToken"])
        self.assertIn("관리자 토큰", answer["text"])
        self.assertIn("관리자 계정", answer["text"])

    def test_a_401_asks_to_sign_in_as_an_administrator_and_shows_the_token_box(self) -> None:
        answer = self._answer(401)
        self.assertEqual(answer["kind"], "error")
        self.assertTrue(answer["askedForToken"])
        self.assertIn("로그인", answer["text"])

    def test_a_403_with_a_typed_token_says_the_token_does_not_fit(self) -> None:
        answer = self._answer(403, "wrong")
        self.assertEqual(answer["text"], "관리자 토큰이 맞지 않습니다.")
        self.assertTrue(answer["askedForToken"])

    def test_a_closed_route_names_both_ways_in_and_does_not_ask_for_a_token(self) -> None:
        answer = self._answer(503)
        self.assertIn("VOINEY_LAB_ADMIN_TOKEN", answer["text"])
        self.assertIn("관리자 계정", answer["text"])
        self.assertFalse(answer["askedForToken"], "a token cannot open a route that has none")


class AdminPageMarkupTests(unittest.TestCase):
    html = PAGE.read_text(encoding="utf-8")

    def test_the_token_box_is_not_required_and_starts_hidden(self) -> None:
        box = re.search(r'<input id="metrics-token"[^>]*>', self.html)
        self.assertIsNotNone(box)
        self.assertNotIn("required", box.group(0))
        label = re.search(r'<label id="metrics-token-label"[^>]*>', self.html)
        self.assertIsNotNone(label)
        self.assertIn("hidden", label.group(0))
        self.assertIn("[hidden]", self.html, "a hidden label stays hidden under the flex layout")

    def test_the_token_stays_in_memory_only(self) -> None:
        for forbidden in ("localStorage", "sessionStorage", "document.cookie", "innerHTML"):
            self.assertNotIn(forbidden, self.html)


if __name__ == "__main__":
    unittest.main()
