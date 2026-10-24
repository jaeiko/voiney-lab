"""Lane KNOW: the public knowledge pipeline, fake-backed (human decision 2026-10-24).

``scripts/public_knowledge_pipeline.py`` reads the source list, fetches with
conditional requests under each source's terms, cuts the material into
pieces, drops duplicates and writes the bundle; ``upload`` sends it to Moss
and ``evaluate`` measures it. Nothing here reaches the network: the
transport is a dictionary of canned replies, the Moss client a recorder.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "public_knowledge_pipeline.py"
SOURCES = ROOT / "data" / "public_knowledge" / "sources.toml"


def _load_script():
    spec = importlib.util.spec_from_file_location("public_knowledge_pipeline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["public_knowledge_pipeline"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pk = _load_script()


class FakeTransport:
    """URL → (status, headers, body); every call is remembered."""

    def __init__(self, replies: dict[str, tuple[int, dict[str, str], bytes]]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> Any:
        self.calls.append((url, dict(headers)))
        for key, reply in self.replies.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                status, reply_headers, body = reply(url) if callable(reply) else reply
                if headers.get("If-None-Match") and headers["If-None-Match"] == reply_headers.get("ETag"):
                    return pk.Response(status=304, headers=reply_headers, body=b"", url=url)
                return pk.Response(status=status, headers=reply_headers, body=body, url=url)
        return pk.Response(status=404, headers={}, body=b"", url=url)


def _source(**overrides: Any) -> Any:
    base = dict(
        id="demo", name="Demo source", kind="html", grade="A", domains=("safety",), language="en",
        license="Public domain", license_url="https://example.org/licence", license_checked="2026-10-24",
        terms="one page", requests_per_second=5.0, options={},
    )
    base.update(overrides)
    return pk.Source(**base)


def _toml(body: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "sources.toml"
    path.write_text(body, encoding="utf-8")
    return path


HEADER = 'version = 1\nuser_agent = "Test/0.1 (https://example.org/contact)"\n'
SOURCE_BLOCK = """
[[source]]
id = "{id}"
name = "Demo"
kind = "{kind}"
grade = "A"
domains = ["safety"]
language = "en"
license = "{license}"
license_url = "https://example.org/licence"
license_checked = "2026-10-24"
terms = "one page"
url = "https://example.org/page"
"""


class SourceListTests(unittest.TestCase):
    def test_the_repository_list_is_valid_and_every_source_has_an_allowed_licence(self):
        sources = pk.load_sources(SOURCES)
        self.assertGreaterEqual(len(sources.sources), 8)
        ids = [source.id for source in sources.sources]
        self.assertEqual(len(ids), len(set(ids)))
        for source in sources.sources:
            with self.subTest(source=source.id):
                self.assertTrue(pk.license_allowed(source.license), source.license)
                self.assertTrue(source.license_url.startswith("https://"))
                self.assertRegex(source.license_checked, r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(source.terms.strip())
                self.assertIn(source.kind, pk.KINDS)
        for excluded in sources.excluded:
            with self.subTest(excluded=excluded.id):
                self.assertTrue(excluded.reason.strip())
                self.assertTrue(excluded.evidence_url.startswith("https://"))
        # Decision 1's candidates that were looked at and left out are named.
        excluded_ids = {item.id for item in sources.excluded}
        self.assertLessEqual({"protocols-io", "materials-project", "openstax-2e", "kosha-msds"}, excluded_ids)

    def test_a_non_commercial_or_no_derivatives_licence_is_refused_by_name(self):
        for licence in ("CC BY-NC-SA 4.0", "CC BY-ND 4.0", "All rights reserved", "Creative Commons Attribution-NonCommercial"):
            with self.subTest(licence=licence):
                path = _toml(HEADER + SOURCE_BLOCK.format(id="bad", kind="html", license=licence))
                with self.assertRaises(pk.SourceListError) as caught:
                    pk.load_sources(path)
                self.assertIn("bad", str(caught.exception))
                self.assertIn("licence", str(caught.exception))

    def test_allowed_licences(self):
        for licence in ("Public domain (U.S. Government work)", "CC0 1.0", "CC BY 4.0", "CC BY-SA 4.0",
                        "Creative Commons Attribution License 4.0"):
            with self.subTest(licence=licence):
                self.assertTrue(pk.license_allowed(licence))
        for licence in ("CC BY-NC 4.0", "CC BY-NC-ND", "비상업적 이용", "proprietary", ""):
            with self.subTest(licence=licence):
                self.assertFalse(pk.license_allowed(licence))

    def test_an_unknown_kind_a_bad_grade_and_a_missing_contact_are_refused(self):
        with self.assertRaises(pk.SourceListError):
            pk.load_sources(_toml(HEADER + SOURCE_BLOCK.format(id="x", kind="ftp", license="CC0 1.0")))
        with self.assertRaises(pk.SourceListError):
            pk.load_sources(_toml(HEADER + SOURCE_BLOCK.format(id="x", kind="html", license="CC0 1.0").replace(
                'grade = "A"', 'grade = "Z"')))
        with self.assertRaises(pk.SourceListError):
            pk.load_sources(_toml('version = 1\nuser_agent = "python-requests"\n'))

    def test_excluded_entries_need_a_reason_and_evidence(self):
        with self.assertRaises(pk.SourceListError):
            pk.load_sources(_toml(HEADER + '\n[[excluded]]\nid = "x"\nname = "X"\nreason = ""\n'
                                  'evidence_url = "https://example.org"\nchecked = "2026-10-24"\n'))


class ChunkTests(unittest.TestCase):
    def test_pieces_stay_under_the_bound_and_cut_at_sentence_ends(self):
        sentence = "The buffer is prepared at room temperature and kept on ice. "
        text = sentence * 60
        chunks = pk.chunk_text(text)
        self.assertGreater(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), pk.CHUNK_MAX_CHARS)
            self.assertTrue(chunk.endswith("ice."), chunk[-30:])
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_paragraphs_are_joined_and_a_short_text_is_one_piece(self):
        self.assertEqual(pk.chunk_text("Short line."), ["Short line."])
        text = "\n\n".join(f"Paragraph {n} says something useful about the method." for n in range(12))
        chunks = pk.chunk_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Paragraph 11", chunks[0])

    def test_a_word_longer_than_the_bound_is_cut_at_the_bound(self):
        chunks = pk.chunk_text("x" * 2500)
        self.assertEqual([len(c) for c in chunks], [1000, 1000, 500])

    def test_empty_text_gives_no_piece(self):
        self.assertEqual(pk.chunk_text("  \n\n  "), [])

    def test_records_carry_the_source_facts_and_stable_ids(self):
        piece = pk.Piece(title="T", locator="p. 3", text="Alpha. " * 300, url="https://example.org/d",
                         language="en", domain="safety", version="v1", fetched_at="2026-10-24")
        records = pk.records_for(piece, _source(id="src", grade="B", license="CC BY 4.0"))
        self.assertGreater(len(records), 1)
        self.assertEqual(records[0]["locator"], f"p. 3 · 1/{len(records)}")
        self.assertEqual({r["tier"] for r in records}, {"public"})
        self.assertEqual(records[0]["grade"], "B")
        self.assertEqual(records[0]["license"], "CC BY 4.0")
        again = pk.records_for(piece, _source(id="src", grade="B", license="CC BY 4.0"))
        self.assertEqual([r["id"] for r in records], [r["id"] for r in again])
        self.assertTrue(all(r["id"].startswith("src:") for r in records))


NPG_PAGE = """<html><body><main><h1>Acetaldehyde</h1>
<h2>Synonyms &amp; Trade Names</h2><p>Acetic aldehyde, Ethanal</p>
<h2>CAS No.</h2><p>75-07-0</p>
<h2>Flash Point</h2><p>-36°F</p>
<h2>Incompatibilities &amp; Reactivities</h2><p>Strong oxidizers, acids, bases</p>
<h2>Exposure Routes</h2><p>inhalation, ingestion, skin and/or eye contact</p>
<h2>First Aid</h2><p>( See procedures )</p><p>Eye: Irrigate immediately</p><p>Skin: Water flush promptly</p>
<p>Page last reviewed: October 30, 2019</p></main></body></html>"""


class NpgTests(unittest.TestCase):
    def test_a_page_becomes_named_fields(self):
        name, fields = pk.parse_npg_page(NPG_PAGE)
        self.assertEqual(name, "Acetaldehyde")
        self.assertEqual(fields["Flash Point"], "-36°F")
        self.assertEqual(fields["Synonyms & Trade Names"], "Acetic aldehyde, Ethanal")
        self.assertEqual(fields["First Aid"], "Eye: Irrigate immediately Skin: Water flush promptly")
        self.assertNotIn("Page last reviewed", json.dumps(fields))

    def test_the_index_links_are_read_with_either_quote(self):
        markup = """<tr><td>75-07-0</td><td><a href='npgd0001.html'>Acetaldehyde</a></td></tr>
        <tr><td>64-19-7</td><td><a href="/niosh/npg/npgd0002.html">Acetic acid</a></td></tr>"""
        found = pk._NPG_INDEX_LINK.findall(markup)
        self.assertEqual([(f, n) for f, _d, n in found], [("npgd0001.html", "Acetaldehyde"), ("npgd0002.html", "Acetic acid")])

    def test_the_handler_yields_two_pieces_per_chemical_and_records_a_failed_page(self):
        index = """<a href='npgd0001.html'>Acetaldehyde</a> <a href='npgd0002.html'>Broken</a>"""
        transport = FakeTransport({
            "https://www.cdc.gov/robots.txt": (200, {}, b"User-agent: *\nDisallow: /niosh/archive/\n"),
            "https://www.cdc.gov/niosh/npg/npgdcas.html": (200, {"ETag": '"idx"'}, index.encode()),
            "https://www.cdc.gov/niosh/npg/npgd0001.html": (200, {"ETag": '"p1"'}, NPG_PAGE.encode()),
            "https://www.cdc.gov/niosh/npg/npgd0002.html": (500, {}, b""),
        })
        source = _source(id="npg", kind="niosh_npg", options={
            "index_url": "https://www.cdc.gov/niosh/npg/npgdcas.html", "base_url": "https://www.cdc.gov/niosh/npg/"})
        with tempfile.TemporaryDirectory() as temporary:
            ctx = pk.RunContext(sources=pk.SourceList("Test/0.1 (x)", (source,), (), Path(temporary)),
                                http=pk.Http("Test/0.1 (x)", transport=transport, sleep=lambda s: None),
                                runtime_dir=Path(temporary), report=pk.RunReport())
            pieces = list(pk.handle_niosh_npg(source, ctx))
        self.assertEqual([p.locator for p in pieces],
                         ["npgd0001 · Identification and physical properties",
                          "npgd0001 · Exposure, symptoms, protection and first aid"])
        self.assertIn("Flash Point: -36°F", pieces[0].text)
        self.assertEqual([f["item"] for f in ctx.report.failures], ["npgd0002.html"])


def _pubchem_record() -> dict:
    return {
        "RecordTitle": "Demo-nitrile",
        "Reference": [
            {"ReferenceNumber": 1, "SourceName": "Hazardous Substances Data Bank (HSDB)"},
            {"ReferenceNumber": 2, "SourceName": "European Chemicals Agency (ECHA)"},
            {"ReferenceNumber": 3, "SourceName": "ILO-WHO International Chemical Safety Cards (ICSCs)"},
        ],
        "Section": [{
            "TOCHeading": "Chemical and Physical Properties",
            "Section": [{
                "TOCHeading": "Experimental Properties",
                "Section": [
                    {"TOCHeading": "Flash Point", "Information": [
                        {"ReferenceNumber": 1, "Value": {"StringWithMarkup": [{"String": "42 °F (6 °C) (Open Cup)"}]}},
                        {"ReferenceNumber": 3, "Value": {"StringWithMarkup": [{"String": "2 °C c.c."}]}},
                    ]},
                    {"TOCHeading": "Boiling Point", "Information": [
                        {"ReferenceNumber": 2, "Value": {"StringWithMarkup": [{"String": "81 °C"}]}},
                    ]},
                    {"TOCHeading": "Patents", "Information": [
                        {"ReferenceNumber": 1, "Value": {"StringWithMarkup": [{"String": "US123"}]}},
                    ]},
                ],
            }],
        }, {
            "TOCHeading": "Safety and Hazards",
            "Section": [{"TOCHeading": "GHS Classification", "Information": [
                {"ReferenceNumber": 2, "Value": {"StringWithMarkup": [{"String": "H225"}]}}]}],
        }],
    }


class PubChemTests(unittest.TestCase):
    def test_only_allowed_references_and_listed_headings_are_kept(self):
        statements = pk.pubchem_statements(
            _pubchem_record(), headings=["Flash Point", "Boiling Point", "GHS Classification"],
            allowed_references=["Hazardous Substances Data Bank (HSDB)",
                                "ILO-WHO International Chemical Safety Cards (ICSCs)"])
        self.assertEqual(sorted(statements), ["Flash Point"])
        self.assertEqual([ref for _v, ref in statements["Flash Point"]],
                         ["Hazardous Substances Data Bank (HSDB)", "ILO-WHO International Chemical Safety Cards (ICSCs)"])

    def test_pieces_name_the_statement_source_and_its_licence(self):
        source = _source(id="pubchem", kind="pubchem", options={
            "headings": ["Flash Point", "Boiling Point"],
            "allowed_references": ["Hazardous Substances Data Bank (HSDB)",
                                   "ILO-WHO International Chemical Safety Cards (ICSCs)"]})
        pieces = list(pk.pubchem_pieces(_pubchem_record(), source, cid=42, fetched_at="2026-10-24"))
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0].locator, "CID 42 · Physical properties")
        self.assertIn("Flash Point: 42 °F (6 °C) (Open Cup) [HSDB]", pieces[0].text)
        self.assertIn("[ILO ICSC]", pieces[0].text)
        self.assertNotIn("81 °C", pieces[0].text)
        self.assertEqual(pieces[0].license, "CC BY 4.0 (ILO); Public domain (NLM)")
        self.assertEqual(pieces[0].url, "https://pubchem.ncbi.nlm.nih.gov/compound/42")


class PubChemCidTests(unittest.TestCase):
    def test_names_become_cids_from_the_list_or_wikidata_and_pubchem_gets_only_pug_view(self):
        wikidata = json.dumps({"entities": {"Q1": {"sitelinks": {"enwiki": {"title": "Dimethylformamide"}}, "claims": {
            "P662": [{"rank": "normal", "mainsnak": {"datavalue": {"value": "6228"}}}]}}}}).encode()
        record = json.dumps({"Record": _pubchem_record()}).encode()
        transport = FakeTransport({
            "https://pubchem.ncbi.nlm.nih.gov/robots.txt": (200, {}, b"User-agent: *\nCrawl-delay: 5\nDisallow: /rest/pug/\n"),
            "https://www.wikidata.org/w/api.php?*": (200, {}, wikidata),
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/6228/JSON": (200, {}, record),
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/14013/JSON": (200, {}, record),
        })
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            (data / "compounds.txt").write_text(
                "ammonium bicarbonate\t14013\nN,N-dimethylformamide\tenwiki:Dimethylformamide\nunknownium\n", encoding="utf-8")
            source = _source(id="pubchem", kind="pubchem", options={
                "base_url": "https://pubchem.ncbi.nlm.nih.gov/rest/", "compounds_file": "compounds.txt",
                "headings": ["Flash Point"], "allowed_references": ["Hazardous Substances Data Bank (HSDB)"]})
            ctx = pk.RunContext(sources=pk.SourceList("Test/0.1 (x)", (source,), (), data),
                                http=pk.Http("Test/0.1 (x)", transport=transport, sleep=lambda s: None),
                                runtime_dir=Path(temporary), report=pk.RunReport())
            pieces = list(pk.handle_pubchem(source, ctx))
            state = pk.RawStore(Path(temporary) / "raw" / "pubchem").state
        self.assertEqual(sorted(p.locator for p in pieces),
                         ["CID 14013 · Physical properties", "CID 6228 · Physical properties"])
        self.assertEqual(state["cids"], {"ammonium bicarbonate": 14013, "N,N-dimethylformamide": 6228, "unknownium": None})
        self.assertEqual([f["item"] for f in ctx.report.failures], ["unknownium"])
        pubchem_urls = [url for url, _h in transport.calls if "pubchem" in url and "robots" not in url]
        self.assertTrue(all("/rest/pug_view/" in url for url in pubchem_urls), pubchem_urls)


class SectionTests(unittest.TestCase):
    def test_headings_become_paths_and_reference_sections_are_skipped(self):
        extract = "Intro text here.\n\n== Uses ==\nUsed widely.\n=== Solvent ===\nAs a solvent.\n\n== References ==\n1. x"
        sections = pk.sections_of(extract)
        self.assertEqual([name for name, _ in sections], ["", "Uses", "Uses › Solvent", "References"])
        pieces = list(pk.wikipedia_pieces(extract, title="T", url="u", revid=5, language="en", domain="chemistry",
                                          fetched_at="2026-10-24"))
        self.assertEqual([p.locator for p in pieces if len(p.text) >= 0], [])  # all sections too short
        long = "Intro text here. " * 10
        pieces = list(pk.wikipedia_pieces(f"{long}\n\n== References ==\n{long}", title="T", url="u", revid=5,
                                          language="en", domain="chemistry", fetched_at="2026-10-24"))
        self.assertEqual([p.locator for p in pieces], ["Introduction"])
        self.assertEqual(pieces[0].version, "revision 5")

    def test_html_to_text_drops_scripts_and_keeps_headings(self):
        markup = "<html><head><style>x{}</style></head><body><nav>menu</nav><h2>Risk</h2><p>Line one.<br>Line two.</p><script>bad()</script></body></html>"
        text = pk.html_to_text(markup)
        self.assertNotIn("menu", text)
        self.assertNotIn("bad()", text)
        self.assertIn("== Risk ==", text)
        self.assertIn("Line one.\nLine two.", text)


JATS = """<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><journal-meta><journal-title-group>
<journal-title>Demo Journal</journal-title></journal-title-group></journal-meta><article-meta>
<article-id pub-id-type="pmcid">PMC1</article-id><title-group><article-title>A method</article-title></title-group>
<pub-date><year>2024</year></pub-date><permissions><license license-type="open-access" xlink:href="{href}">
<license-p>terms</license-p></license></permissions><abstract><p>{text}</p></abstract></article-meta></front>
<body><sec><title>Methods</title><p>{text}</p></sec><sec><title>References</title><p>ref</p></sec></body></article>"""


class EuropePmcTests(unittest.TestCase):
    def _run(self, href: str, search_license: str = "cc by") -> tuple[list, list]:
        text = "The gel is destained in water for ten minutes before digestion. " * 3
        search = json.dumps({"resultList": {"result": [{"pmcid": "PMC1", "license": search_license}]}}).encode()
        transport = FakeTransport({
            "https://www.ebi.ac.uk/robots.txt": (200, {}, b"User-agent: *\nDisallow: /private\n"),
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?*": (200, {}, search),
            "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML": (
                200, {}, JATS.format(href=href, text=text).encode()),
        })
        source = _source(id="epmc", kind="europepmc", options={
            "base_url": "https://www.ebi.ac.uk/europepmc/webservices/rest/", "queries": ["in-gel digestion"],
            "max_per_query": 2, "pmcids": []})
        with tempfile.TemporaryDirectory() as temporary:
            ctx = pk.RunContext(sources=pk.SourceList("Test/0.1 (x)", (source,), (), Path(temporary)),
                                http=pk.Http("Test/0.1 (x)", transport=transport, sleep=lambda s: None),
                                runtime_dir=Path(temporary), report=pk.RunReport())
            pieces = list(pk.handle_europepmc(source, ctx))
        return pieces, ctx.report.failures

    def test_a_cc_by_article_gives_pieces_with_its_licence_and_place(self):
        pieces, failures = self._run("http://creativecommons.org/licenses/by/4.0/")
        self.assertEqual(failures, [])
        self.assertEqual([p.locator for p in pieces], ["Demo Journal 2024 · Abstract", "Demo Journal 2024 · Methods"])
        self.assertEqual(pieces[0].license, "CC BY 4.0")
        self.assertEqual(pieces[0].url, "https://europepmc.org/article/PMC/PMC1")
        self.assertEqual(pieces[0].title, "A method")

    def test_an_article_whose_xml_says_nc_is_refused_even_when_the_search_said_cc_by(self):
        pieces, failures = self._run("http://creativecommons.org/licenses/by-nc/3.0")
        self.assertEqual(pieces, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("licence", failures[0]["reason"])

    def test_a_search_hit_that_is_not_cc_by_is_never_fetched(self):
        pieces, failures = self._run("http://creativecommons.org/licenses/by/4.0/", search_license="cc by-nc")
        self.assertEqual(pieces, [])
        self.assertEqual(failures, [])


class HttpTests(unittest.TestCase):
    def test_requests_are_paced_per_source(self):
        clock = {"now": 0.0}
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock["now"] += seconds

        transport = FakeTransport({"https://example.org/robots.txt": (404, {}, b""),
                                   "https://example.org/a": (200, {}, b"a"), "https://example.org/b": (200, {}, b"b")})
        http = pk.Http("Test/0.1 (x)", transport=transport, sleep=sleep, clock=lambda: clock["now"])
        http.get("https://example.org/a", pace_key="s", per_second=2.0)
        http.get("https://example.org/b", pace_key="s", per_second=2.0)
        self.assertEqual(sleeps, [0.5])

    def test_robots_disallow_is_a_fetch_error_and_a_missing_robots_file_allows(self):
        transport = FakeTransport({"https://a.example/robots.txt": (200, {}, b"User-agent: *\nDisallow: /private/\n"),
                                   "https://a.example/private/x": (200, {}, b"x"), "https://a.example/open": (200, {}, b"o"),
                                   "https://b.example/robots.txt": (404, {}, b""), "https://b.example/x": (200, {}, b"x")})
        http = pk.Http("Test/0.1 (x)", transport=transport, sleep=lambda s: None)
        with self.assertRaises(pk.FetchError) as caught:
            http.get("https://a.example/private/x", pace_key="s", per_second=5)
        self.assertIn("robots.txt", str(caught.exception))
        self.assertEqual(http.get("https://a.example/open", pace_key="s", per_second=5).body, b"o")
        self.assertEqual(http.get("https://b.example/x", pace_key="s", per_second=5).body, b"x")

    def test_an_unreachable_robots_file_refuses_the_host_unless_the_source_is_an_api_client(self):
        transport = FakeTransport({"https://c.example/robots.txt": (503, {}, b""), "https://c.example/x": (200, {}, b"x")})
        http = pk.Http("Test/0.1 (x)", transport=transport, sleep=lambda s: None)
        with self.assertRaises(pk.FetchError):
            http.get("https://c.example/x", pace_key="s", per_second=5)
        self.assertEqual(http.get("https://c.example/x", pace_key="s", per_second=5, robots="api").body, b"x")

    def test_429_is_retried_after_the_server_s_wait_and_the_throttling_header_slows_down(self):
        sleeps: list[float] = []
        state = {"n": 0}

        def transport(url: str, headers: dict, timeout: float) -> Any:
            if url.endswith("robots.txt"):
                return pk.Response(404, {}, b"", url)
            state["n"] += 1
            if state["n"] == 1:
                return pk.Response(429, {"Retry-After": "7"}, b"", url)
            return pk.Response(200, {"X-Throttling-Control": "Request Count status: Yellow (60%)"}, b"ok", url)

        http = pk.Http("Test/0.1 (x)", transport=transport, sleep=sleeps.append)
        reply = http.get("https://p.example/x", pace_key="s", per_second=5)
        self.assertEqual(reply.body, b"ok")
        self.assertIn(7.0, sleeps)
        self.assertIn(3.0, sleeps)
        self.assertEqual(http.requests_made, 3)

    def test_a_mediawiki_maxlag_answer_is_waited_out_not_cached(self):
        sleeps: list[float] = []
        state = {"n": 0}
        lagged = b'{"error":{"code":"maxlag","info":"Waiting for a replica: 5.6 seconds lagged."}}'

        def transport(url: str, headers: dict, timeout: float) -> Any:
            state["n"] += 1
            if state["n"] == 1:
                return pk.Response(200, {"Retry-After": "5"}, lagged, url)
            return pk.Response(200, {}, b'{"entities": {}}', url)

        http = pk.Http("Test/0.1 (x)", transport=transport, sleep=sleeps.append, clock=lambda: 0.0)
        reply = http.get("https://www.wikidata.org/w/api.php?action=wbgetentities", pace_key="s", per_second=5,
                         robots="api")
        self.assertEqual(reply.body, b'{"entities": {}}')
        self.assertEqual(sleeps[0], 5.0)  # the server's wait; then the ordinary pace

    def test_a_robots_crawl_delay_slows_the_pace_below_the_source_s(self):
        clock = {"now": 0.0}
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock["now"] += seconds

        transport = FakeTransport({"https://slow.example/robots.txt": (200, {}, b"User-agent: *\nCrawl-delay: 5\n"),
                                   "https://slow.example/a": (200, {}, b"a"), "https://slow.example/b": (200, {}, b"b")})
        http = pk.Http("Test/0.1 (x)", transport=transport, sleep=sleep, clock=lambda: clock["now"])
        http.get("https://slow.example/a", pace_key="s", per_second=2.0)
        http.get("https://slow.example/b", pace_key="s", per_second=2.0)
        self.assertEqual(sleeps, [5.0])

    def test_the_user_agent_names_the_client_and_a_contact(self):
        transport = FakeTransport({"https://example.org/robots.txt": (404, {}, b""), "https://example.org/a": (200, {}, b"a")})
        http = pk.Http("Test/0.1 (https://example.org/contact)", transport=transport, sleep=lambda s: None)
        http.get("https://example.org/a", pace_key="s", per_second=5)
        self.assertEqual(transport.calls[-1][1]["User-Agent"], "Test/0.1 (https://example.org/contact)")


def _collect_fixture(temporary: Path, *, article_revid: int = 10, missing: bool = False) -> tuple[Any, FakeTransport]:
    directory = temporary / "data"
    directory.mkdir(exist_ok=True)
    (directory / "titles.en.txt").write_text("# chemistry\nAcetonitrile\nNo such page\n", encoding="utf-8")
    (directory / "titles.ko.txt").write_text("# safety\n실험실 안전\n", encoding="utf-8")
    (directory / "terms.txt").write_text("Flash point\n", encoding="utf-8")
    toml = HEADER + """
