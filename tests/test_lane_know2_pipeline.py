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

import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_lane_know_pipeline import FakeTransport, _collect_fixture, _pubchem_record, _source, pk


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


class PageRangeTests(unittest.TestCase):
    def test_only_the_pages_between_the_bounds_are_taken(self) -> None:
        source = _source(id="doe", kind="pdf", options={"skip_pages_before": 2, "skip_pages_after": 3})
        pages = [f"Page {n}. " + LAB * 3 for n in range(1, 6)]
        pieces = list(pk.pdf_pieces(source, pages, url="https://x/h.pdf", fetched_at="2026-10-26"))
        self.assertEqual([piece.locator for piece in pieces], ["p. 2", "p. 3"])


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


class IloCardTests(unittest.TestCase):
    """Person's decision 2026-10-26, 4: the ILO-WHO International Chemical
    Safety Cards are left out of PubChem's statements. Lane KAUDIT read the
    card text "© ILO and WHO 2021 … All rights reserved" (Korean cards ©
    고용노동부·산업안전보건공단 2018), and ILO's copyright page puts only
    publications from 2023-05-03 under CC BY 4.0."""

    def test_the_repository_list_does_not_allow_the_cards_and_says_why(self) -> None:
        sources = pk.load_sources()
        pubchem = sources.by_id("pubchem")
        self.assertNotIn("ILO-WHO International Chemical Safety Cards (ICSCs)",
                         pubchem.option("allowed_references"))
        (excluded,) = [item for item in sources.excluded if item.id == "ilo-icsc"]
        self.assertIn("All rights reserved", excluded.reason)

    def test_a_record_s_card_statements_are_left_out(self) -> None:
        pubchem = pk.load_sources().by_id("pubchem")
        source = _source(id="pubchem", kind="pubchem", options={
            "headings": pubchem.option("headings"), "allowed_references": pubchem.option("allowed_references")})
        (piece,) = pk.pubchem_pieces(_pubchem_record(), source, cid=42, fetched_at="2026-10-26")
        self.assertNotIn("ILO", piece.text)
        self.assertNotIn("2 °C c.c.", piece.text)
        self.assertEqual(piece.license, "Public domain (NLM)")


class FakePassages:
    """Embeds a passage as [its length, 1, 0, …] (the model's dims), counting calls."""

    def __init__(self, dims: int) -> None:
        self.dims = dims
        self.seen: list[str] = []

    def embed_passages(self, texts, *, batch=8):
        import numpy as np

        self.seen += list(texts)
        return np.array([[float(len(text)), 1.0] + [0.0] * (self.dims - 2) for text in texts], dtype=np.float32)


RECORDS = [
    {"id": "a:1", "tier": "public", "title": "Acetonitrile", "text": "Flash point 2 °C.", "locator": "Safety",
     "url": "https://x/1", "license": "CC BY-SA 4.0", "language": "en", "domain": "chemistry",
     "source_id": "a", "source_name": "A", "grade": "C", "version": "r1", "fetched_at": "2026-10-24"},
    {"id": "a:2", "tier": "public", "title": "실험실 안전", "text": "후드 안에서 다룬다.", "locator": "개요",
     "url": "https://x/2", "license": "CC BY-SA 4.0", "language": "ko", "domain": "safety",
     "source_id": "a", "source_name": "A", "grade": "C", "version": "r2", "fetched_at": "2026-10-24"},
]


class EmbedTests(unittest.TestCase):
    """Person's decision 2026-10-26, 5: the pieces' vectors are made here by the
    pinned multilingual model, kept between runs, and sent with the pieces to
    a custom index whose pieces carry the model's key."""

    def test_vectors_are_cached_and_a_second_run_embeds_nothing(self) -> None:
        model = pk.EMBEDDING_MODELS["multilingual-e5-small"]
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            fake = FakePassages(model.dims)
            first = pk.embed(RECORDS, pk.Glossary.empty(), runtime_dir=runtime, model=model, embedder=fake,
                             log=lambda line: None)
            second = pk.embed(RECORDS, pk.Glossary.empty(), runtime_dir=runtime, model=model,
                              embedder=FakePassages(model.dims), log=lambda line: None)
            vectors = pk.EmbeddingCache(runtime, model).vectors_for(
                [doc["text"] for doc in pk.documents_for(RECORDS, pk.Glossary.empty())])
        self.assertEqual((first["embedded"], second["embedded"], second["cached"]), (2, 0, 2))
        self.assertEqual(fake.seen, sorted(fake.seen, key=len))  # shortest first
        self.assertEqual(vectors.shape, (2, model.dims))
        self.assertEqual(float(vectors[0][0]), float(len("Acetonitrile · Safety\nFlash point 2 °C.")))

    def test_a_custom_upload_sends_each_vector_and_the_model_s_key(self) -> None:
        model = pk.EMBEDDING_MODELS["multilingual-e5-small"]
        sent: list = []

        class Recorder:
            async def get_index(self, name):
                raise RuntimeError("not found")

            async def create_index(self, name, docs, model_id):
                sent.append((model_id, docs))

            async def add_docs(self, name, docs, options):
                sent.append(("add", docs))

            async def close(self):
                pass

        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "bundle").mkdir()
            with self.assertRaises(SystemExit):  # no vectors yet
                asyncio.run(pk.upload(RECORDS, pk.Glossary.empty(), index_name="idx", model_id="moss-minilm",
                                      project_id="p", project_key="k", runtime_dir=runtime, embedding=model,
                                      client_factory=lambda pid, key: Recorder(), log=lambda line: None))
            pk.embed(RECORDS, pk.Glossary.empty(), runtime_dir=runtime, model=model,
                     embedder=FakePassages(model.dims), log=lambda line: None)
            outcome = asyncio.run(pk.upload(RECORDS, pk.Glossary.empty(), index_name="idx", model_id="moss-minilm",
                                            project_id="p", project_key="k", runtime_dir=runtime, embedding=model,
                                            client_factory=lambda pid, key: Recorder(), log=lambda line: None))
        (model_id, docs), = sent
        self.assertEqual((model_id, outcome["model"]), ("custom", "custom"))
        self.assertEqual([len(doc["embedding"]) for doc in docs], [model.dims, model.dims])
        self.assertEqual({doc["metadata"]["embedding"] for doc in docs}, {model.key})


if __name__ == "__main__":
    unittest.main()
