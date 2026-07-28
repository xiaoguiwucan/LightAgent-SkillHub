import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_registry import deterministic_zip  # noqa: E402
from common import parse_skill  # noqa: E402


class RegistryV2Test(unittest.TestCase):
    def test_v2_script_skill_declares_runner_entrypoint(self):
        metadata, text = parse_skill(ROOT / "skills" / "av-meta" / "SKILL.md")
        self.assertEqual(2, metadata["schema_version"])
        self.assertEqual("skill_run", metadata["lightagent"]["tools"][0])
        self.assertNotIn("```bash", text)
        entrypoint = metadata["lightagent"]["entrypoints"][0]
        self.assertEqual("scripts/fetch_meta.py", entrypoint["path"])
        self.assertTrue((ROOT / "skills" / "av-meta" / entrypoint["path"]).is_file())

    def test_deterministic_package_contains_runner_metadata(self):
        package = deterministic_zip(ROOT / "skills" / "av-meta")
        self.assertGreater(len(package), 100)

    def test_schema_exposes_capabilities_and_change_notes(self):
        schema = json.loads((ROOT / "schemas" / "skill.schema.json").read_text(encoding="utf-8"))
        self.assertIn("release_notes", schema["properties"])
        self.assertIn("capabilities", schema["properties"]["requirements"]["properties"])
        self.assertIn("entrypoints", schema["properties"]["lightagent"]["properties"])

    def test_schema_restricts_runner_environment_names(self):
        schema = json.loads((ROOT / "schemas" / "skill.schema.json").read_text(encoding="utf-8"))
        env_schema = schema["properties"]["requirements"]["properties"]["env"]
        self.assertTrue(env_schema["uniqueItems"])
        self.assertEqual("^[A-Z_][A-Z0-9_]{0,127}$", env_schema["items"]["pattern"])


if __name__ == "__main__":
    unittest.main()
