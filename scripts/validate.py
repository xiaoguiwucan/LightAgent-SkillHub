#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator
from license_expression import get_spdx_licensing
from packaging.version import InvalidVersion, Version

from common import FORBIDDEN_PATTERNS, RESERVED_NAMES, ROOT, parse_skill


DIRECT_SCRIPT_EXECUTION = re.compile(
    r"(?im)(?:^|[`\s;&|])(?:python(?:3)?|node|bash|sh)\s+[^\n`]*(?:<base_dir>|scripts[/\\])"
)
RESERVED_RUNNER_ENV = {
    "HOME", "PATH", "PYTHONHOME", "PYTHONPATH", "NODE_PATH", "TMP", "TEMP", "TMPDIR",
}


def main():
    schema = json.loads((ROOT / "schemas/skill.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    spdx = get_spdx_licensing()
    errors = []
    seen = {}
    skill_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
    try:
        revocations = json.loads((ROOT / "revocations.json").read_text(encoding="utf-8"))
        if not isinstance(revocations, list):
            raise ValueError("必须是数组")
        for item in revocations:
            if not isinstance(item, dict) or item.get("status") not in ("yanked", "revoked"):
                errors.append("revocations.json: 每项必须包含 name/version 及 yanked/revoked 状态")
            elif not item.get("name") or not item.get("version") or not item.get("reason"):
                errors.append("revocations.json: name、version 和 reason 必填")
    except Exception as exc:
        errors.append(f"revocations.json: {exc}")
    if not skill_files:
        errors.append("skills/ 下至少需要一个技能")

    for path in skill_files:
        rel = path.relative_to(ROOT)
        try:
            meta, text = parse_skill(path)
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
            continue
        name = str(meta.get("name", ""))
        if path.parent.name != name:
            errors.append(f"{rel}: 目录名必须与 name 一致")
        if name in RESERVED_NAMES or name.startswith("lightagent-") and meta.get("publisher") != "official":
            errors.append(f"{rel}: 使用了受保护名称 {name}")
        if name in seen:
            errors.append(f"{rel}: 技能名与 {seen[name]} 重复")
        seen[name] = rel
        for issue in sorted(validator.iter_errors(meta), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in issue.path) or "frontmatter"
            errors.append(f"{rel}: {location}: {issue.message}")
        license_info = spdx.validate(str(meta.get("license", "")))
        if license_info.errors:
            errors.append(f"{rel}: license 必须是有效 SPDX 表达式: {'; '.join(license_info.errors)}")
        try:
            minimum = Version(str(meta.get("min_lightagent_version")))
            maximum = meta.get("max_lightagent_version")
            if maximum is not None and minimum > Version(str(maximum)):
                errors.append(f"{rel}: max_lightagent_version 小于最低版本")
        except InvalidVersion as exc:
            errors.append(f"{rel}: LightAgent 版本无效: {exc}")
        declared_domains = set(meta.get("lightagent", {}).get("network_domains", []))
        capabilities = meta.get("requirements", {}).get("capabilities", [])
        if len(capabilities) != len(set(capabilities)):
            errors.append(f"{rel}: requirements.capabilities 不得重复")
        for env_name in meta.get("requirements", {}).get("env", []):
            if env_name in RESERVED_RUNNER_ENV or str(env_name).startswith("LIGHTAGENT_"):
                errors.append(f"{rel}: 不得声明 Runner 保留环境变量 {env_name}")
        for package in meta.get("requirements", {}).get("python", []):
            if str(package).startswith("-") or "://" in str(package):
                errors.append(f"{rel}: Python 依赖只能使用包名与版本约束")
        for package in meta.get("requirements", {}).get("npm", []):
            if str(package).startswith("-") or "://" in str(package):
                errors.append(f"{rel}: npm 依赖只能使用包名与版本约束")
        for download in meta.get("requirements", {}).get("downloads", []):
            host = urlparse(download.get("url", "")).hostname
            if host and host not in declared_domains:
                errors.append(f"{rel}: 下载域名 {host} 未在 network_domains 声明")
        for label, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{rel}: {label}")
        entrypoints = meta.get("lightagent", {}).get("entrypoints", [])
        scripts = [item for item in (path.parent / "scripts").rglob("*") if item.is_file()] if (path.parent / "scripts").is_dir() else []
        if meta.get("schema_version") == 2 and scripts and not entrypoints:
            errors.append(f"{rel}: Schema v2 脚本技能必须声明 lightagent.entrypoints")
        if meta.get("schema_version") == 2 and scripts and DIRECT_SCRIPT_EXECUTION.search(text):
            errors.append(f"{rel}: Schema v2 禁止要求通过 Bash/解释器直接执行技能脚本，请使用 skill_run")
        names = set()
        for entrypoint in entrypoints:
            entry_name = str(entrypoint.get("name") or "")
            if entry_name in names:
                errors.append(f"{rel}: entrypoint 名称重复: {entry_name}")
            names.add(entry_name)
            entry_path = path.parent / str(entrypoint.get("path") or "")
            try:
                entry_path.resolve().relative_to(path.parent.resolve())
            except ValueError:
                errors.append(f"{rel}: entrypoint 路径越界: {entrypoint.get('path')}")
                continue
            if not entry_path.is_file() or entry_path.is_symlink():
                errors.append(f"{rel}: entrypoint 文件不存在或为符号链接: {entrypoint.get('path')}")
            arguments = entrypoint.get("arguments") or {}
            if int(arguments.get("min_items", 0)) > int(arguments.get("max_items", 0)):
                errors.append(f"{rel}: entrypoint {entry_name} 的 min_items 不能大于 max_items")
        for file_path in path.parent.rglob("*"):
            if file_path.is_symlink():
                errors.append(f"{file_path.relative_to(ROOT)}: 不允许符号链接")
            if file_path.is_file() and file_path.stat().st_size > 5 * 1024 * 1024:
                errors.append(f"{file_path.relative_to(ROOT)}: 单文件不得超过 5 MiB")
            if file_path.is_file() and file_path.suffix == ".py":
                try:
                    compile(file_path.read_text(encoding="utf-8"), str(file_path), "exec")
                except SyntaxError as exc:
                    errors.append(f"{file_path.relative_to(ROOT)}: Python 语法错误: {exc}")
        evaluation = ROOT / "evaluations" / name / "cases.json"
        if evaluation.exists():
            try:
                cases = json.loads(evaluation.read_text(encoding="utf-8"))
                if not isinstance(cases, list) or not cases:
                    raise ValueError("至少需要一个用例")
                for case in cases:
                    if not isinstance(case, dict) or not case.get("name") or not case.get("prompt"):
                        raise ValueError("每个用例必须包含 name 和 prompt")
            except Exception as exc:
                errors.append(f"{evaluation.relative_to(ROOT)}: {exc}")

    if errors:
        print("校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"校验通过：{len(skill_files)} 个技能")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
