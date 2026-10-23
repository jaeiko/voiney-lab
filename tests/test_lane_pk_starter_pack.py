"""Lane PK: the pilot starter pack tool (scripts/pilot_starter_pack.py).

The tool puts a list of public protocols into one data folder's catalog,
analyses them with the catalog's own calls, and reports which are ready, which
wait for confirmation cards and which failed. Everything here runs on a fake
analysis model and fake OCR/HTTP sessions in temporary folders: no provider is
called, the repository's own data/runtime is never read or written, and the
shipped list (scripts/pilot_starter_pack.csv) is checked for what it must hold
(a reusable licence, a source and authors for every row).
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_lane_rv_review_pending import MADE_UP, PAGES, gamma_response, write_pages_pdf
from tests.test_protocol_catalog import write_text_pdf
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_starter_pack.py"
SHIPPED_LIST = ROOT / "scripts" / "pilot_starter_pack.csv"
LEDGER_SECRET = "Fictional protocol sentence 4417."


def load_tool():
    spec = importlib.util.spec_from_file_location("pilot_starter_pack_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()


def gamma_pdf(path: Path, number: int) -> str:
    """One more distinct document with the same steps (a lot line on page 3)."""

    pages = [list(page) for page in PAGES]
    pages[2].append(f"Lot {number}")
    write_pages_pdf(path, pages, title=f"Protocol Gamma {number}")
    return extract_protocol_pdf(path).sha256


class ScriptedModel:
    """Answers each document's analysis from a script keyed by its checksum."""

    def __init__(self, scripts: dict[str, list[str]]) -> None:
        self.scripts = {sha: list(answers) for sha, answers in scripts.items()}
        self.calls = 0
        self.reasks = 0

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        from voiney_lab.experiment_protocol_analysis import REQUOTE_SYSTEM_PROMPT

        if system_prompt == REQUOTE_SYSTEM_PROMPT:
            self.reasks += 1
            return json.dumps({"answers": []})
        for sha, answers in self.scripts.items():
            if sha in input_json:
                self.calls += 1
                return answers.pop(0)
        raise AssertionError("a document this script does not know")


def environment(model: ScriptedModel | None = None, *, provider=None) -> tool.Environment:
    made = {"model": 0, "ocr": 0}

    def model_factory(meter):
        made["model"] += 1
        if model is None:
            raise AssertionError("the model must not be built")
        return model

    def ocr_factory(meter):
        made["ocr"] += 1
        return provider

    env = tool.Environment(model_factory, ocr_factory, ("google",))
    env.made = made  # type: ignore[attr-defined]
    return env


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.data = self.root / "catalog"
        self.shas: dict[str, str] = {}

    def tearDown(self) -> None:
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def entry(self, name: str, number: int, **changes) -> tool.PackEntry:
        self.shas[name] = gamma_pdf(self.sources / f"{name}.pdf", number)
        values = dict(
            id=name, file=f"{name}.pdf", title=f"Gamma {name}", field="생명", source="Fictional Journal",
            license="CC BY 4.0", authors="A. Author", source_url=f"https://example.org/{name}")
        values.update(changes)
        return tool.PackEntry(**values)

    def answers(self, name: str, *, made_up: bool = False) -> list[str]:
        return [gamma_response(
            self.shas[name], pages=(1, 2, 3),
            step_two=MADE_UP if made_up else "2. Incubate for 10 min at 37 C.")]

    def meter(self, **changes) -> tool.Meter:
        values = dict(max_usd=4.0, max_clova_pages=100, price=(0.75, 0.075, 3.75),
                      ledger=self.root / "ledger.jsonl")
        values.update(changes)
        return tool.Meter(**values)

    def run_pack(self, entries, env, **changes):
        meter = changes.pop("meter", None) or self.meter()
        rows = tool.run_pack(
            entries, data_dir=self.data, source_dirs=[self.sources], environment=env, meter=meter, **changes)
        return rows, meter

    def catalog_ids(self) -> list[str]:
        store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.data))
        try:
            return sorted(entry.protocol_id for entry in ProtocolCatalog(store).list_entries())
        finally:
            store.close()


