"""Lane KNOW2: rebuilding the public bundle from its raw cache (2026-10-26).

The clean-up of the bundle (person's decisions 3 and 4: OpenStax chapters,
broken pieces, the ILO cards) changes how the raw material is cut, not the
material. ``collect --offline`` rebuilds the bundle from the raw cache with no
request at all, so the change is measured on the same material. The raw
cache also has to survive a move: lane KNOW's cache was written in its
worktree and copied to the main checkout, and every item still named the old
absolute path, so nothing cached could be read there.

Fake-backed: the transport is lane KNOW's dictionary of canned replies.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_lane_know_pipeline import FakeTransport, _collect_fixture, _source, pk


def _records(runtime: Path) -> list[dict]:
    return [json.loads(line) for line in (runtime / "bundle" / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]


class OfflineTests(unittest.TestCase):
    def _online(self, root: Path):
        sources, transport = _collect_fixture(root)
        http = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
        pk.collect(sources, runtime_dir=root / "runtime", http=http, log=lambda line: None)
        return sources, transport

    def test_an_offline_run_sends_nothing_and_rebuilds_the_same_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, transport = self._online(root)
            before = _records(root / "runtime")
            transport.calls.clear()
            manifest = pk.collect(sources, runtime_dir=root / "runtime", http=pk.OfflineHttp(), offline=True,
                                  log=lambda line: None)
            self.assertEqual(transport.calls, [])
            self.assertEqual(manifest["totals"]["requests"], 0)
            self.assertEqual([r["id"] for r in _records(root / "runtime")], [r["id"] for r in before])

    def test_a_moved_raw_cache_is_still_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, _transport = self._online(root)
            before = _records(root / "runtime")
            moved = root / "elsewhere"
            shutil.move(str(root / "runtime"), str(moved))
            manifest = pk.collect(sources, runtime_dir=moved, http=pk.OfflineHttp(), offline=True,
                                  log=lambda line: None)
            self.assertEqual(manifest["totals"]["failures"], 0)
            self.assertEqual([r["id"] for r in _records(moved)], [r["id"] for r in before])

    def test_the_command_line_takes_offline(self) -> None:
        args = pk.build_parser().parse_args(["collect", "--offline"])
        self.assertTrue(args.offline)


FOOTER = "This OpenStax book is available for free at http://cnx.org/content/col11760/1.9"
LAB = "Concentrated acids are diluted by adding the acid slowly to water while stirring, never the reverse. "


class TextbookTests(unittest.TestCase):
    """Person's decision 2026-10-26, 3: the OpenStax chapters outside the wet lab,
    a chapter's end matter (key terms, summary, exercises) and everything from
    the first appendix (answer key, index) are left out; page furniture goes."""

    def _pieces(self, pages: list[str]) -> list:
        source = _source(id="openstax-chemistry-1e", kind="pdf", options={
            "page_url": "https://openstax.org/details/books/chemistry", "skip_chapters": [2],
            "end_matter": ["Key Terms", "Exercises"], "stop_at": r"^Appendix [A-Z]\b",
            "page_furniture": [r"^This OpenStax book is available for free at .*$",
                               r"^\d+ Chapter \d+ \| .*$", r"^Chapter \d+ \| .*\d+$"]})
        return list(pk.pdf_pieces(source, pages, url="https://x/book.pdf", fetched_at="2026-10-26"))

    def test_chapters_end_matter_appendices_and_furniture_are_left_out(self) -> None:
        pages = [
            f"Chapter 1 | Essential Ideas 3\n{LAB * 3}",
            f"4 Chapter 1 | Essential Ideas\n{LAB * 2}\nKey Terms\nacid substance that yields hydronium ions\n{FOOTER}",
            f"Exercises\n1. What is an acid? 2. What is a base? {LAB}\nChapter 1 | Essential Ideas 5",
            f"6 Chapter 2 | Plant Biology\n{'Plants make seeds in flowers that are pollinated by insects. ' * 4}",
            f"Chapter 3 | Solutions 7\n{'A solution is a homogeneous mixture of a solute in a solvent. ' * 4}",
            f"Appendix A The Periodic Table\n{'Hydrogen 1.008 Helium 4.003 ' * 10}",
            f"Index\n{'acid, 12 base, 14 ' * 10}",
        ]
        pieces = self._pieces(pages)
        self.assertEqual([piece.locator for piece in pieces], ["p. 1", "p. 2", "p. 5"])
        self.assertNotIn("Key Terms", pieces[1].text)
        self.assertNotIn("hydronium", pieces[1].text)
        for piece in pieces:
            self.assertNotIn("OpenStax book is available", piece.text)
            self.assertNotIn("Chapter 1 |", piece.text)


class BrokenPieceTests(unittest.TestCase):
    """A piece that reads as no running text -- a table run together, a book
    index, a reference list, exercises, formula markup -- is left out, with
    the reason counted in the manifest."""

    def test_the_kinds_of_broken_piece(self) -> None:
        cases = {
            "table": "Substance ΔHf° (kJ mol–1) W(s) 0 0 32.6 W(g) 849.4 807.1 174.0 WO3(s) –842.9 –764.0 75.9 "
                     "Zn(s) 0 0 41.6 Zn(g) 130.73 95.14 160.98 Zn2+(aq) –153.9 –147.1 –112.1",
            "index": "binary compounds, 105 binding energy per nucleon, 1153, 1197 biofuel, 257 Bismuth, 974 "
                     "bismuth, 1035 blackbody, 286, 328 Bohr, 291, 292 Bohr’s model, 293 boiling point, 512",
            "references": "Pike RM. Laboratory-associated infections. Annu Rev Microbiol. 1979;33:41–66. "
                          "Sulkin SE. Laboratory infections. Bacteriol Rev. 1951;15(4):203–13. "
                          "Collins CH. Laboratory acquired infections. Med Lab Sci. 1980;37:291–8.",
            "exercises": "17. Determine the oxidation states of the elements in H3PO4. 18. Determine the oxidation "
                         "states in Al(OH)3. 19. Which of the following is a peroxide? 20. Write the formula.",
            "markup": "{\\displaystyle {\\ce {[H+]}}} is obtained from the Henderson-Hasselbalch equation and the "
                      "titration mixture is considered as buffer.",
        }
        for reason, text in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(pk.broken_reason(text), reason)
        self.assertIsNone(pk.broken_reason(LAB * 3))
        self.assertIsNone(pk.broken_reason("Store acetonitrile below 25 °C, away from oxidizers; flash point 2 °C."))

    def test_a_run_leaves_broken_pieces_out_and_counts_them(self) -> None:
        table = "W(s) 0 0 32.6 W(g) 849.4 807.1 174.0 WO3(s) –842.9 –764.0 75.9 Zn(s) 0 0 41.6 Zn(g) 130.73 95.14 160.98"
        page = (f"<html><body><h2>Hoods</h2><p>{LAB * 3}</p><h2>Data</h2><p>{table} {table}</p>"
                "</body></html>").encode()
        transport = FakeTransport({"https://docs.example/robots.txt": (404, {}, b""),
                                   "https://docs.example/guide": (200, {}, page)})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            toml = root / "sources.toml"
            toml.write_text(
                'version = 1\nuser_agent = "Test/0.1 (https://example.org/contact)"\n'
                '[[source]]\nid = "page"\nname = "Demo"\nkind = "html"\ngrade = "A"\ndomains = ["safety"]\n'
                'language = "en"\nlicense = "Public domain"\nlicense_url = "https://example.org/l"\n'
                'license_checked = "2026-10-26"\nterms = "one page"\nurl = "https://docs.example/guide"\n',
                encoding="utf-8")
            sources = pk.load_sources(toml)
            http = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
            manifest = pk.collect(sources, runtime_dir=root / "runtime", http=http, log=lambda line: None)
            locators = [record["locator"] for record in _records(root / "runtime")]
        self.assertEqual(locators, ["Hoods"])
        self.assertEqual(manifest["sources"]["page"]["broken_dropped"], {"table": 1})

    def test_wiki_formula_markup_and_letter_lines_are_taken_out_of_a_section(self) -> None:
        cleaned = pk.clean_wiki_text("The pH is {\\displaystyle {\\ce {[H+]}}} at the start.\np\nq\nIt rises.")
        self.assertEqual(cleaned, "The pH is at the start.\nIt rises.")


NESTED = """<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><journal-meta><journal-title-group>
<journal-title>Demo Journal</journal-title></journal-title-group></journal-meta><article-meta>
<article-id pub-id-type="pmcid">PMC1</article-id><title-group><article-title>A method</article-title></title-group>
<pub-date><year>2024</year></pub-date><permissions><license license-type="open-access"
xlink:href="http://creativecommons.org/licenses/by/4.0/"><license-p>terms</license-p></license></permissions>
<abstract><p>{text}</p></abstract></article-meta></front><body>
<sec><title>Introduction</title><p>{text}</p></sec>
<sec><title>Materials and methods</title><sec><title>Cell culture</title><p>{text}</p></sec></sec>
<sec><title>Results</title><p>{text}</p><sec><title>Materials compared</title><p>{text}</p></sec></sec>
<sec><title>Troubleshooting</title><p>{text}</p></sec>
<sec><title>Notes</title><p>{text}</p></sec></body></article>"""


class MethodsOnlyTests(unittest.TestCase):
    """Person's decision 2026-10-26, 3 (lane KAUDIT's P2): of a Europe PMC
    article only the methods, troubleshooting and notes, not the results
    (two result paragraphs attached to a help were off the point)."""

    def test_only_the_listed_sections_with_their_parents_names(self) -> None:
        text = "Cells were passaged with TrypLE for five minutes at 37 °C before counting. " * 3
        transport = FakeTransport({
            "https://www.ebi.ac.uk/robots.txt": (200, {}, b"User-agent: *\nDisallow: /private\n"),
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?*": (
                200, {}, json.dumps({"resultList": {"result": [{"pmcid": "PMC1", "license": "cc by"}]}}).encode()),
            "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML": (
                200, {}, NESTED.format(text=text).encode()),
        })
        source = _source(id="epmc", kind="europepmc", options={
            "base_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/", "queries": ["passaging"],
            "max_per_query": 1, "pmcids": [], "sections": ["method", "troubleshoot", "note"]})
        with tempfile.TemporaryDirectory() as temporary:
            ctx = pk.RunContext(sources=pk.SourceList("Test/0.1 (x)", (source,), (), Path(temporary)),
                                http=pk.Http("Test/0.1 (x)", transport=transport, sleep=lambda s: None),
                                runtime_dir=Path(temporary), report=pk.RunReport())
            pieces = list(pk.handle_europepmc(source, ctx))
        self.assertEqual([piece.locator for piece in pieces], [
            "Demo Journal 2024 · Materials and methods › Cell culture",
            "Demo Journal 2024 · Troubleshooting", "Demo Journal 2024 · Notes"])


if __name__ == "__main__":
    unittest.main()
