"""The default chunk cache is one directory, wherever the process started."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab.chunk_analysis_cache import (
    CACHE_DIRECTORY_ENV,
    ChunkAnalysisCache,
    ChunkCacheKey,
)

ROOT = Path(__file__).resolve().parents[1]


class DefaultChunkCachePathTests(unittest.TestCase):
    def test_default_cache_resolves_to_the_repository_from_any_directory(
        self,
    ) -> None:
        # A relative default resolves against whatever directory the process
        # was started in, so a run from scripts/ would miss every entry a run
        # from the root had paid a provider call for.
        expected = ROOT / "data" / "development_cache" / "chunk_analysis"
        key = ChunkCacheKey(
            source_sha256="0" * 64,
            chunk_id="chunk-1",
            ordinal=1,
            core_page_refs=(1,),
            context_page_refs=(),
            source_revision="revision-1",
        )
        original = Path.cwd()
        self.addCleanup(os.chdir, original)
        with tempfile.TemporaryDirectory() as elsewhere:
            locations = []
            for directory in (ROOT, ROOT / "tests", Path(elsewhere)):
                os.chdir(directory)
                with patch.dict(os.environ, {CACHE_DIRECTORY_ENV: ""}):
                    cache = ChunkAnalysisCache()
                with self.subTest(directory=str(directory)):
                    self.assertTrue(cache.root.is_absolute())
                    self.assertEqual(cache.root.resolve(), expected)
                locations.append(cache.path_for(key).resolve())
            os.chdir(original)
        self.assertEqual(len(set(locations)), 1, locations)


if __name__ == "__main__":
    unittest.main()
