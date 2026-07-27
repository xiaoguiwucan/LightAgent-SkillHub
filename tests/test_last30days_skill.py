import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "last30days"
ENTRY = SKILL / "scripts" / "lightagent_entry.py"
sys.path.insert(0, str(ROOT / "scripts"))

from common import parse_skill  # noqa: E402


class Last30DaysSkillTest(unittest.TestCase):
    def _run(self, *arguments, test_mode=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        env = os.environ.copy()
        env.update({
            "LIGHTAGENT_SKILL_DATA": str(base / "data"),
            "LIGHTAGENT_SKILL_CONFIG": str(base / "config"),
        })
        if test_mode:
            env["LAST30DAYS_ADAPTER_TEST"] = "1"
        process = subprocess.run(
            [sys.executable, str(ENTRY), *arguments],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        return process, base

    def test_metadata_declares_runner_and_upstream_version(self):
        metadata, text = parse_skill(SKILL / "SKILL.md")
        self.assertEqual("3.18.3", metadata["version"])
        self.assertEqual("MIT", metadata["license"])
        self.assertEqual("skill_run", metadata["lightagent"]["tools"][0])
        self.assertEqual(3, len(metadata["lightagent"]["entrypoints"]))
        self.assertIn("mvanhorn/last30days-skill v3.18.3", text)
        self.assertTrue((SKILL / "LICENSE").is_file())

    def test_mock_research_runs_and_writes_only_skill_data(self):
        process, base = self._run(
            "research", "LightAgent", "--mock", "--quick", test_mode=True
        )
        self.assertEqual(0, process.returncode, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual("LightAgent", payload["query"])
        self.assertEqual(30, payload["window_days"])
        reports = list((base / "data" / "research").glob("*.json"))
        self.assertEqual(1, len(reports))
        self.assertFalse((base / "Documents").exists())

    def test_mock_and_publication_flags_are_rejected(self):
        mock, _ = self._run("research", "LightAgent", "--mock")
        self.assertEqual(2, mock.returncode)
        self.assertEqual("mock_mode_disabled", json.loads(mock.stdout)["error"])

        publish, _ = self._run("research", "LightAgent", "--publish")
        self.assertEqual(2, publish.returncode)
        self.assertEqual("unsupported_flag:--publish", json.loads(publish.stdout)["error"])

        output, _ = self._run("research", "LightAgent", "--output=/tmp/result.json")
        self.assertEqual(2, output.returncode)
        self.assertEqual("unsupported_flag:--output", json.loads(output.stdout)["error"])

    def test_plan_must_be_inline_json_object(self):
        process, _ = self._run("research", "LightAgent", "--plan=/tmp/plan.json")
        self.assertEqual(2, process.returncode)
        self.assertIn("Expecting value", json.loads(process.stdout)["error"])

    def test_missing_lightagent_paths_is_rejected(self):
        env = os.environ.copy()
        env.pop("LIGHTAGENT_SKILL_DATA", None)
        env.pop("LIGHTAGENT_SKILL_CONFIG", None)
        process = subprocess.run(
            [sys.executable, str(ENTRY), "doctor"],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(2, process.returncode)
        self.assertEqual(
            "missing_lightagent_skill_paths", json.loads(process.stdout)["error"]
        )


if __name__ == "__main__":
    unittest.main()
