"""Lane DOC3: the xAI web reference search is gone, the general explanation is not.

The search (``XaiAuthoritativeWebSearch``, ``ExternalReferenceSettings`` and the
thirteen settings that went with them) was off by default. These tests pin what
the deletion must leave behind: no name of it in the code, the table or the
launchers; no capability entry for it (lane FX3 took the screen's away); and the
"AI 일반 지식" side of ``external_references`` still importable and working.
Nothing here calls a provider.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from voiney_lab import external_references, server
from voiney_lab.setting_names import BY_NAME

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "voiney_lab"

#: The names are spelled in pieces so this file does not trip the setting-name
#: scan (a deleted setting must not appear with its prefix in a test).
DELETED_SETTING_WORDS = re.compile(r"EXTERNAL_REFERENCE|EXTERNAL_SEARCH|MOSS_ENABLED")
DELETED_CLASSES = ("XaiAuthoritativeWebSearch", "ExternalReferenceSettings")


class TheSearchIsGoneTests(unittest.TestCase):
    def test_the_classes_are_not_in_the_module_or_the_server(self) -> None:
        for name in DELETED_CLASSES:
            with self.subTest(name=name):
                self.assertFalse(hasattr(external_references, name))
                self.assertFalse(hasattr(server, name))

    def test_no_python_source_names_the_deleted_classes_or_the_tool(self) -> None:
        for path in sorted(SRC.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for word in (*DELETED_CLASSES, "search_authoritative_web"):
                with self.subTest(file=path.name, word=word):
                    self.assertNotIn(word, text)

    def test_the_table_and_the_launchers_name_none_of_the_deleted_settings(self) -> None:
        self.assertEqual(
            sorted(name for name in BY_NAME if DELETED_SETTING_WORDS.search(name)), [])
        for script in ("run_dev.sh", "run_pilot.sh", "run_ci_server.sh"):
            with self.subTest(script=script):
                text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
                self.assertIsNone(DELETED_SETTING_WORDS.search(text))
        self.assertFalse((ROOT / "scripts" / "diagnose_candidate_a_research.py").exists())


class TheGeneralExplanationIsKeptTests(unittest.TestCase):
    def test_the_explanation_side_of_the_module_is_still_there(self) -> None:
        for name in (
            "SupplementalKnowledgeSettings", "XaiSupplementalKnowledge",
            "supplemental_knowledge_allowed", "plan_research_query",
            "supplemental_explanation_query",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(external_references, name))
        self.assertTrue(hasattr(
            external_references.XaiSupplementalKnowledge, "explain_outside_pdf"))

    def test_the_supplemental_role_settings_the_explanation_reads_are_still_in_the_table(self) -> None:
        for name in (
            "VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED",
            "VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS",
            "VOINEY_LAB_SUPPLEMENTAL_PROVIDER", "VOINEY_LAB_SUPPLEMENTAL_MODEL",
            "VOINEY_LAB_SUPPLEMENTAL_REASONING",
        ):
            with self.subTest(name=name):
                self.assertIn(name, BY_NAME)

    def test_a_session_has_no_web_search_settings_and_the_screen_entry_stays_disabled(self) -> None:
        session = server.ListenerSession()
        self.assertFalse(hasattr(session, "external_reference_settings"))
        # Lane FX3, decision 3: the screen stopped reading the entry, so the
        # server stopped sending it.
        source = (SRC / "server.py").read_text(encoding="utf-8")
        self.assertNotIn('"external_text"', source)


if __name__ == "__main__":
    unittest.main()
