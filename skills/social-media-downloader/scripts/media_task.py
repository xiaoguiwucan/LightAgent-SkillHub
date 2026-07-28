#!/usr/bin/env python3
"""Persistent, resumable media tasks for the social-media-downloader skill."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CHUNK_BYTES = 1024 * 1024
DEFAULT_CONCURRENCY = 3
DOWNLOAD_DEADLINE_SECONDS = 540
MAX_COLLECTION_ITEMS = 20
DEFAULT_COLLECTION_ITEMS = 5
SUPPORTED_HOSTS = {
    "douyin": {"v.douyin.com", "www.douyin.com", "m.douyin.com", "www.iesdouyin.com", "m.iesdouyin.com"},
    "tiktok": {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"},
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
    "telegram": {"t.me", "telegram.me", "www.t.me"},
}
TIKTOK_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
MEDIA_SUFFIXES = (".douyinvod.com", ".idouyinvod.com")
DOUYIN_ASSET_SUFFIXES = MEDIA_SUFFIXES + (".douyinpic.com", ".douyinstatic.com")
URL_RE = re.compile(r"https://[^\s<>\"'\[\]()]+", re.IGNORECASE)
SAFE_ID_RE = re.compile(r"[^\w.-]+", re.UNICODE)


class TaskError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.task_id: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_result(ok: bool, **values: Any) -> dict[str, Any]:
    return {"ok": ok, **values}


def emit(result: dict[str, Any], exit_code: int = 0) -> int:
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return exit_code


def data_root(explicit: str | None = None) -> Path:
    value = explicit or os.environ.get("LIGHTAGENT_SKILL_DATA")
    if not value:
        raise TaskError("missing_data_root", "Runner 未提供技能数据目录")
    root = Path(value).expanduser().resolve()
    if root == Path(root.anchor):
        raise TaskError("invalid_data_root", "技能数据目录不能是文件系统根目录")
    for child in ("tasks", "media", "locks", "slots", "telegram", "tools"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return root


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def _task_path(root: Path, task_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", task_id):
        raise TaskError("invalid_task_id", "任务 ID 无效")
    return root / "tasks" / f"{task_id}.json"


def load_task(root: Path, task_id: str) -> dict[str, Any]:
    path = _task_path(root, task_id)
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaskError("task_not_found", "没有找到该下载任务") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskError("task_corrupt", "下载任务记录损坏") from exc
    if task.get("task_id") != task_id:
        raise TaskError("task_corrupt", "下载任务 ID 不匹配")
    return task


def save_task(root: Path, task: dict[str, Any]) -> None:
    task["updated_at"] = utc_now()
    _atomic_json(_task_path(root, str(task["task_id"])), task)


@contextlib.contextmanager
def exclusive_file(path: Path, *, stale_seconds: int = 900) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(f"{os.getpid()} {time.time()}\n")
            acquired = True
            break
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > stale_seconds
            except FileNotFoundError:
                continue
            if stale:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                continue
            raise TaskError("task_busy", "该资源正在由另一个下载任务处理")
    if not acquired:
        raise TaskError("task_busy", "无法取得下载锁")
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


@contextlib.contextmanager
def download_slot(root: Path) -> Iterator[int]:
    claimed: Path | None = None
    for attempt in range(2):
        for index in range(DEFAULT_CONCURRENCY):
            candidate = root / "slots" / f"download-{index}.lock"
            try:
                fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as handle:
                    handle.write(f"{os.getpid()} {time.time()}\n")
                claimed = candidate
                break
            except FileExistsError:
                with contextlib.suppress(FileNotFoundError):
                    if attempt == 0 and time.time() - candidate.stat().st_mtime > 600:
                        candidate.unlink()
        if claimed is not None:
            break
    if claimed is None:
        raise TaskError("concurrency_limit", "已有 3 个下载任务运行，请稍后重试")
    try:
        yield int(claimed.stem.rsplit("-", 1)[-1])
    finally:
        with contextlib.suppress(FileNotFoundError):
            claimed.unlink()


def _host_platform(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    for platform_name, hosts in SUPPORTED_HOSTS.items():
        if host in hosts:
            return platform_name
    return None


def extract_source(text: str) -> tuple[str, str]:
    for match in URL_RE.finditer(text or ""):
        candidate = match.group(0).rstrip(".,!?;:，。！？；：、)]}")
        platform_name = _host_platform(candidate)
        if platform_name:
            return candidate, platform_name
    if "https://" not in (text or ""):
        raise TaskError("missing_url", "没有找到支持平台的 HTTPS 链接")
    raise TaskError("unsupported_url", "只支持抖音、TikTok、YouTube 和 Telegram HTTPS 链接")


def _is_douyin_network_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    return host in SUPPORTED_HOSTS["douyin"] | {"aweme.snssdk.com"} or any(host.endswith(suffix) for suffix in DOUYIN_ASSET_SUFFIXES)


class _DouyinRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_douyin_network_url(newurl):
            host = (urlparse(newurl).hostname or "unknown").lower()
            raise TaskError("unsafe_redirect", f"抖音请求跳转到了未声明域名：{host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


DOUYIN_OPENER = build_opener(_DouyinRedirects())


class _RouterDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.in_script = False
        self.parts: list[str] = []
        self.router_json: str | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag.lower() == "script":
            self.in_script = True
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.in_script:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self.in_script:
            return
        content = "".join(self.parts).strip()
        marker = "window._ROUTER_DATA ="
        if content.startswith(marker):
            self.router_json = content[len(marker):].strip().rstrip(";")
        self.in_script = False
        self.parts = []


def _find_douyin_items(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        candidate = value.get("videoInfoRes")
        if isinstance(candidate, dict) and isinstance(candidate.get("item_list"), list):
            return candidate["item_list"]
        for nested in value.values():
            found = _find_douyin_items(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_douyin_items(nested)
            if found:
                return found
    return None


def _douyin_urls(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    values: list[str] = []
    for key in ("url_list", "download_url_list"):
        for url in value.get(key) or []:
            if isinstance(url, str) and _is_douyin_network_url(url) and url not in values:
                values.append(url)
    for key in ("download_url", "display_image", "owner_watermark_image"):
        nested = value.get(key)
        if isinstance(nested, dict):
            for url in _douyin_urls(nested):
                if url not in values:
                    values.append(url)
    return values


def parse_douyin_page(html: str) -> dict[str, Any]:
    parser = _RouterDataParser()
    parser.feed(html)
    if not parser.router_json:
        raise TaskError("page_changed", "抖音分享页没有可识别的作品数据")
    try:
        document = json.loads(parser.router_json)
    except json.JSONDecodeError as exc:
        raise TaskError("page_changed", "抖音分享页数据解析失败") from exc
    items = _find_douyin_items(document)
    if not items or not isinstance(items[0], dict):
        raise TaskError("media_not_found", "抖音分享页没有返回公开作品")
    item = items[0]
    media_id = str(item.get("aweme_id") or item.get("group_id_str") or "")
    if not re.fullmatch(r"\d{10,32}", media_id):
        raise TaskError("invalid_media_id", "抖音作品 ID 无效")
    images = item.get("images") or item.get("image_infos") or []
    resources: list[dict[str, str]] = []
    if isinstance(images, list) and images:
        for index, image in enumerate(images, 1):
            urls = _douyin_urls(image)
            if urls:
                suffix = Path(urlparse(urls[0]).path).suffix.lower()
                resources.append({"kind": "image", "url": urls[0], "filename": f"{media_id}_{index:03d}{suffix if suffix in {'.jpg', '.jpeg', '.png', '.webp'} else '.jpg'}"})
    else:
        video = item.get("video") or {}
        play_addr = video.get("play_addr") or {}
        urls = _douyin_urls(play_addr)
        if not urls:
            raise TaskError("media_not_found", "抖音作品没有返回媒体地址")
        original = urls[0]
        parsed = urlsplit(original)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        video_id = str(play_addr.get("uri") or query.get("video_id") or "")
        if video_id and "/aweme/v1/play" in parsed.path:
            query.update({"video_id": video_id, "ratio": "1080p"})
            original = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.replace("/playwm/", "/play/", 1), urlencode(query), parsed.fragment))
        resources.append({"kind": "video", "url": original, "fallback_url": urls[0], "filename": f"{media_id}.mp4"})
    if not resources:
        raise TaskError("media_not_found", "抖音图集没有返回原图地址")
    return {"media_id": media_id, "title": str(item.get("desc") or ""), "resources": resources}


def fetch_douyin_info(url: str) -> dict[str, Any]:
    if not _is_douyin_network_url(url) or _host_platform(url) != "douyin":
        raise TaskError("unsupported_url", "抖音入口地址无效")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"})
    try:
        with DOUYIN_OPENER.open(request, timeout=30) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
    except TaskError:
        raise
    except (OSError, TimeoutError) as exc:
        raise TaskError("share_page_failed", f"抖音分享页请求失败：{exc}") from exc
    if len(payload) > 2 * 1024 * 1024:
        raise TaskError("share_page_too_large", "抖音分享页响应异常")
    try:
        return parse_douyin_page(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise TaskError("page_changed", "抖音分享页编码异常") from exc


def _safe_label(value: str, fallback: str = "用户") -> str:
    cleaned = SAFE_ID_RE.sub("_", (value or "").strip()).strip("._-")
    return (cleaned or fallback)[:48]


def _media_id(platform_name: str, url: str) -> str:
    parsed = urlparse(url)
    numbers = re.findall(r"\d{6,32}", parsed.path)
    if numbers:
        return numbers[-1]
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def _new_task(platform_name: str, url: str, requester: str, count: int) -> dict[str, Any]:
    task_id = uuid.uuid4().hex
    return {
        "task_id": task_id,
        "platform": platform_name,
        "source_url": url,
        "media_id": _media_id(platform_name, url),
        "requester_label": _safe_label(requester),
        "requested_items": count,
        "explicit_count": False,
        "status": "resolving",
        "total_bytes": 0,
        "downloaded_bytes": 0,
        "progress_percent": 0.0,
        "speed_bps": 0,
        "eta_seconds": None,
        "original_files": [],
        "delivery_parts": [],
        "next_part": 1,
        "total_parts": 0,
        "last_delivered_part": None,
        "last_error": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def _profile(root: Path) -> dict[str, Any]:
    path = root / "transport-profile.json"
    if not path.exists():
        return {"verified_max_send_bytes": 0, "verified_at": None, "verified_channel": None}
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
        limit = int(profile.get("verified_max_send_bytes") or 0)
        if not 0 <= limit <= 1000 * 1024 * 1024:
            return {"verified_max_send_bytes": 0, "profile_error": "群发阈值必须是 0 至 1000 MiB 的已验证档位"}
        return {**profile, "verified_max_send_bytes": limit}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"verified_max_send_bytes": 0, "profile_error": "群发阈值配置无效"}


def _directory_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return total


def _enough_space(root: Path, total: int, downloaded: int) -> bool:
    free = shutil.disk_usage(root).free
    if total <= 0:
        return free >= 256 * 1024 * 1024
    remaining_source = max(0, total - downloaded)
    required = remaining_source + total + int(total * 0.1)
    return free >= required


def _progress(root: Path, task: dict[str, Any], media_dir: Path, started: float, initial: int) -> None:
    downloaded = _directory_bytes(media_dir)
    elapsed = max(0.001, time.monotonic() - started)
    speed = max(0, int((downloaded - initial) / elapsed))
    total = int(task.get("total_bytes") or 0)
    task["downloaded_bytes"] = downloaded
    task["speed_bps"] = speed
    task["progress_percent"] = round(min(100.0, downloaded * 100 / total), 2) if total else 0.0
    task["eta_seconds"] = int((total - downloaded) / speed) if total > downloaded and speed else None
    save_task(root, task)


def _run_resumable(root: Path, task: dict[str, Any], command: list[str], media_dir: Path) -> bool:
    media_dir.mkdir(parents=True, exist_ok=True)
    initial = _directory_bytes(media_dir)
    started = time.monotonic()
    task["status"] = "downloading"
    task["last_error"] = None
    save_task(root, task)
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=os.name == "posix")
    except OSError as exc:
        raise TaskError("downloader_start_failed", f"无法启动平台下载器：{exc}") from exc
    try:
        while process.poll() is None:
            _progress(root, task, media_dir, started, initial)
            downloaded = int(task.get("downloaded_bytes") or 0)
            total = int(task.get("total_bytes") or 0)
            if not _enough_space(root, total, downloaded):
                _stop_process(process)
                raise TaskError("insufficient_disk_space", "磁盘剩余空间不足，已保留断点")
            if time.monotonic() - started >= DOWNLOAD_DEADLINE_SECONDS:
                _stop_process(process)
                task["status"] = "download_pending"
                task["last_error"] = "本轮达到 540 秒，断点已保存；请继续下载"
                _progress(root, task, media_dir, started, initial)
                return False
            time.sleep(1)
        stderr = (process.stderr.read() if process.stderr else "").strip()
        if process.returncode != 0:
            detail = "\n".join(stderr.splitlines()[-12:]) if stderr else "下载器返回失败"
            raise TaskError("download_failed", detail[-2000:])
        _progress(root, task, media_dir, started, initial)
        return True
    finally:
        if process.poll() is None:
            _stop_process(process)
        if process.stderr:
            process.stderr.close()


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGINT)
        else:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        process.wait(timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        with contextlib.suppress(OSError):
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def _download_douyin_resource(root: Path, task: dict[str, Any], resource: dict[str, str], media_dir: Path, deadline: float) -> bool:
    url = resource["url"]
    if not _is_douyin_network_url(url):
        raise TaskError("unsafe_media_url", "抖音媒体地址不在声明域名内")
    destination = media_dir / resource["filename"]
    partial = destination.with_name(f"{destination.name}.part")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
        "Referer": task["source_url"],
    }
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = Request(url, headers=headers)
    started = time.monotonic()
    initial = _directory_bytes(media_dir)
    try:
        with DOUYIN_OPENER.open(request, timeout=30) as response:
            if offset and getattr(response, "status", None) != 206:
                offset = 0
            length = int(response.headers.get("Content-Length") or 0)
            expected = offset + length
            if expected:
                existing = _directory_bytes(media_dir)
                task["total_bytes"] = max(int(task.get("total_bytes") or 0), max(0, existing - offset) + expected)
            mode = "ab" if offset else "wb"
            with partial.open(mode) as output:
                while True:
                    if time.monotonic() >= deadline:
                        task["status"] = "download_pending"
                        task["last_error"] = "本轮达到 540 秒，断点已保存；请继续下载"
                        _progress(root, task, media_dir, started, initial)
                        return False
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    output.write(chunk)
                    if time.monotonic() - started >= 1:
                        _progress(root, task, media_dir, started, initial)
                        started = time.monotonic()
                        initial = _directory_bytes(media_dir)
                    downloaded = _directory_bytes(media_dir)
                    total = int(task.get("total_bytes") or 0)
                    if not _enough_space(root, total, downloaded):
                        task["status"] = "download_pending"
                        task["last_error"] = "磁盘剩余空间不足，已保留断点"
                        save_task(root, task)
                        return False
        os.replace(partial, destination)
        return True
    except HTTPError as exc:
        fallback_url = resource.get("fallback_url")
        if fallback_url and fallback_url != url and exc.code in {403, 404, 410, 416, 429, 500, 502, 503, 504}:
            fallback = {**resource, "url": fallback_url, "fallback_url": fallback_url}
            return _download_douyin_resource(root, task, fallback, media_dir, deadline)
        task["status"] = "download_pending"
        task["last_error"] = f"抖音下载中断，断点已保留：HTTP {exc.code}"
        save_task(root, task)
        return False
    except TaskError:
        raise
    except (OSError, TimeoutError) as exc:
        task["status"] = "download_pending"
        task["last_error"] = f"抖音下载中断，断点已保留：{exc}"
        save_task(root, task)
        return False


def _download_douyin(root: Path, task: dict[str, Any], media_dir: Path) -> bool:
    info = fetch_douyin_info(str(task["source_url"]))
    task["media_id"] = info["media_id"]
    task["title"] = info["title"]
    task["status"] = "downloading"
    task["last_error"] = None
    save_task(root, task)
    media_dir = root / "media" / "douyin" / task["media_id"]
    media_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
    for resource in info["resources"]:
        destination = media_dir / resource["filename"]
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        if not _download_douyin_resource(root, task, resource, media_dir, deadline):
            return False
    task["downloaded_bytes"] = _directory_bytes(media_dir)
    task["total_bytes"] = max(int(task.get("total_bytes") or 0), task["downloaded_bytes"])
    task["progress_percent"] = 100.0
    task["eta_seconds"] = 0
    save_task(root, task)
    return True


def probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise TaskError("missing_media_processing", "缺少 ffprobe，无法验证原始规格")
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate:format=duration,bit_rate", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if completed.returncode:
        raise TaskError("invalid_media", "ffprobe 无法读取下载文件")
    try:
        document = json.loads(completed.stdout)
        streams = document.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        fmt = document.get("format") or {}
        return {
            "video_codec": video.get("codec_name") or "",
            "audio_codec": audio.get("codec_name") or "",
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "fps": str(video.get("avg_frame_rate") or "0/1"),
            "duration_seconds": float(fmt.get("duration") or 0),
            "bitrate_bps": int(fmt.get("bit_rate") or 0),
            "size_bytes": path.stat().st_size,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskError("invalid_media", "媒体规格数据不完整") from exc


def _finished_files(media_dir: Path) -> list[Path]:
    ignored = {".part", ".ytdl", ".tmp", ".json"}
    return sorted(
        path for path in media_dir.rglob("*")
        if path.is_file()
        and "delivery" not in path.relative_to(media_dir).parts
        and not any(path.name.endswith(suffix) for suffix in ignored)
    )


class _YtLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        self.errors.append(str(message))


def _yt_options(task: dict[str, Any], media_dir: Path, progress_hook: Any) -> dict[str, Any]:
    count = max(1, min(MAX_COLLECTION_ITEMS, int(task.get("requested_items") or DEFAULT_COLLECTION_ITEMS)))
    output = str(media_dir / "%(playlist_index&{} - |)s%(id)s.%(ext)s")
    return {
        "continuedl": True,
        "overwrites": False,
        "nopart": False,
        "merge_output_format": "mp4",
        "format": "bestvideo*+bestaudio/best",
        "playlistend": count,
        "outtmpl": {"default": output},
        "postprocessor_args": {"ffmpeg": ["-threads", "1"]},
        "progress_hooks": [progress_hook],
        "quiet": True,
        "noprogress": True,
        "writesubtitles": False,
        "writeinfojson": False,
    }


def _run_ytdlp(root: Path, task: dict[str, Any], media_dir: Path) -> bool:
    try:
        import yt_dlp
    except ImportError as exc:
        raise TaskError("missing_python_dependency", "技能私有环境缺少 yt-dlp") from exc
    media_dir.mkdir(parents=True, exist_ok=True)
    task["status"] = "downloading"
    task["last_error"] = None
    save_task(root, task)
    deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
    paused = False
    last_saved = 0.0

    def progress(value: dict[str, Any]) -> None:
        nonlocal paused, last_saved
        now = time.monotonic()
        if now >= deadline:
            paused = True
            raise TaskError("download_paused", "本轮达到 540 秒，断点已保存；请继续下载")
        downloaded = int(value.get("downloaded_bytes") or 0)
        total = int(value.get("total_bytes") or value.get("total_bytes_estimate") or 0)
        task["downloaded_bytes"] = max(int(task.get("downloaded_bytes") or 0), downloaded)
        task["total_bytes"] = max(int(task.get("total_bytes") or 0), total)
        task["speed_bps"] = int(value.get("speed") or 0)
        task["eta_seconds"] = int(value["eta"]) if value.get("eta") is not None else None
        task["progress_percent"] = round(min(100.0, downloaded * 100 / total), 2) if total else 0.0
        if now - last_saved >= 1:
            save_task(root, task)
            last_saved = now
        if not _enough_space(root, total, downloaded):
            paused = True
            raise TaskError("insufficient_disk_space", "磁盘剩余空间不足，已保留断点")

    logger = _YtLogger()
    options = _yt_options(task, media_dir, progress)
    options["logger"] = logger
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            code = downloader.download([str(task["source_url"])])
    except Exception as exc:
        if paused:
            task["status"] = "download_pending"
            task["last_error"] = str(exc)[:500]
            task["downloaded_bytes"] = _directory_bytes(media_dir)
            save_task(root, task)
            return False
        detail = logger.errors[-1] if logger.errors else str(exc)
        raise TaskError("download_failed", detail[-2000:]) from exc
    if code:
        raise TaskError("download_failed", (logger.errors[-1] if logger.errors else f"yt-dlp exit {code}")[-2000:])
    task["downloaded_bytes"] = _directory_bytes(media_dir)
    task["total_bytes"] = max(int(task.get("total_bytes") or 0), task["downloaded_bytes"])
    task["progress_percent"] = 100.0
    task["eta_seconds"] = 0
    save_task(root, task)
    return True


def _gallery_command(task: dict[str, Any], media_dir: Path) -> list[str]:
    return [sys.executable, "-m", "gallery_dl", "--dest", str(media_dir), "--no-mtime", str(task["source_url"])]


class _PlatformRedirects(HTTPRedirectHandler):
    def __init__(self, platform_name: str):
        super().__init__()
        self.platform_name = platform_name

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _host_platform(newurl) != self.platform_name:
            raise TaskError("unsafe_redirect", f"{self.platform_name} 短链接跳转到了未声明域名")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _resolve_platform_url(url: str, platform_name: str) -> str:
    if _host_platform(url) != platform_name:
        raise TaskError("unsupported_url", f"{platform_name} 地址无效")
    opener = build_opener(_PlatformRedirects(platform_name))
    try:
        with opener.open(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
            final_url = response.geturl()
    except TaskError:
        raise
    except (OSError, TimeoutError) as exc:
        raise TaskError("link_resolution_failed", f"{platform_name} 短链接解析失败：{exc}") from exc
    if _host_platform(final_url) != platform_name:
        raise TaskError("unsafe_redirect", f"{platform_name} 短链接跳转到了未声明域名")
    return final_url


def _resolve_tiktok_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host not in TIKTOK_SHORT_HOSTS:
        return url
    return _resolve_platform_url(url, "tiktok")


def _tdl_binary(root: Path) -> str:
    candidates = [root / "tools" / ("tdl.exe" if os.name == "nt" else "tdl")]
    for candidate in candidates:
        if str(candidate) and candidate.is_file() and os.access(candidate, os.X_OK):
            if candidate.parent == root / "tools":
                manifest_path = root / "tools" / "tdl-manifest.json"
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
                except (OSError, json.JSONDecodeError):
                    raise TaskError("telegram_binary_unverified", "Telegram 下载器缺少本地完整性清单，请管理员重新安装")
                if manifest.get("binary_sha256") != actual or manifest.get("version") != "v0.20.3":
                    raise TaskError("telegram_binary_unverified", "Telegram 下载器本地完整性校验失败，请管理员重新安装")
            return str(candidate)
    raise TaskError("telegram_not_configured", "未安装或登录 Telegram 下载器；请管理员按登录文档配置 tdl")


def _telegram_command(root: Path, task: dict[str, Any], media_dir: Path) -> list[str]:
    storage = root / "telegram"
    source = str(task["source_url"])
    count = max(1, min(MAX_COLLECTION_ITEMS, int(task.get("requested_items") or 1)))
    parsed = urlparse(source)
    match = re.fullmatch(r"(.*/)(\d+)", parsed.path.rstrip("/"))
    urls = [source]
    if task.get("explicit_count") and count > 1 and match:
        prefix, first = match.groups()
        urls = [parsed._replace(path=f"{prefix}{int(first) + offset}").geturl() for offset in range(count)]
    command = [_tdl_binary(root), "--storage", f"type=bolt,path={storage}", "dl"]
    for url in urls:
        command.extend(["-u", url])
    return [*command, "-d", str(media_dir), "--group", "--continue", "--skip-same", "-t", "8", "-l", "3"]


def _choose_downloader(root: Path, task: dict[str, Any], media_dir: Path) -> list[str]:
    platform_name = task["platform"]
    if platform_name == "telegram":
        return _telegram_command(root, task, media_dir)
    return _gallery_command(task, media_dir)


def _segment_video(source: Path, output_dir: Path, target_bytes: int, label: str, platform_name: str, media_id: str) -> list[Path]:
    spec = probe_media(source)
    if source.stat().st_size <= target_bytes:
        return [source]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TaskError("missing_media_processing", "缺少 ffmpeg，无法无损分段")
    duration = float(spec.get("duration_seconds") or 0)
    if duration <= 0:
        raise TaskError("invalid_media", "无法取得视频时长，不能安全分段")
    estimated_parts = max(2, (source.stat().st_size + target_bytes - 1) // target_bytes)
    segment_seconds = max(1.0, duration / estimated_parts * 0.92)
    output_dir.mkdir(parents=True, exist_ok=True)
    for _attempt in range(8):
        for old in output_dir.glob("*.mp4"):
            old.unlink()
        pattern = output_dir / "raw-%03d.mp4"
        completed = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(source), "-map", "0", "-c", "copy", "-f", "segment", "-segment_time", f"{segment_seconds:.3f}", "-reset_timestamps", "1", str(pattern)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=300, check=False,
        )
        raw_parts = sorted(output_dir.glob("raw-*.mp4"))
        if completed.returncode or not raw_parts:
            raise TaskError("segment_failed", (completed.stderr or "ffmpeg 无损分段失败")[-500:])
        total_parts = len(raw_parts)
        parts = []
        for index, raw in enumerate(raw_parts, 1):
            destination = output_dir / f"{label}_{platform_name}_{media_id}_第{index:03d}-{total_parts:03d}段.mp4"
            os.replace(raw, destination)
            parts.append(destination)
        if parts and max(path.stat().st_size for path in parts) <= target_bytes:
            original_duration = duration
            part_specs = [probe_media(path) for path in parts]
            part_duration = sum(float(item.get("duration_seconds") or 0) for item in part_specs)
            if abs(part_duration - original_duration) > max(2.0, original_duration * 0.02):
                raise TaskError("segment_validation_failed", "分段总时长与原文件不一致")
            identity = (spec["video_codec"], spec["audio_codec"], spec["width"], spec["height"], spec["fps"])
            if any((item["video_codec"], item["audio_codec"], item["width"], item["height"], item["fps"]) != identity for item in part_specs):
                raise TaskError("segment_validation_failed", "分段前后编码、分辨率、帧率或音轨不一致")
            return parts
        segment_seconds *= 0.75
    raise TaskError("segment_too_large", "无损切分后仍有分段超过发送上限")


def _prepare_delivery(root: Path, task: dict[str, Any], media_dir: Path) -> None:
    files = _finished_files(media_dir)
    if not files:
        raise TaskError("empty_download", "下载器没有生成媒体文件")
    task["original_files"] = [str(path) for path in files]
    profile = _profile(root)
    hard_limit = int(profile.get("verified_max_send_bytes") or 0)
    task["transport_profile"] = profile
    if hard_limit <= 0:
        task["status"] = "ready"
        task["delivery_parts"] = []
        task["total_parts"] = 0
        task["last_error"] = "尚未完成微信群文件上限实测；已下载但自动群发保持禁用"
        save_task(root, task)
        return
    target = int(hard_limit * 0.95)
    delivery: list[Path] = []
    for source in files:
        suffix = source.suffix.lower()
        if suffix in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
            delivery.extend(_segment_video(source, media_dir / "delivery" / task["task_id"] / source.stem, target, task["requester_label"], task["platform"], task["media_id"]))
        elif source.stat().st_size <= hard_limit:
            delivery.append(source)
        else:
            raise TaskError("image_too_large", "原图超过已验证发送上限，技能不会压缩或转换")
    if any(path.stat().st_size > hard_limit for path in delivery):
        raise TaskError("delivery_part_too_large", "发送前复核发现分段仍超过硬上限")
    task["delivery_parts"] = [str(path) for path in delivery]
    task["total_parts"] = len(delivery)
    task["next_part"] = 1
    task["status"] = "ready"
    task["progress_percent"] = 100.0
    task["last_error"] = None
    save_task(root, task)


def _download_task(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    if task.get("status") == "complete":
        return task
    media_dir = root / "media" / task["platform"] / task["media_id"]
    lock_name = hashlib.sha256(f"{task['platform']}:{task['media_id']}".encode()).hexdigest()
    with download_slot(root), exclusive_file(root / "locks" / f"media-{lock_name}.lock"):
        if task["platform"] == "douyin":
            finished = _download_douyin(root, task, media_dir)
            media_dir = root / "media" / "douyin" / task["media_id"]
        elif task["platform"] == "youtube":
            finished = _run_ytdlp(root, task, media_dir)
        elif task["platform"] == "tiktok":
            resolved = _resolve_tiktok_url(str(task["source_url"]))
            task["source_url"] = resolved
            save_task(root, task)
            if "/photo/" in urlparse(resolved).path:
                command = _choose_downloader(root, task, media_dir)
                finished = _run_resumable(root, task, command, media_dir)
            else:
                finished = _run_ytdlp(root, task, media_dir)
        else:
            command = _choose_downloader(root, task, media_dir)
            finished = _run_resumable(root, task, command, media_dir)
        if not finished:
            return task
        task["status"] = "segmenting"
        save_task(root, task)
        _prepare_delivery(root, task, media_dir)
    return task


def prepare_media(arguments: list[str]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("source_text")
    parser.add_argument("requester", nargs="?", default="用户")
    parser.add_argument("count", nargs="?", type=int, default=None)
    parser.add_argument("--data-root")
    args = parser.parse_args(arguments)
    if args.count is not None and not 1 <= args.count <= MAX_COLLECTION_ITEMS:
        raise TaskError("invalid_item_count", "合集数量必须在 1 到 20 之间")
    url, platform_name = extract_source(args.source_text)
    count = args.count if args.count is not None else (DEFAULT_COLLECTION_ITEMS if platform_name == "youtube" else 1)
    task = _new_task(platform_name, url, args.requester, count)
    task["explicit_count"] = args.count is not None
    root = data_root(args.data_root)
    save_task(root, task)
    try:
        _download_task(root, task)
    except TaskError as exc:
        task["status"] = "failed" if exc.code not in {"insufficient_disk_space", "concurrency_limit", "task_busy"} else "download_pending"
        task["last_error"] = str(exc)
        task["error_code"] = exc.code
        save_task(root, task)
        exc.task_id = task["task_id"]
        raise
    return task


def continue_download(arguments: list[str]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("task_id")
    parser.add_argument("--data-root")
    args = parser.parse_args(arguments)
    root = data_root(args.data_root)
    task = load_task(root, args.task_id)
    if task.get("status") not in {"download_pending", "failed", "downloading", "segmenting"}:
        return task
    try:
        return _download_task(root, task)
    except TaskError as exc:
        task["status"] = "download_pending"
        task["last_error"] = str(exc)
        task["error_code"] = exc.code
        save_task(root, task)
        exc.task_id = task["task_id"]
        raise


def task_status(arguments: list[str]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("task_id")
    parser.add_argument("--data-root")
    args = parser.parse_args(arguments)
    root = data_root(args.data_root)
    task = load_task(root, args.task_id)
    task["transport_profile"] = _profile(root)
    return task


def next_delivery(arguments: list[str]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("task_id")
    parser.add_argument("--data-root")
    args = parser.parse_args(arguments)
    root = data_root(args.data_root)
    with exclusive_file(root / "locks" / f"task-{args.task_id}.lock", stale_seconds=120):
        task = load_task(root, args.task_id)
        parts = [Path(value) for value in task.get("delivery_parts") or []]
        index = int(task.get("next_part") or 1) - 1
        if task.get("status") not in {"ready", "sending", "complete"} or not parts:
            raise TaskError("not_ready", task.get("last_error") or "媒体尚未完成下载和分段")
        if index >= len(parts):
            task["status"] = "complete"
            save_task(root, task)
            return {**task, "delivery_complete": True}
        path = parts[index].resolve()
        media_root = (root / "media").resolve()
        if media_root not in path.parents or not path.is_file():
            raise TaskError("delivery_missing", "待发送文件不存在或路径越界")
        hard_limit = int(_profile(root).get("verified_max_send_bytes") or 0)
        if hard_limit <= 0 or path.stat().st_size > hard_limit:
            raise TaskError("delivery_limit_unverified", "当前没有可用的微信群实测发送上限")
        task["last_delivered_part"] = index + 1
        task["next_part"] = index + 2
        task["status"] = "complete" if index + 1 == len(parts) else "sending"
        save_task(root, task)
        return {
            **task,
            "delivery_complete": False,
            "file": str(path),
            "part_number": index + 1,
            "total_parts": len(parts),
            "message": f"{task['requester_label']} 的媒体，第 {index + 1}/{len(parts)} 段",
            "size_bytes": path.stat().st_size,
        }


def retry_delivery(arguments: list[str]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("task_id")
    parser.add_argument("--data-root")
    args = parser.parse_args(arguments)
    root = data_root(args.data_root)
    task = load_task(root, args.task_id)
    previous = int(task.get("last_delivered_part") or 0)
    parts = [Path(value) for value in task.get("delivery_parts") or []]
    if previous < 1 or previous > len(parts):
        raise TaskError("nothing_to_retry", "没有可重发的上一段")
    path = parts[previous - 1]
    return {**task, "file": str(path), "part_number": previous, "total_parts": len(parts), "message": f"{task['requester_label']} 的媒体，重发第 {previous}/{len(parts)} 段", "size_bytes": path.stat().st_size}


def cancel_task(arguments: list[str]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("task_id")
    parser.add_argument("--data-root")
    args = parser.parse_args(arguments)
    root = data_root(args.data_root)
    task = load_task(root, args.task_id)
    media_dir = root / "media" / task["platform"] / task["media_id"]
    shutil.rmtree(media_dir / "delivery" / task["task_id"], ignore_errors=True)
    shared = False
    for task_file in (root / "tasks").glob("*.json"):
        if task_file == _task_path(root, task["task_id"]):
            continue
        with contextlib.suppress(OSError, json.JSONDecodeError):
            other = json.loads(task_file.read_text(encoding="utf-8"))
            if other.get("platform") == task["platform"] and other.get("media_id") == task["media_id"] and other.get("status") not in {"cancelled", "failed"}:
                shared = True
                break
    if not shared:
        shutil.rmtree(media_dir, ignore_errors=True)
    task["status"] = "cancelled"
    task["original_files"] = []
    task["delivery_parts"] = []
    task["last_error"] = None
    save_task(root, task)
    return task


def telegram_status(arguments: list[str]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-root")
    args = parser.parse_args(arguments)
    root = data_root(args.data_root)
    binary = None
    with contextlib.suppress(TaskError):
        binary = _tdl_binary(root)
    session_files = [path for path in (root / "telegram").rglob("*") if path.is_file()]
    return {
        "configured": bool(binary and session_files),
        "binary": binary,
        "session_present": bool(session_files),
        "session_directory": str(root / "telegram"),
        "login_method": "管理员终端二维码登录",
    }


ACTIONS = {
    "prepare_media": prepare_media,
    "continue_download": continue_download,
    "task_status": task_status,
    "next_delivery": next_delivery,
    "retry_delivery": retry_delivery,
    "cancel_task": cancel_task,
    "telegram_status": telegram_status,
}


def run_entrypoint(action: str, arguments: list[str] | None = None) -> int:
    try:
        handler = ACTIONS[action]
        return emit(json_result(True, **handler(list(sys.argv[1:] if arguments is None else arguments))))
    except TaskError as exc:
        values = {"error": exc.code, "message": str(exc)}
        if exc.task_id:
            values["task_id"] = exc.task_id
        return emit(json_result(False, **values), 1)
    except SystemExit:
        return emit(json_result(False, error="invalid_arguments", message="入口参数无效"), 1)
    except Exception as exc:  # noqa: BLE001 - keep Runner output structured without exposing a traceback
        return emit(json_result(False, error="internal_error", message=str(exc)[:500]), 1)


if __name__ == "__main__":
    action_parser = argparse.ArgumentParser()
    action_parser.add_argument("action", choices=sorted(ACTIONS))
    known, remaining = action_parser.parse_known_args()
    raise SystemExit(run_entrypoint(known.action, remaining))
