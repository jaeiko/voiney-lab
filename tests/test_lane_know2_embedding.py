"""Lane KNOW2: multilingual embeddings for the public tier (person's decision 2026-10-26, 5).

Moss's built-in models are English. A ``custom`` index holds vectors made by
one pinned multilingual model (``public_knowledge.EMBEDDING_MODEL``, int8
ONNX, MIT), and the server embeds every query with the same model in
process. The model's files are pinned to a commit and checked by SHA-256;
the server refuses an index whose pieces were built with another model. The
Moss client and the embedder are fakes here: contract-tested.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from tests.test_lane_know_public_knowledge import _doc
from voiney_lab import knowledge
from voiney_lab import public_knowledge as pk
from voiney_lab.public_knowledge import (
    EMBEDDING_MODEL,
    INDEX_SETTING,
    KnowledgeUnavailable,
    PublicKnowledgeSettings,
    ensure_model_files,
    start_public_knowledge,
)

CONTEXT = knowledge.KnowledgeContext("lab", "acc", "p1", "r1", "s1", "ko")


class FakeEmbedder:
    def __init__(self, model=EMBEDDING_MODEL) -> None:
        self.model = model
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.5] * self.model.dims


class CustomClient:
    """A Moss client whose index is ``custom`` (or another model)."""

    def __init__(self, docs, *, model: str = "custom") -> None:
        self.docs = list(docs)
        self.model = model
        self.queries: list[dict] = []
        self.closed = False

    async def load(self, index_name: str) -> None:
        pass

    async def index_model(self, index_name: str) -> str:
        return self.model

    async def query(self, index_name: str, text: str, *, top_k: int, alpha: float, embedding=None):
        self.queries.append({"text": text, "top_k": top_k, "alpha": alpha, "embedding": embedding})
        return self.docs[:top_k]

    async def close(self) -> None:
        self.closed = True


def _built_with(key: str, **kwargs):
    document = _doc("d1", "A", **kwargs)
    return replace(document, metadata={**document.metadata, "embedding": key})


SETTINGS = PublicKnowledgeSettings(enabled=True, project_id="p", project_key="k", index_name="idx",
                                   cache_dir="/unused/cache")


class StartTests(unittest.TestCase):
    def _start(self, client, embedder=None):
        return asyncio.run(start_public_knowledge(
            SETTINGS, client_factory=lambda s: client, embedder_factory=lambda model: embedder or FakeEmbedder(model)))

    def test_a_custom_index_gets_the_embedder_and_every_query_its_vector(self) -> None:
        client = CustomClient([_built_with(EMBEDDING_MODEL.key)])
        source = self._start(client)
        assert source is not None
        self.assertEqual(source.embedding, EMBEDDING_MODEL.key)
        self.assertEqual(source.status()["embedding"], EMBEDDING_MODEL.key)
        asyncio.run(source.search("아세토니트릴 인화점", limit=2, context=CONTEXT))
        last = client.queries[-1]
        self.assertEqual(len(last["embedding"]), EMBEDDING_MODEL.dims)
        self.assertEqual(last["alpha"], SETTINGS.alpha)
        self.assertEqual(source.score_floor, pk.SCORE_FLOORS[EMBEDDING_MODEL.key])

    def test_an_index_built_with_another_model_leaves_the_tier_off_by_name(self) -> None:
        client = CustomClient([_built_with("another-model@0123456789ab")])
        with self.assertRaises(KnowledgeUnavailable) as caught:
            self._start(client)
        self.assertEqual(caught.exception.setting, INDEX_SETTING)
        self.assertIn("another embedding model", str(caught.exception))
        self.assertTrue(client.closed)

    def test_a_moss_model_index_is_queried_without_a_vector(self) -> None:
        client = CustomClient([_doc("d1", "A")], model="moss-minilm")
        source = self._start(client)
        assert source is not None
        self.assertIsNone(source.embedding)
        asyncio.run(source.search("acetonitrile", limit=2, context=CONTEXT))
        self.assertIsNone(client.queries[-1]["embedding"])
        self.assertEqual(source.score_floor, pk.SCORE_FLOOR)

    def test_an_embedder_that_cannot_be_built_leaves_the_tier_off_by_name(self) -> None:
        def broken(model):
            raise KnowledgeUnavailable("onnxruntime", "not installed")

        client = CustomClient([_built_with(EMBEDDING_MODEL.key)])
        with self.assertRaises(KnowledgeUnavailable) as caught:
            asyncio.run(start_public_knowledge(SETTINGS, client_factory=lambda s: client, embedder_factory=broken))
        self.assertEqual(caught.exception.setting, "onnxruntime")
        self.assertTrue(client.closed)

    def test_the_problem_search_carries_the_vector_of_its_own_query(self) -> None:
        embedder = FakeEmbedder()
        client = CustomClient([_built_with(EMBEDDING_MODEL.key, title="Acetonitrile",
                                           text="Shaking acetonitrile causes foam.", score=0.9)])
        source = self._start(client, embedder)
        query = pk.problem_query("acetonitrile 거품이 생겼어", "2 Wash the gel piece.", source.glossary)
        assert query is not None
        asyncio.run(source.for_problem(query).search("acetonitrile 거품이 생겼어", limit=2, context=CONTEXT))
        self.assertEqual(embedder.queries[-1], query.text)
        self.assertIsNotNone(client.queries[-1]["embedding"])


class ModelFileTests(unittest.TestCase):
    def _model(self, content: bytes):
        digest = hashlib.sha256(content).hexdigest()
        return replace(EMBEDDING_MODEL, files={"model": ("onnx/m.onnx", digest), "tokenizer": ("t.json", digest)})

    def test_files_are_fetched_from_the_pinned_commit_once_and_checked(self) -> None:
        content = b"model bytes"
        model = self._model(content)
        fetched: list[str] = []

        def fetch(url: str, path: Path) -> None:
            fetched.append(url)
            path.write_bytes(content)

        with tempfile.TemporaryDirectory() as temporary:
            paths = ensure_model_files(model, temporary, fetch=fetch)
            again = ensure_model_files(model, temporary, fetch=fetch)
        self.assertEqual(paths, again)
        self.assertEqual(len(fetched), 2)
        self.assertIn(f"/resolve/{EMBEDDING_MODEL.revision}/onnx/m.onnx", fetched[0])

    def test_a_file_that_differs_leaves_the_tier_off_by_name(self) -> None:
        model = self._model(b"expected")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(KnowledgeUnavailable) as caught:
                ensure_model_files(model, temporary, fetch=lambda url, path: path.write_bytes(b"other"))
        self.assertEqual(caught.exception.setting, INDEX_SETTING)

    def test_the_pinned_models_have_a_permitted_licence_and_a_commit(self) -> None:
        for model in pk.EMBEDDING_MODELS.values():
            with self.subTest(model=model.name):
                self.assertIn(model.licence, {"MIT", "Apache-2.0", "BSD-3-Clause"})
                self.assertRegex(model.revision, r"^[0-9a-f]{40}$")
                for _path, digest in model.files.values():
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")


class MossClientTests(unittest.TestCase):
    def test_the_vector_goes_into_the_query_options(self) -> None:
        seen: list[dict] = []

        class Options:
            def __init__(self, **kwargs):
                seen.append(kwargs)

        class Client:
            async def query(self, name, text, options):
                return SimpleNamespace(docs=[])

            async def get_index(self, name):
                return SimpleNamespace(model=SimpleNamespace(id="custom"))

        client = pk.MossSdkClient.__new__(pk.MossSdkClient)
        client._moss = SimpleNamespace(QueryOptions=Options, MossError=RuntimeError)
        client._client = Client()
        asyncio.run(client.query("idx", "q", top_k=3, alpha=0.5, embedding=(0.1, 0.2)))
        asyncio.run(client.query("idx", "q", top_k=3, alpha=0.5))
        self.assertEqual(seen, [{"top_k": 3, "alpha": 0.5, "embedding": [0.1, 0.2]}, {"top_k": 3, "alpha": 0.5}])
        self.assertEqual(asyncio.run(client.index_model("idx")), "custom")


class OfflineHybridTests(unittest.TestCase):
    """The audit's ``local`` backend: a custom index approximated offline."""

    def test_vector_and_keyword_ranks_are_fused_and_the_score_is_the_cosine(self) -> None:
        import numpy as np

        from tests.test_lane_kaudit_public_knowledge_audit import audit
        from tests.test_lane_know2_pipeline import RECORDS, pk as pipeline

        model = EMBEDDING_MODEL
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            texts = [doc["text"] for doc in pipeline.documents_for(RECORDS, pk.Glossary.empty())]
            vectors = np.zeros((2, model.dims), dtype=np.float32)
            vectors[0, 0], vectors[1, 1] = 1.0, 1.0
            pipeline.EmbeddingCache(runtime, model).add(texts, vectors)
            client = audit.HybridBundleClient(RECORDS, pk.Glossary.empty(), model, runtime)
            asyncio.run(client.load("idx"))
            query = [0.0] * model.dims
            query[1] = 1.0
            by_vector = asyncio.run(client.query("idx", "Flash point", top_k=2, alpha=1.0, embedding=query))
            by_keyword = asyncio.run(client.query("idx", "Flash point", top_k=2, alpha=0.0, embedding=query))
        self.assertEqual([doc.id for doc in by_vector], ["a:2", "a:1"])
        self.assertEqual([doc.score for doc in by_vector], [1.0, 0.0])
        self.assertEqual(by_keyword[0].id, "a:1")
        self.assertEqual(by_vector[0].metadata["embedding"], model.key)


if __name__ == "__main__":
    unittest.main()