class LicenceRuleTests(unittest.TestCase):
    def test_only_reusable_licences_pass(self) -> None:
        for text in ("CC BY", "CC BY 4.0", "CC BY 3.0", "CC BY (protocols.io public protocol)", "CC0 1.0",
                     "Public domain (US federal government work, 17 U.S.C. §105)"):
            self.assertTrue(tool.license_allowed(text), text)
        for text in ("CC BY-NC-ND 4.0", "CC BY-NC 4.0", "CC BY-SA 4.0", "CC BY-ND", "All rights reserved", "",
                     "저작권법 제24조의2(공공저작물 자유이용) 근거, 조건부, 내부 측정용, 재배포 안 함"):
            self.assertFalse(tool.license_allowed(text), text)


class ListTests(unittest.TestCase):
    def write(self, rows: list[list[str]], header=("file", "title", "field", "source", "license")) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_a_row_needs_the_five_columns(self) -> None:
        with self.assertRaises(tool.StarterPackError):
            tool.read_list(self.write([["a.pdf", "A", "생명", "X"]], header=("file", "title", "field", "source")))
        with self.assertRaises(tool.StarterPackError):
            tool.read_list(self.write([["a.pdf", "A", "", "X", "CC BY"]]))

    def test_the_id_is_the_file_stem_and_may_not_repeat(self) -> None:
        (only,) = tool.read_list(self.write([["dir/a.pdf", "A", "생명", "X", "CC BY"]]))
        self.assertEqual(only.id, "a")
        with self.assertRaises(tool.StarterPackError):
            tool.read_list(self.write([["a.pdf", "A", "생명", "X", "CC BY"], ["b/a.pdf", "B", "생명", "X", "CC BY"]]))

    def test_an_empty_list_is_refused(self) -> None:
        with self.assertRaises(tool.StarterPackError):
            tool.read_list(self.write([]))


class ShippedListTests(unittest.TestCase):
    def test_every_row_is_reusable_and_names_its_source_and_authors(self) -> None:
        entries = tool.read_list(SHIPPED_LIST)
        # Decision of 2026-10-20: eight to twelve protocols, both fields.
        self.assertTrue(8 <= len(entries) <= 12, len(entries))
        self.assertEqual({entry.field for entry in entries}, {"생명", "화학·소재"})
        for entry in entries:
            self.assertTrue(tool.license_allowed(entry.license), entry.id)
            self.assertTrue(entry.source and entry.authors and entry.source_url, entry.id)
            self.assertTrue(entry.license_evidence, entry.id)
            self.assertRegex(entry.sha256, r"^[0-9a-f]{64}$", entry.id)
            self.assertIn(entry.field, {"생명", "화학·소재"}, entry.id)

    def test_the_list_carries_no_protocol_file(self) -> None:
        self.assertEqual(sorted(path.name for path in (ROOT / "scripts").glob("*.pdf")), [])


