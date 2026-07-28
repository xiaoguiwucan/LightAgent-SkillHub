#!/usr/bin/env python3
"""Download one public Douyin video from a user-provided share link."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_SEND_BYTES = 20 * 1024 * 1024
TARGET_SEND_BYTES = 18 * 1024 * 1024
READ_CHUNK_BYTES = 256 * 1024
EXACT_HOSTS = {
    "v.douyin.com",
    "www.douyin.com",
    "m.douyin.com",
    "www.iesdouyin.com",
    "m.iesdouyin.com",
    "aweme.snssdk.com",
}
HOST_SUFFIXES = (".douyinvod.com", ".idouyinvod.com")
URL_PATTERN = re.compile(r"https://[^\s<>\"'\[\]()]+", re.IGNORECASE)


class DownloadError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _is_allowed_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    if port not in (None, 443):
        return False
    return host in EXACT_HOSTS or any(host.endswith(suffix) for suffix in HOST_SUFFIXES)


def extract_share_url(text: str) -> str:
    for match in URL_PATTERN.finditer(text or ""):
        candidate = match.group(0).rstrip(".,!?;:，。！？；：、)]}")
        if _is_allowed_url(candidate):
            return candidate
    if "https://" not in (text or ""):
        raise DownloadError("missing_url", "没有找到 HTTPS 抖音链接")
    raise DownloadError("unsupported_url", "链接不是受支持的抖音 HTTPS 地址")


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_allowed_url(newurl):
            raise DownloadError("unsafe_redirect", "抖音请求重定向到了未声明域名")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = build_opener(SafeRedirectHandler())


def _request(url: str, *, referer: str | None = None):
    if not _is_allowed_url(url):
        raise DownloadError("unsupported_url", "拒绝访问未声明域名")
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    return OPENER.open(Request(url, headers=headers), timeout=30)


class RouterDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._in_script = False
        self._parts: list[str] = []
        self.router_json: str | None = None

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() == "script":
            self._in_script = True
            self._parts = []

    def handle_data(self, data: str):
        if self._in_script:
            self._parts.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() != "script" or not self._in_script:
            return
        content = "".join(self._parts).strip()
        marker = "window._ROUTER_DATA ="
        if content.startswith(marker):
            self.router_json = content[len(marker) :].strip().rstrip(";")
        self._in_script = False
        self._parts = []


def _find_video_info(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        candidate = value.get("videoInfoRes")
        if isinstance(candidate, dict) and isinstance(candidate.get("item_list"), list):
            return candidate
        for nested in value.values():
            result = _find_video_info(nested)
            if result is not None:
                return result
    elif isinstance(value, list):
        for nested in value:
            result = _find_video_info(nested)
            if result is not None:
                return result
    return None


def parse_video_page(html: str) -> dict[str, Any]:
    parser = RouterDataParser()
    parser.feed(html)
    if not parser.router_json:
        raise DownloadError("page_changed", "抖音分享页没有可识别的视频数据")
    try:
        document = json.loads(parser.router_json)
    except json.JSONDecodeError as exc:
        raise DownloadError("page_changed", "抖音分享页数据解析失败") from exc
    video_info = _find_video_info(document)
    items = video_info.get("item_list") if video_info else None
    if not items or not isinstance(items[0], dict):
        raise DownloadError("video_not_found", "分享页没有返回公开视频")
    item = items[0]
    video = item.get("video")
    images = item.get("images") or item.get("image_infos")
    if not isinstance(video, dict) or images:
        raise DownloadError("unsupported_item_type", "当前链接不是单个视频")
    play_addr = video.get("play_addr") or {}
    urls = play_addr.get("url_list") or []
    if not urls or not isinstance(urls[0], str):
        raise DownloadError("video_not_found", "分享页没有返回视频地址")
    media_url = urls[0]
    if not _is_allowed_url(media_url):
        raise DownloadError("unsafe_media_url", "视频地址不在声明的抖音域名内")
    return {
        "aweme_id": str(item.get("aweme_id") or item.get("group_id_str") or ""),
        "title": str(item.get("desc") or ""),
        "author": str((item.get("author") or {}).get("nickname") or ""),
        "duration_ms": int(video.get("duration") or 0),
        "media_url": media_url,
    }


def fetch_video_info(share_url: str) -> dict[str, Any]:
    try:
        with _request(share_url) as response:
            final_url = response.geturl()
            if not _is_allowed_url(final_url):
                raise DownloadError("unsafe_redirect", "分享链接跳转到了未声明域名")
            payload = response.read(MAX_HTML_BYTES + 1)
    except DownloadError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise DownloadError("share_page_failed", f"抖音分享页请求失败：{exc}") from exc
    if len(payload) > MAX_HTML_BYTES:
        raise DownloadError("share_page_too_large", "抖音分享页响应异常")
    try:
        html = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DownloadError("page_changed", "抖音分享页编码异常") from exc
    return parse_video_page(html)


def _looks_like_mp4(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"ftyp" in handle.read(64)
    except OSError:
        return False


def _download_once(url: str, destination: Path, referer: str) -> int:
    temp_path: Path | None = None
    try:
        with _request(url, referer=referer) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if not (content_type.startswith("video/") or "octet-stream" in content_type):
                raise DownloadError("invalid_video", "下载响应不是视频文件")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_VIDEO_BYTES:
                raise DownloadError("video_too_large", "视频超过 200 MiB")
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{destination.stem}-", suffix=".part", dir=destination.parent, delete=False
            ) as handle:
                temp_path = Path(handle.name)
                total = 0
                while True:
                    chunk = response.read(READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise DownloadError("video_too_large", "视频超过 200 MiB")
                    handle.write(chunk)
        if not temp_path or not _looks_like_mp4(temp_path):
            raise DownloadError("invalid_video", "下载文件不是有效 MP4")
        os.replace(temp_path, destination)
        return destination.stat().st_size
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def download_video(info: dict[str, Any], output_root: Path, share_url: str) -> tuple[Path, int]:
    aweme_id = info.get("aweme_id") or ""
    if not re.fullmatch(r"[0-9]{10,32}", aweme_id):
        raise DownloadError("invalid_aweme_id", "抖音视频 ID 无效")
    root = output_root.expanduser().resolve()
    if root == Path(root.anchor):
        raise DownloadError("invalid_output_root", "输出目录不能是文件系统根目录")
    destination_dir = root / "videos" / "douyin-video-share"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{aweme_id}.mp4"
    if destination.is_file() and 0 < destination.stat().st_size <= MAX_VIDEO_BYTES and _looks_like_mp4(destination):
        return destination, destination.stat().st_size

    original_url = str(info["media_url"])
    candidates = []
    clean_url = original_url.replace("/playwm/", "/play/", 1)
    for candidate in (clean_url, original_url):
        if candidate not in candidates:
            candidates.append(candidate)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return destination, _download_once(candidate, destination, share_url)
        except DownloadError as exc:
            if exc.code == "video_too_large":
                raise
            last_error = exc
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
    raise DownloadError("download_failed", f"抖音视频下载失败：{last_error}")


def _target_video_bitrate_kbps(duration_ms: int) -> int:
    if duration_ms <= 0:
        raise DownloadError("invalid_duration", "无法确定视频时长，不能生成群聊发送版本")
    total_kbps = int(TARGET_SEND_BYTES * 8 / (duration_ms / 1000) / 1000)
    return max(180, total_kbps - 72)


def prepare_video_for_send(path: Path, duration_ms: int) -> tuple[int, bool]:
    size = path.stat().st_size
    if size <= MAX_SEND_BYTES:
        return size, False
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise DownloadError("missing_media_processing", "视频超过 20 MiB，需要 media-processing 能力生成群聊发送版本")

    video_kbps = _target_video_bitrate_kbps(duration_ms)
    temp_path = path.with_name(f".{path.stem}-send.mp4")
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                "scale='min(854,iw)':-2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-threads",
                "1",
                "-b:v",
                f"{video_kbps}k",
                "-maxrate",
                f"{video_kbps}k",
                "-bufsize",
                f"{video_kbps * 2}k",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-movflags",
                "+faststart",
                str(temp_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=420,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
            raise DownloadError("transcode_failed", f"群聊发送版本生成失败：{detail[0]}")
        if not _looks_like_mp4(temp_path) or temp_path.stat().st_size > MAX_SEND_BYTES:
            raise DownloadError("transcode_failed", "群聊发送版本无效或仍超过 20 MiB")
        os.replace(temp_path, path)
        return path.stat().st_size, True
    except subprocess.TimeoutExpired as exc:
        raise DownloadError("transcode_timeout", "生成群聊发送版本超过 420 秒") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("share_text")
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    try:
        share_url = extract_share_url(args.share_text)
        info = fetch_video_info(share_url)
        video_file, _ = download_video(info, Path(args.output_root), share_url)
        size, transcoded = prepare_video_for_send(video_file, info["duration_ms"])
        result = {
            "ok": True,
            "aweme_id": info["aweme_id"],
            "title": info["title"],
            "author": info["author"],
            "duration_ms": info["duration_ms"],
            "size_bytes": size,
            "transcoded_for_send": transcoded,
            "video_file": str(video_file),
            "source_url": share_url,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except DownloadError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
