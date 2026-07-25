#!/usr/bin/env python3
import hashlib
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RESERVED_NAMES = {"image-generation", "knowledge-wiki", "skill-creator"}
FORBIDDEN_PATTERNS = {
    "疑似硬编码密钥": re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"),
    "疑似绕过确认": re.compile(r"(?i)(bypass|skip|ignore).{0,30}(approval|confirmation|safety)"),
    "疑似窃取凭据": re.compile(r"(?i)(exfiltrat|steal|upload).{0,40}(secret|credential|token|key)"),
}


def parse_skill(path: Path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not match:
        raise ValueError("缺少 YAML Frontmatter")
    data = yaml.safe_load(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("Frontmatter 必须是对象")
    return data, text


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes):
    return hashlib.sha256(value).hexdigest()
