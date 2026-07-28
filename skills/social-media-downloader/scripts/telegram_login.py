#!/usr/bin/env python3
"""Install a pinned tdl release and manage its persistent Telegram session."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "v0.20.3"
RELEASE_ROOT = f"https://github.com/iyear/tdl/releases/download/{VERSION}"
ASSETS = {
    ("linux", "x86_64"): ("tdl_Linux_64bit.tar.gz", "f69fe06c17f74c30a3b894b5be05c57a1b082f56b346c994025a2301b269a718"),
    ("linux", "aarch64"): ("tdl_Linux_arm64.tar.gz", "8398784d5b9390d26450e3e3528e2ffd0e9fe75d374f63273d0247e7ab0378b7"),
    ("darwin", "x86_64"): ("tdl_MacOS_64bit.tar.gz", "f66018736e446bd803872512519094b98bb4bde16a1c344271836061eba03561"),
    ("darwin", "arm64"): ("tdl_MacOS_arm64.tar.gz", "e6279b0679ebb96c8446b46e893f8671e52af64f7dad72b9ed0147955762a0e0"),
    ("windows", "amd64"): ("tdl_Windows_64bit.zip", "a908fe0e8aef387e50f3861ddcbd4f47b9c915153845ab05017a66478c0c530b"),
    ("windows", "arm64"): ("tdl_Windows_arm64.zip", "b08f7d61b6bca66e2bc6540a221d189a044f09b90a7a6ffbf230be0f891ba719"),
}


def _key() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    aliases = {
        "amd64": "amd64" if system == "windows" else "x86_64",
        "x64": "amd64" if system == "windows" else "x86_64",
        "arm64": "aarch64" if system == "linux" else "arm64",
        "aarch64": "arm64" if system in {"darwin", "windows"} else "aarch64",
    }
    return system, aliases.get(machine, machine)


def _root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if root == Path(root.anchor):
        raise SystemExit("数据目录不能是文件系统根目录")
    for child in ("tools", "telegram"):
        (root / child).mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(root / "telegram", 0o700)
    return root


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as handle:
            for item in handle.infolist():
                target = (destination / item.filename).resolve()
                if destination not in target.parents and target != destination:
                    raise SystemExit("tdl 压缩包包含越界路径")
                unix_mode = (item.external_attr >> 16) & 0o170000
                if unix_mode == stat.S_IFLNK:
                    raise SystemExit("tdl 压缩包包含符号链接")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    else:
        with tarfile.open(archive, "r:gz") as handle:
            for item in handle.getmembers():
                target = (destination / item.name).resolve()
                if destination not in target.parents and target != destination:
                    raise SystemExit("tdl 压缩包包含越界路径")
                if item.issym() or item.islnk() or item.isdev():
                    raise SystemExit("tdl 压缩包包含链接或设备文件")
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not item.isfile():
                    continue
                source = handle.extractfile(item)
                if source is None:
                    raise SystemExit("tdl 压缩包文件读取失败")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def install(root: Path) -> Path:
    key = _key()
    if key not in ASSETS:
        supported = ", ".join(f"{system}/{machine}" for system, machine in sorted(ASSETS))
        raise SystemExit(f"当前平台 {key[0]}/{key[1]} 没有固定产物；支持：{supported}")
    name, expected = ASSETS[key]
    with tempfile.TemporaryDirectory(prefix="tdl-install-") as directory:
        archive = Path(directory) / name
        with urllib.request.urlopen(f"{RELEASE_ROOT}/{name}", timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"tdl SHA-256 不匹配：期望 {expected}，实际 {actual}")
        extracted = Path(directory) / "extracted"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        candidates = [path for path in extracted.rglob("tdl.exe" if os.name == "nt" else "tdl") if path.is_file()]
        if len(candidates) != 1:
            raise SystemExit("tdl 官方包内未找到唯一可执行文件")
        destination = root / "tools" / ("tdl.exe" if os.name == "nt" else "tdl")
        temporary = destination.with_suffix(".new")
        shutil.copy2(candidates[0], temporary)
        if os.name != "nt":
            temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR)
        os.replace(temporary, destination)
        manifest = {
            "version": VERSION,
            "asset": name,
            "asset_sha256": expected,
            "binary_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        }
        manifest_path = root / "tools" / "tdl-manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(manifest_path, 0o600)
    return destination


def binary_valid(root: Path) -> Path | None:
    path = root / "tools" / ("tdl.exe" if os.name == "nt" else "tdl")
    manifest_path = root / "tools" / "tdl-manifest.json"
    if path.is_file() and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            valid = manifest.get("version") == VERSION and manifest.get("binary_sha256") == hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, json.JSONDecodeError):
            valid = False
        if valid:
            return path
    return None


def binary(root: Path) -> Path:
    return binary_valid(root) or install(root)


def main() -> int:
    parser = argparse.ArgumentParser(description="配置 social-media-downloader 的 Telegram 账号会话")
    parser.add_argument("action", choices=("install", "login", "status", "logout"))
    parser.add_argument("--data-root", required=True, help="技能持久数据目录")
    args = parser.parse_args()
    root = _root(args.data_root)
    storage = root / "telegram"
    if args.action == "install":
        executable = binary(root)
        print(json.dumps({"ok": True, "version": VERSION, "binary": str(executable)}, ensure_ascii=False))
        return 0
    if args.action == "login":
        executable = binary(root)
        return subprocess.call([str(executable), "--storage", f"type=bolt,path={storage}", "login", "-T", "qr"])
    if args.action == "status":
        executable = binary_valid(root)
        print(json.dumps({"installed": executable is not None, "session_present": any(storage.iterdir()), "version": VERSION}, ensure_ascii=False))
        return 0
    if storage.exists():
        shutil.rmtree(storage)
    storage.mkdir(mode=0o700)
    print(json.dumps({"ok": True, "session_removed": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