class RunTests(_Case):
    def test_a_document_that_passes_waits_for_nothing_and_one_with_an_item_waits_for_review(self) -> None:
        good, odd = self.entry("good", 1), self.entry("odd", 2)
        model = ScriptedModel({self.shas["good"]: self.answers("good"), self.shas["odd"]: self.answers("odd", made_up=True)})
        rows, _ = self.run_pack([good, odd], environment(model))
        by_id = {row.id: row for row in rows}
        self.assertEqual(by_id["good"].state, tool.STATE_READY)
        self.assertEqual(by_id["good"].steps, 6)
        self.assertEqual(by_id["odd"].state, tool.STATE_REVIEW)
        # Lane MISS: the made-up step, and page 1's real step 2 no quote covers.
        self.assertEqual(by_id["odd"].cards, 2)
        self.assertIn("확인 필요 2건", by_id["odd"].reasons)
        self.assertEqual(by_id["odd"].label, "확인할 것 2장")
        # Nothing was confirmed for the person: the waiting analysis never runs.
        store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.data))
        try:
            entry = ProtocolCatalog(store).get_entry(by_id["odd"].protocol_id)
        finally:
            store.close()
        self.assertFalse(entry.available_for_execution)
        self.assertEqual(entry.review_pending["needs_review_count"], 2)

    def test_a_failed_analysis_is_reported_with_its_code_and_the_catalog_line(self) -> None:
        bad = self.entry("bad", 3)
        broken = gamma_response(self.shas["bad"]).replace('"steps":', '"stepz":', 1)
        rows, _ = self.run_pack([bad], environment(ScriptedModel({self.shas["bad"]: [broken, broken]})))
        (row,) = rows
        self.assertEqual(row.state, tool.STATE_FAILED)
        self.assertEqual(row.failure_code, "protocol_analysis_invalid_response")
        self.assertTrue(row.reasons)
        self.assertIn("실패", row.label)

    def test_a_second_run_uploads_and_analyses_nothing_again(self) -> None:
        good, odd = self.entry("good", 1), self.entry("odd", 2)
        model = ScriptedModel({self.shas["good"]: self.answers("good"), self.shas["odd"]: self.answers("odd", made_up=True)})
        first, _ = self.run_pack([good, odd], environment(model))
        ids = self.catalog_ids()
        self.assertEqual(len(ids), 2)
        calls = model.calls
        again_env = environment(None)  # building the model at all would fail the test
        second, meter = self.run_pack([good, odd], again_env)
        self.assertEqual(model.calls, calls)
        self.assertEqual(again_env.made, {"model": 0, "ocr": 0})
        self.assertEqual(self.catalog_ids(), ids)
        self.assertEqual([row.state for row in second], [row.state for row in first])
        self.assertTrue(all(row.deduplicated and not row.analysed_now for row in second))
        self.assertEqual(meter.run_summary()["usd_total"], 0.0)

    def test_the_same_file_under_two_ids_is_registered_and_analysed_once(self) -> None:
        one = self.entry("one", 1)
        twin = tool.PackEntry(**{**one.__dict__, "id": "twin", "title": "Gamma twin"})
        model = ScriptedModel({self.shas["one"]: self.answers("one")})
        rows, _ = self.run_pack([one, twin], environment(model))
        self.assertEqual([row.state for row in rows], [tool.STATE_READY, tool.STATE_SKIPPED])
        self.assertIn("one", rows[1].detail)
        self.assertEqual(len(self.catalog_ids()), 1)
        self.assertEqual(model.calls, 1)

    def test_a_failed_one_is_left_alone_until_asked_for_again(self) -> None:
        bad = self.entry("bad", 3)
        broken = gamma_response(self.shas["bad"]).replace('"steps":', '"stepz":', 1)
        self.run_pack([bad], environment(ScriptedModel({self.shas["bad"]: [broken, broken]})))
        untouched = ScriptedModel({self.shas["bad"]: self.answers("bad")})
        rows, _ = self.run_pack([bad], environment(untouched))
        self.assertEqual(untouched.calls, 0)
        self.assertEqual(rows[0].state, tool.STATE_FAILED)
        self.assertIn("--retry-failed", rows[0].detail)
        retried = ScriptedModel({self.shas["bad"]: self.answers("bad")})
        rows, _ = self.run_pack([bad], environment(retried), retry_failed=True)
        self.assertEqual(retried.calls, 1)
        self.assertEqual(rows[0].state, tool.STATE_READY)

    def test_the_protocols_belong_to_the_labs_organisation_not_to_another(self) -> None:
        good = self.entry("good", 1)
        rows, _ = self.run_pack([good], environment(ScriptedModel({self.shas["good"]: self.answers("good")})))
        workspace = initialize_workspace_store(WorkspaceSettings(True, self.data / "workspace"))
        try:
            lab = tool._service_principal(tool.DEFAULT_ORGANIZATION)
            other = tool._service_principal("another-lab")
            workspace.bootstrap_principal(other)
            self.assertEqual(workspace.resource_ids(lab, "protocol_catalog"), {rows[0].protocol_id})
            self.assertEqual(workspace.resource_ids(other, "protocol_catalog"), frozenset())
        finally:
            workspace.close()

    def test_a_licence_that_is_not_reusable_is_refused_before_anything_is_registered(self) -> None:
        sealed = self.entry("sealed", 1, license="CC BY-NC-ND 4.0")
        rows, meter = self.run_pack([sealed], environment(None))
        self.assertEqual(rows[0].state, tool.STATE_SKIPPED)
        self.assertIn("라이선스", rows[0].detail)
        self.assertEqual(self.catalog_ids(), [])
        self.assertEqual(meter.rows, [])

    def test_a_missing_file_and_a_changed_file_are_skipped_with_the_reason(self) -> None:
        gone = self.entry("gone", 1)
        (self.sources / "gone.pdf").unlink()
        changed = self.entry("changed", 2, sha256="0" * 64)
        rows, _ = self.run_pack([gone, changed], environment(None))
        self.assertIn("원문 파일이 없음", rows[0].detail)
        self.assertIn("sha256", rows[1].detail)
        self.assertEqual(self.catalog_ids(), [])

    def test_a_scanned_document_without_an_ocr_engine_fails_by_name_and_is_not_analysed(self) -> None:
        write_text_pdf(self.sources / "scan.pdf", None, title="Scan")
        scan = tool.PackEntry(id="scan", file="scan.pdf", title="A scan", field="생명", source="X",
                              license="CC BY", authors="B")
        model = ScriptedModel({})
        rows, _ = self.run_pack([scan], environment(model, provider=None))
        self.assertEqual((rows[0].state, rows[0].failure_code), (tool.STATE_FAILED, "ocr_not_configured"))
        self.assertEqual(model.calls, 0)

    def test_a_cap_stops_the_pack_and_the_rest_are_not_started(self) -> None:
        first, second = self.entry("first", 1), self.entry("second", 2)
        model = ScriptedModel({self.shas["first"]: self.answers("first"), self.shas["second"]: self.answers("second")})
        client_calls = []

        def model_factory(meter):
            fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                create=lambda **request: client_calls.append(request) or SimpleNamespace(usage=SimpleNamespace(
                    prompt_tokens=100_000, completion_tokens=0, reasoning_tokens=0,
                    prompt_tokens_details=None)))))
            metered = tool.MeteredClient(fake, meter, model="gemini-3.8-flash")

            class Through:
                def analyze(self, **kwargs):
                    metered.chat.completions.create(messages=[{"role": "user", "content": kwargs["input_json"]}])
                    return model.analyze(**kwargs)

            return Through()

        env = tool.Environment(model_factory, lambda meter: None, ())
        rows, meter = self.run_pack([first, second], env, meter=self.meter(max_usd=0.05))
        self.assertEqual(rows[0].state, tool.STATE_READY)
        self.assertEqual(rows[1].state, tool.STATE_SKIPPED)
        self.assertIn("상한", rows[1].detail)
        self.assertIn("USD 0.05", meter.stopped)
        self.assertEqual(len(client_calls), 1)


class MeterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.temp.name) / "pk" / "ledger.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_a_call_is_priced_from_its_usage_and_written_without_its_text(self) -> None:
        meter = tool.Meter(max_usd=4, max_clova_pages=100, price=(0.75, 0.075, 3.75), ledger=self.ledger)
        usage = SimpleNamespace(prompt_tokens=10_000, completion_tokens=2_000, reasoning_tokens=500,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=2_000))
        seen = []

        def create(**request):
            seen.append(request)
            return SimpleNamespace(usage=usage, choices=[])

        client = tool.MeteredClient(
            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
            meter, model="gemini-3.8-flash")
        meter.current_doc = "doc-1"
        client.chat.completions.create(model="m", messages=[{"role": "user", "content": LEDGER_SECRET}])
        (row,) = [json.loads(line) for line in self.ledger.read_text().splitlines()]
        # (8000 * 0.75 + 2000 * 0.075 + 2000 * 3.75) / 1e6
        self.assertAlmostEqual(row["usd"], 0.01365, places=5)
        self.assertEqual((row["kind"], row["doc"], row["status"], row["prompt_tokens"]), ("analysis", "doc-1", "ok", 10_000))
        self.assertNotIn(LEDGER_SECRET, self.ledger.read_text())
        self.assertEqual(seen[0]["model"], "m")

    def test_an_error_is_written_and_raised_and_a_403_stops_every_later_call(self) -> None:
        meter = tool.Meter(max_usd=4, max_clova_pages=100, price=(1, 1, 1), ledger=self.ledger)

        def create(**request):
            raise RuntimeError("google authentication (HTTP 403)")

        client = tool.MeteredClient(
            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), meter, model="gemini-3.8-flash")
        with self.assertRaises(RuntimeError):
            client.chat.completions.create(messages=[])
        self.assertIn("403", meter.stopped)
        with self.assertRaises(tool.BudgetExceeded):
            client.chat.completions.create(messages=[])
        self.assertIn("error:RuntimeError", self.ledger.read_text())

    def test_the_caps_count_what_the_ledger_already_holds(self) -> None:
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text(
            json.dumps({"kind": "analysis", "provider": "google", "usd": 3.99}) + "\n"
            + json.dumps({"kind": "ocr_clova", "provider": "clova", "pages": 99, "usd": 0.0}) + "\n")
        meter = tool.Meter(max_usd=4, max_clova_pages=100, price=(1, 1, 1), ledger=self.ledger)
        self.assertAlmostEqual(meter.usd, 3.99)
        self.assertEqual(meter.clova_pages, 99)
        with self.assertRaises(tool.BudgetExceeded):
            meter.reserve("analysis", usd=0.05)

    def test_ocr_pages_are_counted_priced_and_capped_by_engine(self) -> None:
        meter = tool.Meter(max_usd=4, max_clova_pages=2, price=(1, 1, 1), ledger=self.ledger)
        posted = []
        inner = SimpleNamespace(post=lambda url, **kw: posted.append(url) or SimpleNamespace(status_code=200))
        session = tool.MeteredSession(meter, session=inner)
        session.post("https://vision.googleapis.com/v1/images:annotate", json={})
        session.post("https://example.apigw.ntruss.com/ocr", json={})
        session.post("https://example.apigw.ntruss.com/ocr", json={})
        with self.assertRaises(tool._OcrStopped):
            session.post("https://example.apigw.ntruss.com/ocr", json={})
        summary = meter.run_summary()
        self.assertEqual((summary["vision_pages"], summary["clova_pages"]), (1, 2))
        self.assertAlmostEqual(summary["vision_usd"], tool.VISION_USD_PER_PAGE)
        self.assertEqual(len(posted), 3)
        self.assertIn("CLOVA", meter.stopped)

    def test_a_price_for_an_unknown_model_must_be_given(self) -> None:
        with self.assertRaises(tool.StarterPackError):
            tool.read_price(None, "some-new-model")
        self.assertEqual(tool.read_price("1,0.1,5", "some-new-model"), (1.0, 0.1, 5.0))
        with self.assertRaises(tool.StarterPackError):
            tool.read_price("1,2", "x")


