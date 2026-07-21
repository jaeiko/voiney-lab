import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from tools import SEARCH_TOOL, execute_tool, search_approved_safety_manual

class ToolTests(unittest.TestCase):
    def test_exact_schema(self):
        function=SEARCH_TOOL["function"]; schema=function["parameters"]
        self.assertEqual(function["name"],"search_approved_safety_manual")
        self.assertEqual(schema["required"],["query","language"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["language"]["enum"],["ko","vi"])
    def test_bilingual_success_and_safe_shape(self):
        for query,language in (("화학 누출","ko"),("máy thiết bị","vi")):
            result=search_approved_safety_manual(query,language)
            self.assertEqual(result["status"],"success")
            self.assertLessEqual(len(result["matches"]),3)
            self.assertEqual(set(result["matches"][0]),{"document_id","title","section","guidance","source_label","demo_only"})
            self.assertTrue(result["matches"][0]["demo_only"])
    def test_miss_invalid_unknown_and_error_are_structured(self):
        self.assertEqual(search_approved_safety_manual("zzzzzz","ko")["status"],"not_found")
        self.assertEqual(search_approved_safety_manual("","ko")["status"],"invalid_arguments")
        self.assertEqual(execute_tool("bad",{})["status"],"invalid_arguments")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(search_approved_safety_manual("누출","ko",Path(directory)/"missing")["status"],"error")
    def test_no_network_and_no_invented_numeric_guidance(self):
        with patch("requests.get",side_effect=AssertionError("network")):
            result=search_approved_safety_manual("화학 누출","ko")
        blob=json.dumps(result,ensure_ascii=False)
        self.assertNotRegex(blob,r"\\d")
        self.assertNotIn("secret",blob.lower())

if __name__=="__main__": unittest.main()
