#!/usr/bin/env python3
"""LightAgent adapter for the upstream last30days engine."""

import json
import os
import runpy
import sys
from pathlib import Path


ENGINE = Path(__file__).with_name("last30days.py")
FORBIDDEN_FLAGS = {
    "--angles",
    "--corpus",
    "--corpus-all-time",
    "--emit",
    "--finalize",
    "--judgments",
    "--nominate-only",
    "--output",
    "--preflight-report-on-save-dir",
    "--publish",
    "--publish-html",
    "--publish-password",
    "--record-fixtures",
    "--save-dir",
    "--synthesis-file",
}
INLINE_JSON_FLAGS = {"--plan", "--competitors-plan"}
DOCTOR_FLAGS = {"--cached", "--postmortem", "--probe"}


def _fail(message):
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    return 2


def _configure_paths():
    raw_data_dir = os.environ.get("LIGHTAGENT_SKILL_DATA", "").strip()
    raw_config_dir = os.environ.get("LIGHTAGENT_SKILL_CONFIG", "").strip()
    if not raw_data_dir or not raw_config_dir:
        raise RuntimeError("missing_lightagent_skill_paths")
    data_dir = Path(raw_data_dir).resolve()
    config_dir = Path(raw_config_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    research_dir = data_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(data_dir)
    os.environ["LAST30DAYS_CONFIG_DIR"] = str(config_dir)
    os.environ["LAST30DAYS_MEMORY_DIR"] = str(research_dir)
    os.environ["LAST30DAYS_TRUST_PROJECT_CONFIG"] = "0"
    os.environ["BROWSER_COOKIE_MODE"] = "off"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    return research_dir


def _flag_name(value):
    return value.split("=", 1)[0] if value.startswith("--") else ""


def _validate_inline_json(arguments, index, flag):
    current = arguments[index]
    if "=" in current:
        raw = current.split("=", 1)[1]
    elif index + 1 < len(arguments):
        raw = arguments[index + 1]
    else:
        raise ValueError(f"{flag} 缺少 JSON 参数")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{flag} 只接受内联 JSON 对象")


def _research(arguments, research_dir):
    if not arguments or arguments[0].startswith("-"):
        return _fail("research_requires_topic")
    for index, value in enumerate(arguments):
        flag = _flag_name(value)
        if flag in FORBIDDEN_FLAGS:
            return _fail(f"unsupported_flag:{flag}")
        if flag == "--mock" and os.environ.get("LAST30DAYS_ADAPTER_TEST") != "1":
            return _fail("mock_mode_disabled")
        if flag in INLINE_JSON_FLAGS:
            try:
                _validate_inline_json(arguments, index, flag)
            except (ValueError, json.JSONDecodeError) as exc:
                return _fail(str(exc))
    engine_args = [
        *arguments,
        "--no-browser-cookies",
        "--emit=json",
        f"--save-dir={research_dir}",
    ]
    return _run_engine(engine_args)


def _doctor(arguments):
    invalid = [item for item in arguments if item not in DOCTOR_FLAGS]
    if invalid:
        return _fail(f"unsupported_doctor_flag:{invalid[0]}")
    return _run_engine(["doctor", *arguments, "--json"])


def _library_search(arguments):
    if not arguments or any(item.startswith("-") for item in arguments):
        return _fail("library_search_requires_plain_query")
    return _run_engine(["library", "search", " ".join(arguments)])


def _run_engine(arguments):
    sys.argv = [str(ENGINE), *arguments]
    runpy.run_path(str(ENGINE), run_name="__main__")
    return 0


def main(arguments=None):
    args = list(sys.argv[1:] if arguments is None else arguments)
    if not args:
        return _fail("missing_action")
    try:
        research_dir = _configure_paths()
    except (OSError, RuntimeError) as exc:
        return _fail(str(exc))
    action, action_args = args[0], args[1:]
    if action == "research":
        return _research(action_args, research_dir)
    if action == "doctor":
        return _doctor(action_args)
    if action == "library-search":
        return _library_search(action_args)
    return _fail(f"unknown_action:{action}")


if __name__ == "__main__":
    raise SystemExit(main())
