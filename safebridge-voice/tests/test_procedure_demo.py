import subprocess
import tempfile
import unittest
from pathlib import Path

from safebridge_voice.procedure_definitions import load_procedure_definitions

ROOT=Path(__file__).resolve().parents[1]


class ProcedureDemoTests(unittest.TestCase):
    def test_setup_creates_fresh_validated_non_operational_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=subprocess.run(
                [str(ROOT/".venv"/"bin"/"python"),
                 str(ROOT/"scripts"/"setup_procedure_demo.py"),
                 "--output-dir",temporary],
                cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            catalog=Path(temporary)/"approved_catalog.sqlite"
            definitions=load_procedure_definitions(
                ROOT/"data"/"procedure_demo"/"procedures.ko.json",catalog,
                facility_id="DEMO-FACILITY",language="ko",usage_scope="test_only")
            definition=definitions["fictional-color-card-demo-ko"]
            self.assertIn("FICTIONAL NON-OPERATIONAL",definition.title)
            self.assertEqual(len(definition.steps),3)


if __name__=="__main__":
    unittest.main()
