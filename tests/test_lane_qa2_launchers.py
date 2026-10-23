"""Effective launcher settings remain explicit while user values still win."""
from tests import test_launcher_settings_win as support


class DevCapabilityTests(support._LauncherCopy):
    SCRIPT = "run_dev.sh"

    def test_copied_template_flags_are_visible_with_an_actionable_notice(self):
        self._dotenv("VOINEY_LAB_PROTOCOL_ENABLED=false\nVOINEY_LAB_WORKSPACE_ENABLED=false\n")
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("protocol_catalog: disabled", result.stdout)
        self.assertIn("workspace: disabled", result.stdout)
        self.assertIn("VOINEY_LAB_PROTOCOL_ENABLED=true", result.stdout)
        self.assertIn("pdf_worker_timeout_seconds:", result.stdout)


class PilotCapabilityTests(support._LauncherCopy):
    SCRIPT = "run_pilot.sh"

    def test_router_and_pdf_bound_are_printed_from_the_effective_settings(self):
        self._dotenv("VOINEY_LAB_LLM_ROUTER_ENABLED=false\nVOINEY_LAB_PDF_WORKER_TIMEOUT_SECONDS=123\n")
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("llm_router: disabled", result.stdout)
        self.assertIn("router_first: disabled", result.stdout)
        self.assertIn("pdf_worker_timeout_seconds: 123", result.stdout)
