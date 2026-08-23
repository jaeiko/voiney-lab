import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.procedure_definitions import load_procedure_definitions

ROOT=Path(__file__).resolve().parents[1]


class ProcedureDemoTests(unittest.TestCase):
    def test_setup_creates_fresh_validated_non_operational_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=subprocess.run(
                [sys.executable,
                 str(ROOT/"scripts"/"setup_procedure_demo.py"),
                 "--output-dir",temporary],
                cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            catalog=Path(temporary)/"approved_catalog.sqlite"
            definitions=load_procedure_definitions(
                ROOT/"data"/"procedure_demo"/"procedures.ko.json",catalog,
                facility_id="DEMO-FACILITY",language="ko",usage_scope="test_only")
            definition=definitions["fictional-wet-lab-workflow-demo-ko"]
            self.assertIn("FICTIONAL NON-OPERATIONAL",definition.title)
            self.assertEqual(len(definition.steps),3)
            self.assertTrue(definition.steps[0].observation_schema["required"])
            self.assertEqual(definition.steps[1].timer["duration_seconds"],10)
            self.assertTrue(definition.steps[2].observation_schema["required"])


if __name__=="__main__":
    unittest.main()