class GuideTests(unittest.TestCase):
    """docs/PILOT_GUIDE.md section 6 names what the tool takes and writes."""

    def setUp(self) -> None:
        text = (ROOT / "docs" / "PILOT_GUIDE.md").read_text(encoding="utf-8")
        self.section = text.split("## 6. 스타터 팩", 1)[1].split("## 7.", 1)[0]

    def test_every_option_the_guide_shows_exists(self) -> None:
        parser = tool.build_parser()
        known = {option for action in parser._actions for option in action.option_strings}
        shown = set(re.findall(r"--[a-z][a-z-]+", self.section))
        self.assertTrue(shown)
        self.assertEqual(shown - known, set())

    def test_the_guide_names_the_files_the_tool_writes_and_the_licence_rule(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        names = [path.name for path in tool.write_report(folder, [], tool.Meter(max_usd=1, max_clova_pages=1))]
        for name in names:
            self.assertIn(name.removesuffix(".md").removesuffix(".json"), self.section)
        for word in ("CC BY", "service-starter-pack", "lab-pilot", "--allow-runtime-dir", "--retry-failed"):
            self.assertIn(word, self.section)


class OutputTests(_Case):
    def setUp(self) -> None:
        super().setUp()
        good, odd, bad = self.entry("good", 1), self.entry("odd", 2), self.entry("bad", 3)
        broken = gamma_response(self.shas["bad"]).replace('"steps":', '"stepz":', 1)
        sealed = self.entry("sealed", 4, license="CC BY-NC 4.0")
        model = ScriptedModel({
            self.shas["good"]: self.answers("good"), self.shas["odd"]: self.answers("odd", made_up=True),
            self.shas["bad"]: [broken, broken]})
        self.rows, self.meter = self.run_pack([good, odd, bad, sealed], environment(model))

    def test_the_table_counts_ready_cards_failures_and_skips(self) -> None:
        counts = tool.summary_counts(self.rows)
        # Lane MISS: odd's made-up step and page 1's real step 2 are 2 cards.
        self.assertEqual((counts["ready"], counts["review"], counts["failed"], counts["skipped"], counts["cards"]),
                         (1, 1, 1, 1, 2))
        text = tool.report_text(self.rows, self.meter)
        for expected in ("바로 쓸 수 있음", "확인할 것 2장", "실패 · protocol_analysis_invalid_response",
                         "건너뜀 · 라이선스가 재사용 가능한 것이 아님"):
            self.assertIn(expected, text)
        self.assertIn("바로 쓸 수 있음 1 · 확인할 것 있음 1(카드 2장) · 실패 1 · 건너뜀 1", text)

    def test_the_human_list_names_what_waits_and_what_failed_and_confirms_nothing(self) -> None:
        text = tool.human_checks(self.rows)
        self.assertIn("Gamma odd | 2 |", text)  # lane MISS: +1 card
        self.assertIn("Gamma bad", text)
        self.assertNotIn("Gamma good", text)
        self.assertIn("어떤 카드도 대신 확인하지 않았다", text)

    def test_the_sources_table_keeps_source_authors_and_licence(self) -> None:
        text = tool.sources_table(self.rows)
        self.assertIn("| Gamma good | 생명 | Fictional Journal | A. Author | CC BY 4.0 |", text)

    def test_the_report_folder_holds_four_files_and_no_protocol_text(self) -> None:
        folder = self.root / "report"
        paths = tool.write_report(folder, self.rows, self.meter)
        self.assertEqual(sorted(path.name for path in paths), [
            "starter_pack_human_checks.md", "starter_pack_result.json", "starter_pack_result.md",
            "starter_pack_sources.md"])
        data = json.loads((folder / "starter_pack_result.json").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["cards"], 2)  # lane MISS: +1 card
        self.assertNotIn("Add 2 mL of buffer A", "".join(path.read_text(encoding="utf-8") for path in paths))


class CommandTests(_Case):
    def write_list(self, entries) -> Path:
        path = self.root / "list.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(tool.PackEntry.__dataclass_fields__))
            writer.writeheader()
            for entry in entries:
                writer.writerow(entry.__dict__)
        return path

    def main(self, *argv, env=None, model_env=None):
        out = io.StringIO()
        with patch("sys.stderr", io.StringIO()) as err:
            code = tool.main(list(argv), environment=env or environment(None), env=model_env or {}, out=out)
        return code, out.getvalue(), err.getvalue()

    def test_a_dry_run_estimates_and_touches_nothing(self) -> None:
        good = self.entry("good", 1)
        listing = self.write_list([good])
        env = environment(None)
        code, out, _ = self.main(
            "--data-dir", str(self.data), "--list", str(listing), "--source-dir", str(self.sources), "--dry-run",
            env=env, model_env={"VOINEY_LAB_ANALYSIS_MODEL": "gemini-3.8-flash"})
        self.assertEqual(code, 0)
        self.assertIn("예상 비용 USD", out)
        self.assertIn("Gamma good", out)
        self.assertFalse(self.data.exists())
        self.assertEqual(env.made, {"model": 0, "ocr": 0})

    def test_a_dry_run_marks_what_the_catalog_already_holds(self) -> None:
        good = self.entry("good", 1)
        self.run_pack([good], environment(ScriptedModel({self.shas["good"]: self.answers("good")})))
        listing = self.write_list([good])
        _, out, _ = self.main(
            "--data-dir", str(self.data), "--list", str(listing), "--source-dir", str(self.sources), "--dry-run",
            model_env={"VOINEY_LAB_ANALYSIS_MODEL": "gemini-3.8-flash"})
        self.assertIn("카탈로그에 이미 있음", out)

    def test_a_run_prints_the_table_and_the_list_and_writes_the_report(self) -> None:
        good, odd = self.entry("good", 1), self.entry("odd", 2)
        model = ScriptedModel({self.shas["good"]: self.answers("good"), self.shas["odd"]: self.answers("odd", made_up=True)})
        report = self.root / "report"
        code, out, err = self.main(
            "--data-dir", str(self.data), "--list", str(self.write_list([good, odd])), "--source-dir", str(self.sources),
            "--report-dir", str(report), env=environment(model), model_env={"VOINEY_LAB_ANALYSIS_MODEL": "gemini-3.8-flash"})
        self.assertEqual(code, 0)
        self.assertIn("확인할 것 2장", out)  # lane MISS: +1 card
        self.assertIn("사람이 확인할 목록", out)
        self.assertIn("good: 바로 쓸 수 있음", err)
        self.assertTrue((report / "starter_pack_result.md").is_file())

    def test_only_runs_the_ids_named_and_an_unknown_id_is_refused(self) -> None:
        good, odd = self.entry("good", 1), self.entry("odd", 2)
        model = ScriptedModel({self.shas["good"]: self.answers("good")})
        code, out, _ = self.main(
            "--data-dir", str(self.data), "--list", str(self.write_list([good, odd])), "--source-dir", str(self.sources),
            "--only", "good", env=environment(model), model_env={"VOINEY_LAB_ANALYSIS_MODEL": "gemini-3.8-flash"})
        self.assertEqual(code, 0)
        self.assertNotIn("Gamma odd", out)
        code, _, err = self.main(
            "--data-dir", str(self.data), "--list", str(self.write_list([good])), "--source-dir", str(self.sources),
            "--only", "nope", model_env={"VOINEY_LAB_ANALYSIS_MODEL": "gemini-3.8-flash"})
        self.assertEqual(code, 2)
        self.assertIn("nope", err)

    def test_the_repositorys_runtime_folder_is_refused_unless_allowed(self) -> None:
        good = self.entry("good", 1)
        listing = self.write_list([good])
        runtime = self.root / "repo" / "data" / "runtime" / "pilot"
        with patch.object(tool, "RUNTIME_DIR", self.root / "repo" / "data" / "runtime"):
            code, _, err = self.main(
                "--data-dir", str(runtime), "--list", str(listing), "--source-dir", str(self.sources),
                model_env={"VOINEY_LAB_ANALYSIS_MODEL": "gemini-3.8-flash"})
            self.assertEqual(code, 2)
            self.assertIn("--allow-runtime-dir", err)
            self.assertFalse(runtime.exists())
            model = ScriptedModel({self.shas["good"]: self.answers("good")})
            code, _, _ = self.main(
                "--data-dir", str(runtime), "--list", str(listing), "--source-dir", str(self.sources),
                "--allow-runtime-dir", env=environment(model),
                model_env={"VOINEY_LAB_ANALYSIS_MODEL": "gemini-3.8-flash"})
            self.assertEqual(code, 0)
            self.assertTrue(runtime.exists())

    def test_a_model_without_a_price_is_refused_before_any_work(self) -> None:
        good = self.entry("good", 1)
        code, _, err = self.main(
            "--data-dir", str(self.data), "--list", str(self.write_list([good])), "--source-dir", str(self.sources),
            model_env={"VOINEY_LAB_ANALYSIS_MODEL": "some-new-model"})
        self.assertEqual(code, 2)
        self.assertIn("--price", err)
        self.assertFalse(self.data.exists())


class EnvironmentTests(unittest.TestCase):
    def test_an_unset_analysis_provider_and_an_xai_provider_are_refused_by_name(self) -> None:
        meter = tool.Meter(max_usd=1, max_clova_pages=1)
        for env, word in (({}, "VOINEY_LAB_ANALYSIS_PROVIDER"), ({"VOINEY_LAB_ANALYSIS_PROVIDER": "xai"}, "xai")):
            factory = tool.default_environment(env).model_factory
            with self.assertRaises(tool.StarterPackError) as caught:
                factory(meter)
            self.assertIn(word, str(caught.exception))

    def test_the_ocr_engines_are_the_shells_and_named_for_the_dry_run(self) -> None:
        names = tool.default_environment({"VOINEY_LAB_OCR_PROVIDERS": "clova, Google"}).engines
        self.assertEqual(names, ("clova", "google"))


if __name__ == "__main__":
    unittest.main()
