#!/usr/bin/env python3
import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from common import ROOT, canonical_json, parse_skill, sha256_bytes


def git_value(*args, default="unknown"):
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return default


def deterministic_zip(source: Path):
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
        temp_name = handle.name
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(source.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                info = zipfile.ZipInfo(str(Path(source.name) / path.relative_to(source)))
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.external_attr = (0o755 if os.access(path, os.X_OK) else 0o644) << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        return Path(temp_name).read_bytes()
    finally:
        Path(temp_name).unlink(missing_ok=True)


def sign(payload: bytes):
    encoded = os.environ.get("SKILL_HUB_SIGNING_KEY", "").strip()
    if not encoded:
        return None
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = base64.b64decode(encoded)
    key = Ed25519PrivateKey.from_private_bytes(raw)
    return base64.b64encode(key.sign(payload)).decode("ascii")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist")
    parser.add_argument("--base-url", default="https://xiaoguiwucan.github.io/LightAgent-SkillHub")
    parser.add_argument("--require-signature", action="store_true")
    args = parser.parse_args()
    output = ROOT / args.output
    if output.exists():
        shutil.rmtree(output)
    (output / "packages").mkdir(parents=True)
    shutil.copytree(ROOT / "site", output, dirs_exist_ok=True)

    commit = git_value("rev-parse", "HEAD")
    skills = []
    for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
        meta, _ = parse_skill(skill_md)
        package = deterministic_zip(skill_md.parent)
        package_name = f"{meta['name']}-{meta['version']}.zip"
        (output / "packages" / package_name).write_bytes(package)
        entry = dict(meta)
        entry.setdefault("schema_version", 1)
        entry.setdefault("release_notes", "")
        entry.setdefault("breaking_changes", [])
        entry.setdefault("requirements", {}).setdefault("capabilities", [])
        entry.update({
            "source_commit": commit,
            "download_url": f"{args.base_url.rstrip('/')}/packages/{package_name}",
            "sha256": sha256_bytes(package),
        })
        skills.append(entry)

    registry = {
        "registry_version": 2,
        "repository": "https://github.com/xiaoguiwucan/LightAgent-SkillHub",
        "source_commit": commit,
        "skills": skills,
        "revocations": json.loads((ROOT / "revocations.json").read_text(encoding="utf-8")),
    }
    payload = canonical_json(registry)
    signature = sign(payload)
    if args.require_signature and not signature:
        raise SystemExit("发布要求设置 SKILL_HUB_SIGNING_KEY")
    document = dict(registry)
    document["signature"] = {"algorithm": "ed25519", "key_id": "lightagent-skillhub-2026-01", "value": signature} if signature else None
    (output / "registry.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "registry.sha256").write_text(sha256_bytes(payload) + "\n", encoding="ascii")
    print(f"已生成 {len(skills)} 个技能：{output}")


if __name__ == "__main__":
    main()
