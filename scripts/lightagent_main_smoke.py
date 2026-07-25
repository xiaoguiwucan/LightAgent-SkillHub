#!/usr/bin/env python3
"""Verify Hub manifests with the parser from the current LightAgent main branch."""

import importlib.util
import sys
from pathlib import Path


def _lightagent_parser():
    sys.modules.pop("common", None)
    sys.path = [entry for entry in sys.path if entry != "/hub/scripts"]
    sys.path.insert(0, "/lightagent-main")
    path = Path("/lightagent-main/agent/skills/frontmatter.py")
    spec = importlib.util.spec_from_file_location("lightagent_frontmatter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_frontmatter


def main():
    parse_frontmatter = _lightagent_parser()
    manifests = sorted(Path("/hub/skills").glob("*/SKILL.md"))
    if not manifests:
        raise SystemExit("No Hub skills found")
    for manifest in manifests:
        metadata = parse_frontmatter(manifest.read_text(encoding="utf-8"))
        if metadata.get("name") != manifest.parent.name:
            raise SystemExit(f"{manifest}: LightAgent parsed a mismatched name")
    print(f"LightAgent main compatibility passed: {len(manifests)} skills")


if __name__ == "__main__":
    main()
