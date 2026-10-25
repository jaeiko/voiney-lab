"""Lane KNOW: the ``public`` knowledge tier (human decision 2026-10-24).

``voiney_lab.public_knowledge`` reads its settings, loads one Moss index at
the server's start and searches it in process. Here the Moss client is a
fake: what is tested is the fail-closed settings (the missing setting by
name, no other provider), the grade ordering and labels of the hits, the
glossary that joins a Korean question to English material, and that this
tier's failure is its own.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from voiney_lab import public_knowledge as pk
from voiney_lab.public_knowledge import (
    DEFAULT_INDEX_NAME,
    ENABLED_SETTING,
    INDEX_SETTING,
    MOSS_PROJECT_ID_SETTING,
    MOSS_PROJECT_KEY_SETTING,
    PUBLIC_LABEL,
    FoundDocument,
    Glossary,
    KnowledgeContext,
    KnowledgeUnavailable,
    PublicKnowledgeConfigurationError,
    PublicKnowledgeSettings,
    PublicKnowledgeSource,
    grade_rank,
    index_text_for,
    public_reference_label,
    start_public_knowledge,
    wiki_only,
)

CONTEXT = KnowledgeContext(organization_id="lab", account_id="acc", protocol_id="p1", revision_id="r1",
                           step_id="s3", language="ko")


def _doc(doc_id: str, grade: str, *, title: str = "Acetonitrile", text: str = "Flash point is low.",
         source_name: str = "NIOSH Pocket Guide", score: float = 0.5, language: str = "en",
         payload: str | None = None) -> FoundDocument:
    return FoundDocument(
        id=doc_id, text=f"{title} · Safety\n{text}", score=score, payload=payload,
        metadata={"title": title, "locator": "Safety", "url": f"https://example.org/{doc_id}",
                  "license": "Public domain", "language": language, "domain": "chemistry",
                  "source_id": "src", "source_name": source_name, "grade": grade, "version": "v1",
                  "fetched_at": "2026-10-24"},
    )


class FakeClient:
    def __init__(self, docs=(), *, fail_load: Exception | None = None) -> None:
        self.docs = list(docs)
        self.fail_load = fail_load
        self.loaded: list[str] = []
        self.queries: list[tuple[str, str, int, float]] = []
        self.closed = False

    async def load(self, index_name: str) -> None:
        if self.fail_load:
            raise self.fail_load
        self.loaded.append(index_name)

    async def query(self, index_name: str, text: str, *, top_k: int, alpha: float):
        self.queries.append((index_name, text, top_k, alpha))
        return self.docs[:top_k]

    async def close(self) -> None:
        self.closed = True


class SettingsTests(unittest.TestCase):
    def test_off_by_default_and_the_index_name_has_a_default(self):
        settings = PublicKnowledgeSettings.from_environment({})
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.index_name, DEFAULT_INDEX_NAME)

    def test_a_malformed_switch_is_refused_by_name(self):
        with self.assertRaises(PublicKnowledgeConfigurationError) as caught:
            PublicKnowledgeSettings.from_environment({ENABLED_SETTING: "maybe"})
        self.assertIn(ENABLED_SETTING, str(caught.exception))

    def test_an_index_name_with_a_slash_is_refused_by_name(self):
        with self.assertRaises(PublicKnowledgeConfigurationError) as caught:
            PublicKnowledgeSettings.from_environment({ENABLED_SETTING: "true", INDEX_SETTING: "a/b"})
        self.assertIn(INDEX_SETTING, str(caught.exception))

    def test_the_settings_repr_never_shows_a_credential(self):
        settings = PublicKnowledgeSettings.from_environment(
            {ENABLED_SETTING: "true", MOSS_PROJECT_ID_SETTING: "proj-id-7f3a", MOSS_PROJECT_KEY_SETTING: "key-value-7f3a"})
        self.assertNotIn("7f3a", repr(settings))
        self.assertEqual(settings.project_key, "key-value-7f3a")

    def test_the_missing_credential_is_named(self):
        settings = PublicKnowledgeSettings.from_environment({ENABLED_SETTING: "true"})
        self.assertEqual(settings.missing_setting(), MOSS_PROJECT_ID_SETTING)
        settings = PublicKnowledgeSettings.from_environment({ENABLED_SETTING: "true", MOSS_PROJECT_ID_SETTING: "p"})
        self.assertEqual(settings.missing_setting(), MOSS_PROJECT_KEY_SETTING)
        settings = PublicKnowledgeSettings.from_environment(
            {ENABLED_SETTING: "true", MOSS_PROJECT_ID_SETTING: "p", MOSS_PROJECT_KEY_SETTING: "k"})
        self.assertIsNone(settings.missing_setting())


class StartTests(unittest.TestCase):
    def test_off_means_no_source_and_no_client(self):
        built: list[object] = []
        settings = PublicKnowledgeSettings(enabled=False)
        source = asyncio.run(start_public_knowledge(settings, client_factory=lambda s: built.append(s) or FakeClient()))
        self.assertIsNone(source)
        self.assertEqual(built, [])

    def test_enabled_without_credentials_fails_closed_by_the_setting_name(self):
        settings = PublicKnowledgeSettings(enabled=True, project_id="", project_key="k")
        with self.assertRaises(KnowledgeUnavailable) as caught:
            asyncio.run(start_public_knowledge(settings, client_factory=lambda s: FakeClient()))
        self.assertEqual(caught.exception.setting, MOSS_PROJECT_ID_SETTING)

    def test_a_failed_load_is_this_tier_s_failure_and_closes_the_client(self):
        client = FakeClient(fail_load=RuntimeError("network"))
        settings = PublicKnowledgeSettings(enabled=True, project_id="p", project_key="k")
        with self.assertRaises(KnowledgeUnavailable) as caught:
            asyncio.run(start_public_knowledge(settings, client_factory=lambda s: client))
        self.assertEqual(caught.exception.setting, INDEX_SETTING)
        self.assertTrue(client.closed)

    def test_a_successful_load_reports_numbers_only(self):
        client = FakeClient()
        settings = PublicKnowledgeSettings(enabled=True, project_id="p", project_key="k", index_name="idx")
        with tempfile.TemporaryDirectory() as temporary:
            glossary = Path(temporary) / "glossary.json"
            glossary.write_text(json.dumps({"terms": {"인화점": ["flash point"]}}), encoding="utf-8")
            source = asyncio.run(start_public_knowledge(settings, client_factory=lambda s: client, glossary_path=glossary))
        assert source is not None
        self.assertEqual(client.loaded, ["idx"])
        status = source.status()
        self.assertEqual(status["index"], "idx")
        self.assertTrue(status["loaded"])
        self.assertEqual(status["glossary_terms"], 1)
        self.assertIsInstance(status["load_ms"], float)
        self.assertNotIn("p", json.dumps(status).replace('"loaded"', "").replace("public", ""))  # no credential
        self.assertEqual(set(status), {"enabled", "loaded", "index", "glossary_terms", "load_ms"})

    def test_a_missing_glossary_file_means_no_expansion_not_a_failure(self):
        client = FakeClient()
        settings = PublicKnowledgeSettings(enabled=True, project_id="p", project_key="k")
        source = asyncio.run(start_public_knowledge(
            settings, client_factory=lambda s: client, glossary_path="/nonexistent/glossary.json"))
        assert source is not None
        self.assertEqual(len(source.glossary), 0)


class SearchTests(unittest.TestCase):
    def _source(self, docs, *, glossary: Glossary | None = None, enabled: bool = True) -> tuple[PublicKnowledgeSource, FakeClient]:
        client = FakeClient(docs)
        settings = PublicKnowledgeSettings(enabled=enabled, project_id="p", project_key="k", index_name="idx")
        source = PublicKnowledgeSource(settings, client, glossary=glossary)
        asyncio.run(source.load())
        return source, client

    def test_search_before_load_or_while_off_raises_by_setting_name(self):
        source = PublicKnowledgeSource(PublicKnowledgeSettings(enabled=True, project_id="p", project_key="k"), FakeClient())
        with self.assertRaises(KnowledgeUnavailable) as caught:
            asyncio.run(source.search("x", limit=2, context=CONTEXT))
        self.assertEqual(caught.exception.setting, INDEX_SETTING)
        off, _client = self._source([], enabled=False)
        with self.assertRaises(KnowledgeUnavailable) as caught:
            asyncio.run(off.search("x", limit=2, context=CONTEXT))
        self.assertEqual(caught.exception.setting, ENABLED_SETTING)

    def test_hits_come_best_grade_first_within_the_index_order_and_carry_the_label(self):
        docs = [_doc("c1", "C", source_name="Wikipedia (English)", score=0.9),
                _doc("a1", "A", score=0.6), _doc("b1", "B", source_name="OpenStax Chemistry", score=0.7),
                _doc("a2", "A", score=0.5, payload="The words themselves.")]
        source, client = self._source(docs)
        hits = asyncio.run(source.search("acetonitrile flash point", limit=3, context=CONTEXT))
        self.assertEqual([hit.detail("doc_id") for hit in hits], ["a1", "a2", "b1"])
        self.assertEqual([hit.tier for hit in hits], ["public"] * 3)
        self.assertEqual(hits[0].detail("label"), f"{PUBLIC_LABEL} — NIOSH Pocket Guide")
        self.assertEqual(hits[0].detail("grade_words"), "공공기관·표준·심사 논문")
        self.assertEqual(hits[0].source_url, "https://example.org/a1")
        self.assertEqual(hits[0].license, "Public domain")
        self.assertEqual(hits[0].text, "Flash point is low.")
        self.assertEqual(hits[1].text, "The words themselves.")  # payload wins over the searched text
        self.assertEqual(client.queries[0][2], 9)  # three candidates per hit
        self.assertEqual(client.queries[0][3], source.settings.alpha)

    def test_a_korean_question_is_joined_by_the_english_names_before_the_query(self):
        glossary = Glossary(terms={"아세토니트릴": ("acetonitrile",), "인화점": ("flash point",)})
        source, client = self._source([_doc("a1", "A")], glossary=glossary)
        asyncio.run(source.search("아세토니트릴의 인화점이 몇 도야?", limit=2, context=CONTEXT))
        self.assertEqual(client.queries[0][1], "아세토니트릴의 인화점이 몇 도야? acetonitrile flash point")

    def test_an_empty_question_or_zero_limit_queries_nothing(self):
        source, client = self._source([_doc("a1", "A")])
        self.assertEqual(asyncio.run(source.search("   ", limit=2, context=CONTEXT)), [])
        self.assertEqual(asyncio.run(source.search("x", limit=0, context=CONTEXT)), [])
        self.assertEqual(client.queries, [])

    def test_wiki_only_is_true_only_when_every_hit_is_grade_c(self):
        source, _client = self._source([_doc("c1", "C"), _doc("c2", "C")])
        hits = asyncio.run(source.search("x", limit=2, context=CONTEXT))
        self.assertTrue(wiki_only(hits))
        source, _client = self._source([_doc("c1", "C"), _doc("a1", "A")])
        self.assertFalse(wiki_only(asyncio.run(source.search("x", limit=2, context=CONTEXT))))
        self.assertFalse(wiki_only([]))


class GlossaryTests(unittest.TestCase):
    def test_particles_are_taken_off_and_a_prefix_is_found(self):
        glossary = Glossary(terms={"원심분리기": ("centrifuge",), "피펫": ("pipette",), "기준전극": ("reference electrode",)})
        self.assertEqual(glossary.lookup("원심분리기를"), ("centrifuge",))
        self.assertEqual(glossary.lookup("원심분리기에서는"), ("centrifuge",))
        self.assertEqual(glossary.lookup("피펫팅"), ("pipette",))
        self.assertEqual(glossary.lookup("피"), ())
        self.assertEqual(glossary.matches("기준 전극이 이상해"), {"기준 전극": ("reference electrode",)})

    def test_expansion_adds_at_most_two_names_per_word_and_never_repeats(self):
        glossary = Glossary(terms={"아세토니트릴": ("acetonitrile", "methyl cyanide", "ACN"), "용매": ("solvent",)})
        self.assertEqual(glossary.expand("아세토니트릴 용매 acetonitrile"), "아세토니트릴 용매 acetonitrile methyl cyanide solvent")
        self.assertEqual(glossary.expand("no korean here"), "no korean here")
        self.assertEqual(Glossary.empty().expand("아세토니트릴"), "아세토니트릴")

    def test_load_normalises_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "g.json"
            path.write_text(json.dumps({"terms": {"기준 전극": ["reference electrode"], " ": ["x"]},
                                        "aliases": {"Acetonitrile": ["아세토나이트릴"]}}), encoding="utf-8")
            glossary = Glossary.load(path)
        self.assertEqual(glossary.terms, {"기준전극": ("reference electrode",)})
        self.assertEqual(glossary.aliases["Acetonitrile"], ("아세토나이트릴",))


class HelperTests(unittest.TestCase):
    def test_grade_rank_and_labels(self):
        self.assertEqual([grade_rank(g) for g in ("A", "B", "C", "?")], [0, 1, 2, 3])
        self.assertEqual(public_reference_label("NIOSH"), "공개 자료 · 일반 참고 — NIOSH")
        self.assertEqual(public_reference_label(""), "공개 자료 · 일반 참고")

    def test_index_text_puts_title_names_and_locator_before_the_words(self):
        record = {"title": "Acetonitrile", "locator": "Safety", "text": "Flammable."}
        self.assertEqual(index_text_for(record, ("아세토나이트릴", "Acetonitrile")), "Acetonitrile (아세토나이트릴) · Safety\nFlammable.")
        self.assertEqual(index_text_for({"title": "T", "locator": "", "text": "W"}), "T\nW")

    def test_the_sdk_client_names_the_package_when_it_is_missing(self):
        saved = sys.modules.get("moss")
        sys.modules["moss"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises(KnowledgeUnavailable) as caught:
                pk.MossSdkClient("p", "k")
        finally:
            if saved is None:
                del sys.modules["moss"]
            else:
                sys.modules["moss"] = saved
        self.assertEqual(caught.exception.setting, "moss")


if __name__ == "__main__":
    unittest.main()