[[source]]
id = "glossary"
name = "Wikidata labels"
kind = "wikidata_glossary"
grade = "A"
domains = ["glossary"]
language = "ko"
license = "CC0 1.0"
license_url = "https://www.wikidata.org/wiki/Wikidata:Copyright"
license_checked = "2026-10-24"
terms = "api"
api_url = "https://www.wikidata.org/w/api.php"
from_sources = ["en"]
extra_terms_file = "terms.txt"

[[source]]
id = "page"
name = "Demo guidance"
kind = "html"
grade = "A"
domains = ["safety"]
language = "en"
license = "Public domain"
license_url = "https://example.org/licence"
license_checked = "2026-10-24"
terms = "one page"
url = "https://docs.example/guide"
version = "2024"

[[source]]
id = "en"
name = "Wikipedia (English)"
kind = "wikipedia"
grade = "C"
domains = ["chemistry"]
language = "en"
license = "CC BY-SA 4.0"
license_url = "https://en.wikipedia.org/wiki/Wikipedia:Reusing_Wikipedia_content"
license_checked = "2026-10-24"
terms = "api"
api_url = "https://en.wikipedia.org/w/api.php"
titles_file = "titles.en.txt"

[[source]]
id = "ko"
name = "위키백과"
kind = "wikipedia"
grade = "C"
domains = ["safety"]
language = "ko"
license = "CC BY-SA 4.0"
license_url = "https://ko.wikipedia.org/wiki/위키백과:저작권"
license_checked = "2026-10-24"
terms = "api"
api_url = "https://ko.wikipedia.org/w/api.php"
titles_file = "titles.ko.txt"
sitelinks_from = "en"
"""
    (directory / "sources.toml").write_text(toml, encoding="utf-8")
    sources = pk.load_sources(directory / "sources.toml")
    guide = "<html><body><h2>Fume hoods</h2><p>" + "Work with volatile solvents inside a fume hood with the sash low. " * 4 + \
            "</p><h2>References</h2><p>1. x</p></body></html>"
    en_text = "Acetonitrile is a solvent. " * 6 + "\n\n== Safety ==\n" + "It is flammable and has a low flash point. " * 5
    ko_text = "실험실 안전은 실험실에서 일어날 수 있는 사고를 막는 일이다. " * 5
    en_info = {"query": {"pages": [
        {"title": "Acetonitrile", "pageid": 1, "lastrevid": article_revid, "fullurl": "https://en.wikipedia.org/wiki/Acetonitrile"},
        {"title": "No such page", "missing": True}]}}
    if not missing:
        en_info["query"]["pages"][1] = {"title": "No such page", "pageid": 2, "lastrevid": 3,
                                        "fullurl": "https://en.wikipedia.org/wiki/No_such_page"}
    replies = {
        "https://www.wikidata.org/w/api.php?*": (200, {}, json.dumps({"entities": {"Q1": {
            "labels": {"en": {"value": "acetonitrile"}, "ko": {"value": "아세토나이트릴"}},
            "aliases": {"ko": [{"value": "아세토니트릴"}], "en": [{"value": "methyl cyanide"}]},
            "sitelinks": {"enwiki": {"title": "Acetonitrile"}, "kowiki": {"title": "아세토나이트릴"}}},
            "Q2": {"labels": {"en": {"value": "flash point"}, "ko": {"value": "인화점"}}, "aliases": {},
                   "sitelinks": {"enwiki": {"title": "Flash point"}}}}}).encode()),
        "https://docs.example/robots.txt": (404, {}, b""),
        "https://docs.example/guide": (200, {"ETag": '"g1"'}, guide.encode()),
        "https://en.wikipedia.org/w/api.php?action=query&prop=info*": (200, {}, json.dumps(en_info).encode()),
        "https://en.wikipedia.org/w/api.php?action=query&prop=extracts*": (
            200, {}, json.dumps({"query": {"pages": [{"extract": en_text}]}}).encode()),
        "https://ko.wikipedia.org/w/api.php?action=query&prop=info*": (200, {}, json.dumps({"query": {"pages": [
            {"title": "실험실 안전", "pageid": 5, "lastrevid": 7, "fullurl": "https://ko.wikipedia.org/wiki/실험실_안전"},
            {"title": "아세토나이트릴", "pageid": 6, "lastrevid": 8, "fullurl": "https://ko.wikipedia.org/wiki/아세토나이트릴"},
        ]}}).encode()),
        "https://ko.wikipedia.org/w/api.php?action=query&prop=extracts*": lambda url: (
            200, {}, json.dumps({"query": {"pages": [{"extract": (
                ko_text if "%EC%8B%A4%ED%97%98%EC%8B%A4" in url else "아세토나이트릴은 무색의 액체 용매이다. " * 5)}]}}).encode()),
        "https://ko.wikipedia.org/w/api.php?action=query&prop=redirects*": (
            200, {}, json.dumps({"query": {"pages": [{"redirects": [{"title": "아세토니트릴"}]}]}}).encode()),
    }
    return sources, FakeTransport(replies)


class CollectTests(unittest.TestCase):
    def test_a_run_writes_the_bundle_the_glossary_the_manifest_and_the_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, transport = _collect_fixture(root, missing=True)
            http = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
            manifest = pk.collect(sources, runtime_dir=root / "runtime", http=http, log=lambda line: None)
            bundle = root / "runtime" / "bundle"
            records = [json.loads(line) for line in (bundle / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
            by_source = {}
            for record in records:
                by_source.setdefault(record["source_id"], []).append(record)
            self.assertEqual(sorted(by_source), ["en", "ko", "page"])
            self.assertEqual([r["locator"] for r in by_source["page"]], ["Fume hoods"])
            self.assertEqual([r["locator"] for r in by_source["en"]], ["Introduction", "Safety"])
            self.assertEqual({r["title"] for r in by_source["ko"]}, {"실험실 안전", "아세토나이트릴"})
            self.assertEqual(by_source["ko"][0]["language"], "ko")
            self.assertEqual(by_source["en"][0]["license"], "CC BY-SA 4.0")
            self.assertEqual(by_source["en"][0]["version"], "revision 10")
            glossary = json.loads((bundle / "glossary.json").read_text(encoding="utf-8"))
            self.assertEqual(glossary["terms"]["아세토나이트릴"], ["Acetonitrile", "acetonitrile", "methyl cyanide"])
            self.assertIn("아세토니트릴", glossary["terms"])
            self.assertEqual(glossary["terms"]["인화점"], ["Flash point", "flash point"])
            self.assertEqual(glossary["aliases"]["Acetonitrile"], ["아세토나이트릴", "아세토니트릴"])
            failures = [json.loads(line) for line in (bundle / "failures.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(f["source_id"], f["item"], f["reason"]) for f in failures],
                             [("en", "No such page", "no such article")])
            self.assertEqual(manifest["totals"]["chunks"], len(records))
            self.assertEqual(manifest["sources"]["en"]["failures"], 1)
            self.assertEqual(manifest["totals"]["grades"], {"A": 1, "C": 4})
            self.assertIn("en.wikipedia.org", manifest["totals"]["requests_by_host"])

    def test_a_second_run_fetches_nothing_whose_revision_or_etag_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, transport = _collect_fixture(root)
            http = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
            pk.collect(sources, runtime_dir=root / "runtime", http=http, log=lambda line: None)
            first = [url for url, _h in transport.calls]
            transport.calls.clear()
            http2 = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
            manifest = pk.collect(sources, runtime_dir=root / "runtime", http=http2, log=lambda line: None)
            second = [url for url, _h in transport.calls]
            self.assertTrue(any("prop=extracts" in url for url in first))
            self.assertFalse(any("prop=extracts" in url for url in second), second)
            self.assertFalse(any("wikidata.org" in url for url in second), second)
            guide_calls = [h for url, h in transport.calls if url == "https://docs.example/guide"]
            self.assertEqual(guide_calls[0].get("If-None-Match"), '"g1"')
            self.assertEqual(manifest["totals"]["chunks"], 5)
            self.assertLess(len(second), len(first))

    def test_only_reruns_the_named_sources_and_keeps_the_others_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, transport = _collect_fixture(root)
            http = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
            pk.collect(sources, runtime_dir=root / "runtime", http=http, log=lambda line: None)
            transport.calls.clear()
            http2 = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
            manifest = pk.collect(sources, runtime_dir=root / "runtime", http=http2, only={"page"}, log=lambda line: None)
            self.assertEqual({url.split("/")[2] for url, _h in transport.calls}, {"docs.example"})
            self.assertEqual(manifest["totals"]["chunks"], 5)
            self.assertEqual(sorted(manifest["sources"]), ["page"])

    def test_a_source_whose_handler_breaks_is_recorded_and_the_others_still_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, transport = _collect_fixture(root)
            transport.replies["https://docs.example/guide"] = (200, {}, b"\xff\xfe<html>")
            broken = {**pk.HANDLERS, "html": lambda source, ctx: (_ for _ in ()).throw(RuntimeError("boom"))}
            original = pk.HANDLERS
            pk.HANDLERS = broken
            try:
                http = pk.Http(sources.user_agent, transport=transport, sleep=lambda s: None)
                manifest = pk.collect(sources, runtime_dir=root / "runtime", http=http, log=lambda line: None)
            finally:
                pk.HANDLERS = original
            self.assertEqual(manifest["sources"]["page"]["failures"], 1)
            self.assertEqual(manifest["sources"]["page"]["chunks"], 0)
            self.assertGreater(manifest["sources"]["en"]["chunks"], 0)


class UploadTests(unittest.TestCase):
    def _records(self) -> list[dict]:
        return [
            {"id": "a:1", "tier": "public", "title": "Acetonitrile", "text": "Flash point 2 °C.", "locator": "Safety",
             "url": "https://x/1", "license": "CC BY-SA 4.0", "language": "en", "domain": "chemistry",
             "source_id": "a", "source_name": "A", "grade": "C", "version": "r1", "fetched_at": "2026-10-24"},
            {"id": "a:2", "tier": "public", "title": "실험실 안전", "text": "후드 안에서 다룬다.", "locator": "개요",
             "url": "https://x/2", "license": "CC BY-SA 4.0", "language": "ko", "domain": "safety",
             "source_id": "a", "source_name": "A", "grade": "C", "version": "r2", "fetched_at": "2026-10-24"},
        ]

    def test_documents_carry_korean_names_string_metadata_and_the_words_as_payload(self):
        glossary = pk.Glossary(terms={}, aliases={"Acetonitrile": ("아세토나이트릴",)})
        documents = pk.documents_for(self._records(), glossary)
        self.assertEqual(documents[0]["text"], "Acetonitrile (아세토나이트릴) · Safety\nFlash point 2 °C.")
        self.assertEqual(documents[0]["payload"], "Flash point 2 °C.")
        self.assertEqual(set(documents[0]["metadata"]), set(pk.METADATA_FIELDS))
        self.assertTrue(all(isinstance(v, str) for v in documents[0]["metadata"].values()))
        estimate = pk.estimate_memory_mib(self._records())
        self.assertGreater(estimate["steady_mib"], pk.MOSS_MODEL_MIB)

    def test_upload_creates_then_updates_and_deletes(self):
        class Recorder:
            def __init__(self) -> None:
                self.calls: list[tuple] = []
                self.exists = False

            async def get_index(self, name):
                self.calls.append(("get_index", name))
                if not self.exists:
                    raise RuntimeError("not found")

            async def create_index(self, name, docs, model):
                self.calls.append(("create_index", name, [d["id"] for d in docs], model))

            async def add_docs(self, name, docs, options):
                self.calls.append(("add_docs", name, [d["id"] for d in docs], options))

            async def delete_docs(self, name, ids):
                self.calls.append(("delete_docs", name, list(ids)))

            async def close(self):
                self.calls.append(("close",))

        recorder = Recorder()
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "bundle").mkdir()
            records = self._records()
            first = asyncio.run(pk.upload(records, pk.Glossary.empty(), index_name="idx", model_id="moss-minilm",
                                          project_id="p", project_key="k", runtime_dir=runtime,
                                          client_factory=lambda pid, key: recorder, log=lambda line: None))
            self.assertTrue(first["created"])
            self.assertEqual(recorder.calls[1], ("create_index", "idx", ["a:1", "a:2"], "moss-minilm"))
            self.assertEqual(recorder.calls[-1], ("close",))
            recorder.exists = True
            recorder.calls.clear()
            records[1]["text"] = "후드 안에서만 다룬다."
            records.append({**records[0], "id": "a:3", "text": "New piece."})
            del records[0]
            second = asyncio.run(pk.upload(records, pk.Glossary.empty(), index_name="idx", model_id="moss-minilm",
                                           project_id="p", project_key="k", runtime_dir=runtime,
                                           client_factory=lambda pid, key: recorder, log=lambda line: None))
            self.assertFalse(second["created"])
            self.assertEqual(second["changed"], 2)
            self.assertEqual(second["deleted"], 1)
            self.assertEqual(recorder.calls[1], ("add_docs", "idx", ["a:2", "a:3"], {"upsert": True}))
            self.assertEqual(recorder.calls[2], ("delete_docs", "idx", ["a:1"]))
            state = json.loads((runtime / "bundle" / "upload.idx.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(state["documents"]), ["a:2", "a:3"])


class EvaluateTests(unittest.TestCase):
    def test_fts5_baseline_scores_recall_and_resolves_gold_by_source_title_and_text(self):
        records = [
            {"id": "npg:1", "title": "Acetonitrile", "text": "Flash Point: 42°F. Incompatibilities: strong oxidizers.",
             "locator": "npgd0006 · Identification", "url": "u", "license": "PD", "language": "en", "domain": "chemistry",
             "source_id": "npg", "source_name": "NPG", "grade": "A", "version": "v", "fetched_at": "d", "tier": "public"},
            {"id": "wp:1", "title": "Centrifuge", "text": "A centrifuge must be balanced before spinning tubes.",
             "locator": "Safety", "url": "u", "license": "CC BY-SA 4.0", "language": "en", "domain": "equipment",
             "source_id": "wp", "source_name": "Wikipedia", "grade": "C", "version": "v", "fetched_at": "d", "tier": "public"},
            {"id": "wp:2", "title": "원심분리기", "text": "원심분리기는 돌리기 전에 균형을 맞춘다.",
             "locator": "개요", "url": "u", "license": "CC BY-SA 4.0", "language": "ko", "domain": "equipment",
             "source_id": "wp", "source_name": "위키백과", "grade": "C", "version": "v", "fetched_at": "d", "tier": "public"},
        ]
        glossary = pk.Glossary(terms={"원심분리기": ("Centrifuge",), "아세토니트릴": ("Acetonitrile",), "인화점": ("Flash point",)})
        questions = [
            pk.Question("q1", "아세토니트릴 인화점이 몇 도야?", "ko", "property",
                        ({"source_id": "npg", "title_contains": "Acetonitrile", "text_contains": "Flash Point"},)),
            pk.Question("q2", "Why must a centrifuge be balanced?", "en", "equipment", ({"source_id": "wp", "title_contains": "Centrifuge"},)),
            pk.Question("q3", "원심분리기 균형", "ko", "equipment", ({"source_id": "*", "text_contains": "균형"},)),
            pk.Question("q4", "nothing matches this", "en", "x", ({"source_id": "npg", "title_contains": "Zzz"},)),
        ]
        result = pk.evaluate_fts5(questions, records, glossary)
        self.assertEqual(result["unresolved"], ["q4"])
        self.assertEqual(result["scored"], 3)
        self.assertEqual(result["groups"]["all"]["recall@5"], 1.0)
        self.assertEqual(result["groups"]["language:ko"]["n"], 2)
        self.assertIn("p50", result["latency_ms"])
        without = pk.evaluate_fts5(questions, records, glossary, expand=False)
        self.assertLess(without["groups"]["language:ko"]["recall@5"], 1.0)

    def test_score_runs_counts_a_hit_only_within_k(self):
        questions = [pk.Question("q", "x", "en", "", ())]
        gold = {"q": {"g"}}
        scored = pk.score_runs(questions, gold, {"q": ["a", "b", "c", "d", "e", "g"]}, {"q": 1.0})
        self.assertEqual(scored["groups"]["all"]["recall@5"], 0.0)
        self.assertEqual(scored["misses"], ["q"])
        scored = pk.score_runs(questions, gold, {"q": ["g"]}, {"q": 1.0})
        self.assertEqual(scored["groups"]["all"]["recall@1"], 1.0)
        self.assertEqual(scored["groups"]["all"]["mrr"], 1.0)


class CommandLineTests(unittest.TestCase):
    def test_status_without_a_bundle_says_so(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(pk.main(["--runtime-dir", temporary, "status"]), 1)

    def test_upload_dry_run_counts_without_a_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            record = {"id": "a:1", "tier": "public", "title": "T", "text": "Words.", "locator": "L", "url": "u",
                      "license": "CC0 1.0", "language": "en", "domain": "d", "source_id": "a", "source_name": "A",
                      "grade": "A", "version": "v", "fetched_at": "2026-10-24"}
            (bundle / "chunks.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = pk.main(["--runtime-dir", temporary, "upload", "--index", "x", "--dry-run"])
            self.assertEqual(code, 0)
            printed = json.loads(out.getvalue())
            self.assertEqual(printed["documents"], 1)
            self.assertIn("estimated_memory", printed)


if __name__ == "__main__":
    unittest.main()
