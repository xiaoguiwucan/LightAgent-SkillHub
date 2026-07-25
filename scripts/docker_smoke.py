#!/usr/bin/env python3
"""Read-only smoke test intended to run as the official Docker non-root user."""

import os
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
    print(f"Docker non-root smoke passed: {len(packages)} packages")


if __name__ == "__main__":
    main()
