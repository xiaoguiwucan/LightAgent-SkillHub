import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "av-meta" / "scripts" / "fetch_meta.py"
SKILL = SCRIPT.parent.parent / "SKILL.md"
SPEC = importlib.util.spec_from_file_location("av_meta_fetch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class AvMetaFetchTest(unittest.TestCase):
    def test_skill_metadata_requires_script_path_for_explicit_code(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("version: 1.0.2", text)
        self.assertIn("必须读取本技能并执行 scripts/fetch_meta.py", text)
        self.assertIn("禁止改用 browser、web_fetch", text)

    def test_normalize_code_accepts_explicit_code_only(self):
        self.assertEqual("SSIS-001", MODULE.normalize_code("ssis_001"))
        self.assertEqual("IPX-177", MODULE.normalize_code("请查 IPX177"))
        self.assertEqual("", MODULE.normalize_code("刚才那个有图吗"))

    def test_external_url_allowlist(self):
        self.assertEqual(
            "https://www.javbus.com/SSIS-001",
            MODULE.validate_external_url("https://www.javbus.com/SSIS-001"),
        )
        for url in (
            "http://www.javbus.com/SSIS-001",
            "https://example.com/SSIS-001",
            "https://user@www.javbus.com/SSIS-001",
            "https://www.javbus.com:8443/SSIS-001",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                MODULE.validate_external_url(url)

    def test_cover_path_must_stay_inside_output_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            outside = Path(temp) / "outside"
            root.mkdir()
            outside.mkdir()
            target = MODULE.safe_output_path(
                str(root), str(root / "images" / "av-meta" / "SSIS-001.jpg"), "SSIS-001"
            )
            self.assertEqual(
                Path(target).parent.parent.parent.resolve(),
                root.resolve(),
            )
            with self.assertRaisesRegex(ValueError, "cover_path_outside_output_root"):
                MODULE.safe_output_path(
                    str(root), str(outside / "SSIS-001.jpg"), "SSIS-001"
                )
            (root / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "cover_path_outside_output_root"):
                MODULE.safe_output_path(
                    str(root), str(root / "linked" / "SSIS-001.jpg"), "SSIS-001"
                )

    def test_magnet_parser_deduplicates_hashes(self):
        html = """
        <table>
          <tr><td><a href="magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&dn=one">one 高清 字幕</a></td><td><a>2.5GB</a></td><td><a>2026-01-01</a></td></tr>
          <tr><td><a href="magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&dn=duplicate">duplicate</a></td></tr>
        </table>
        """
        magnets = MODULE.parse_magnets_html(html)
        self.assertEqual(1, len(magnets))
        self.assertEqual(["HD", "SUB"], magnets[0]["tags"])

    def test_cli_rejects_invalid_code_without_network(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = MODULE.main(["没有番号"])
        self.assertEqual(2, status)
        self.assertEqual("invalid_code", json.loads(output.getvalue())["error"])

    def test_cli_rejects_undeclared_source(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = MODULE.main(
                ["SSIS-001", "--javbus-base", "https://example.com"]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(2, status)
        self.assertEqual("invalid_source", payload["error"])


if __name__ == "__main__":
    unittest.main()
