#!/usr/bin/env python3
"""Read-only smoke test intended to run as the official Docker non-root user."""

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def main():
    if os.geteuid() == 0:
        raise SystemExit("Docker smoke test must run as a non-root user")
    packages = sorted(Path("/hub/dist/packages").glob("*.zip"))
    if not packages:
        raise SystemExit("No skill packages found")
    for package in packages:
        with tempfile.TemporaryDirectory() as target:
            with zipfile.ZipFile(package) as archive:
                archive.extractall(target)
            manifests = list(Path(target).rglob("SKILL.md"))
            if len(manifests) != 1:
                raise SystemExit(f"{package.name}: expected exactly one SKILL.md")
            manifests[0].read_text(encoding="utf-8")
            if manifests[0].parent.name == "last30days":
                skill_root = manifests[0].parent
                data_dir = Path(target, "last30days-data")
                config_dir = Path(target, "last30days-config")
                env = os.environ.copy()
                env.update({
                    "LIGHTAGENT_SKILL_DATA": str(data_dir),
                    "LIGHTAGENT_SKILL_CONFIG": str(config_dir),
                    "LAST30DAYS_ADAPTER_TEST": "1",
                })
                result = subprocess.run(
                    [
                        sys.executable,
                        str(skill_root / "scripts" / "lightagent_entry.py"),
                        "research",
                        "LightAgent",
                        "--mock",
                        "--quick",
                    ],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    raise SystemExit(
                        f"{package.name}: last30days mock failed: {result.stderr}"
                    )
                payload = json.loads(result.stdout)
                if payload.get("query") != "LightAgent":
                    raise SystemExit(f"{package.name}: unexpected mock output")
    print(f"Docker non-root smoke passed: {len(packages)} packages")


if __name__ == "__main__":
    main()
