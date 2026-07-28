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
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_SEND_BYTES = 24 * 1024 * 1024
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
        "source_width": int(video.get("width") or play_addr.get("width") or 0),
        "source_height": int(video.get("height") or play_addr.get("height") or 0),
        "video_id": str(play_addr.get("uri") or ""),
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


def _parse_frame_rate(value: str) -> float:
    try:
        numerator, denominator = value.split("/", 1)
        return round(float(numerator) / float(denominator), 3) if float(denominator) else 0.0
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return 0.0


def probe_video(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise DownloadError("missing_media_processing", "缺少 ffprobe，无法检测视频实际规格")
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,avg_frame_rate,bit_rate:format=duration,bit_rate",
                "-of",
                "json",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DownloadError("invalid_video", "视频规格检测超时") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise DownloadError("invalid_video", f"视频规格检测失败：{detail[0]}")
    try:
        document = json.loads(completed.stdout)
        streams = document.get("streams") or []
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        file_format = document.get("format") or {}
        return {
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "fps": _parse_frame_rate(str(video.get("avg_frame_rate") or "0/1")),
            "video_codec": str(video.get("codec_name") or ""),
            "audio_codec": str(audio.get("codec_name") or ""),
            "video_bitrate_bps": int(video.get("bit_rate") or 0),
            "audio_bitrate_bps": int(audio.get("bit_rate") or 0),
            "total_bitrate_bps": int(file_format.get("bit_rate") or 0),
            "duration_ms": int(float(file_format.get("duration") or 0) * 1000),
        }
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DownloadError("invalid_video", "视频规格数据不完整") from exc


def _meets_declared_resolution(spec: dict[str, Any], info: dict[str, Any]) -> bool:
    source_width = int(info.get("source_width") or 0)
    source_height = int(info.get("source_height") or 0)
    if not source_width or not source_height:
        return True
    return int(spec.get("width") or 0) >= source_width and int(spec.get("height") or 0) >= source_height


def _quality_label(spec: dict[str, Any]) -> str:
    short_edge = min(int(spec.get("width") or 0), int(spec.get("height") or 0))
    return f"{short_edge}p" if short_edge else "unknown"


def build_media_candidates(info: dict[str, Any]) -> list[tuple[str, str]]:
    original_url = str(info["media_url"])
    parsed = urlsplit(original_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    video_id = str(info.get("video_id") or query.get("video_id") or "")
    candidates: list[tuple[str, str]] = []
    if video_id and "/aweme/v1/play" in parsed.path:
        query["video_id"] = video_id
        query["ratio"] = "1080p"
        preferred_path = parsed.path.replace("/playwm/", "/play/", 1)
        preferred = urlunsplit((parsed.scheme, parsed.netloc, preferred_path, urlencode(query), parsed.fragment))
        candidates.append((preferred, "1080p-request"))
    if original_url not in {url for url, _ in candidates}:
        candidates.append((original_url, "page-fallback"))
    return candidates


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


def download_video(
    info: dict[str, Any], output_root: Path, share_url: str
) -> tuple[Path, int, str, dict[str, Any]]:
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
        cached_spec = probe_video(destination)
        if _meets_declared_resolution(cached_spec, info):
            return destination, destination.stat().st_size, f"cache-{_quality_label(cached_spec)}", cached_spec

    candidates = build_media_candidates(info)
    last_error: Exception | None = None
    candidate_path = destination.with_name(f".{destination.stem}-candidate.mp4")
    try:
        for index, (candidate, candidate_name) in enumerate(candidates):
            try:
                size = _download_once(candidate, candidate_path, share_url)
                spec = probe_video(candidate_path)
                if not _meets_declared_resolution(spec, info) and index < len(candidates) - 1:
                    continue
                os.replace(candidate_path, destination)
                return destination, size, f"{candidate_name}-{_quality_label(spec)}", spec
            except DownloadError as exc:
                if exc.code == "video_too_large":
                    raise
                last_error = exc
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
    finally:
        if candidate_path.exists():
            candidate_path.unlink()
    raise DownloadError("download_failed", f"抖音视频下载失败：{last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("share_text")
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    try:
        share_url = extract_share_url(args.share_text)
        info = fetch_video_info(share_url)
        video_file, size, selected_quality, spec = download_video(info, Path(args.output_root), share_url)
        duration_ms = spec.get("duration_ms") or info["duration_ms"]
        quality_preserved = _meets_declared_resolution(spec, info)
        result = {
            "ok": True,
            "aweme_id": info["aweme_id"],
            "title": info["title"],
            "author": info["author"],
            "duration_ms": duration_ms,
            "size_bytes": size,
            "source_width": info["source_width"],
            "source_height": info["source_height"],
            "output_width": spec["width"],
            "output_height": spec["height"],
            "fps": spec["fps"],
            "video_codec": spec["video_codec"],
            "audio_codec": spec["audio_codec"],
            "video_bitrate_bps": spec["video_bitrate_bps"],
            "audio_bitrate_bps": spec["audio_bitrate_bps"],
            "selected_quality": selected_quality,
            "quality_preserved": quality_preserved,
            "transcoded_for_send": False,
            "large_file_compatibility_warning": size > MAX_SEND_BYTES,
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
