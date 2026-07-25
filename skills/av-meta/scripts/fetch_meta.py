#!/usr/bin/env python3
"""Fetch AV metadata (code / magnets / cover / plot) from JavBus + JavDB (+ plot fallback)."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_JAVBUS = "https://www.javbus.com"
DEFAULT_JAVDB_MIRRORS = [
    "https://javdb.com",
    "https://www.javdb.com",
    "https://javdb36.com",
    "https://javdb39.com",
    "https://javdb48.com",
    "https://javdb601.com",
]
DEFAULT_PLOTS = [
    "https://javtxt.com",
]
ALLOWED_HOSTS = {
    "www.javbus.com",
    "javdb.com",
    "www.javdb.com",
    "javdb36.com",
    "javdb39.com",
    "javdb48.com",
    "javdb601.com",
    "javtxt.com",
    "pics.dmm.co.jp",
}
MAX_COVER_BYTES = 20 * 1024 * 1024

CODE_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{2,15}|[A-Z]{1,6}\d{1,3})-?(\d{2,5})(?![A-Z0-9])",
    re.I,
)
BTIH_RE = re.compile(r"btih:([A-Fa-f0-9]{32,40})", re.I)
SIZE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:TB|GB|MB|KB)", re.I)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalize_code(text: str) -> str:
    text = (text or "").strip().upper().replace("_", "-")
    m = CODE_RE.search(text)
    if not m:
        return ""
    return f"{m.group(1).upper()}-{m.group(2)}"


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def validate_external_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or host not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError(f"external_url_not_allowed:{host or 'missing_host'}")
    return url


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_external_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen_safe(req: urllib.request.Request, timeout: float):
    validate_external_url(req.full_url)
    opener = urllib.request.build_opener(
        SafeRedirectHandler(),
        urllib.request.HTTPSHandler(context=_ssl_context()),
    )
    return opener.open(req, timeout=timeout)


def http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    cookies: str = "",
) -> tuple[int, str, str]:
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
        "Cache-Control": "no-cache",
    }
    if cookies:
        h["Cookie"] = cookies
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urlopen_safe(req, timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="ignore")
            return resp.status, body, resp.geturl()
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        body = raw.decode("utf-8", errors="ignore")
        return e.code, body, url
    except Exception as e:  # noqa: BLE001
        return 0, str(e), url


def abs_url(base: str, path: str) -> str:
    if not path:
        return ""
    return validate_external_url(
        urllib.parse.urljoin(base if base.endswith("/") else base + "/", path)
    )


def safe_output_path(root: str, requested: str, code: str) -> str:
    root_path = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
    requested_path = os.path.expanduser(requested)
    if requested_path.endswith(("/", os.sep)):
        requested_path = os.path.join(requested_path, f"{code}.jpg")
    target = os.path.realpath(os.path.abspath(requested_path))
    if os.path.commonpath([root_path, target]) != root_path or target == root_path:
        raise ValueError("cover_path_outside_output_root")
    return target


def unique_keep(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        x = (x or "").strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def parse_magnets_html(html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tr in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", html, re.I):
        block = tr.group(1)
        m = re.search(r'href="(magnet:\?[^"]+)"', block, re.I)
        if not m:
            continue
        magnet = unescape(m.group(1))
        texts = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(t))).strip()
            for t in re.findall(r"<a[^>]*>([\s\S]*?)</a>", block, re.I)
        ]
        texts = [t for t in texts if t]
        name = texts[0] if texts else ""
        size = next(
            (t for t in texts if SIZE_RE.fullmatch(t.replace(" ", "")) or SIZE_RE.search(t)),
            "",
        )
        if size:
            size = SIZE_RE.search(size).group(0)  # type: ignore[union-attr]
        date = next((t for t in texts if DATE_RE.fullmatch(t)), "")
        tags: list[str] = []
        if re.search(r"高清|HD|FHD|UHD|btn-primary", block, re.I):
            tags.append("HD")
        if re.search(r"字幕|SUB|中字|_CH|CH\b", block, re.I):
            tags.append("SUB")
        if re.search(r"無修正|无码|無碼|UC|Uncensored", block, re.I):
            tags.append("UC")
        rows.append(
            {
                "name": name,
                "size": size,
                "date": date,
                "tags": unique_keep(tags),
                "magnet": magnet,
            }
        )
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        h = BTIH_RE.search(r["magnet"])
        key = h.group(1).upper() if h else r["magnet"]
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def sort_magnets(mags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def score(m: dict[str, Any]) -> tuple:
        tags = set(m.get("tags") or [])
        name = (m.get("name") or "") + " " + (m.get("magnet") or "")
        s = 0
        if "SUB" in tags or re.search(r"字幕|_CH|中字", name, re.I):
            s += 100
        if "HD" in tags or re.search(r"FHD|高清|2160|1080", name, re.I):
            s += 50
        size = m.get("size") or ""
        mb = 0.0
        mm = re.search(r"([\d.]+)\s*(TB|GB|MB|KB)", size, re.I)
        if mm:
            n = float(mm.group(1))
            unit = mm.group(2).upper()
            mb = n * {"TB": 1024 * 1024, "GB": 1024, "MB": 1, "KB": 0.001}[unit]
        return (-s, -mb, m.get("date") or "")

    return sorted(mags, key=score)


# ---------- JavBus ----------


def javbus_detail(base: str, code: str) -> dict[str, Any]:
    base = base.rstrip("/")
    url = f"{base}/{urllib.parse.quote(code)}"
    cookies = "existmag=mag; age=verified; dv=1"
    status, html, final = http_get(
        url,
        cookies=cookies,
        headers={"Referer": base + "/"},
    )
    result: dict[str, Any] = {
        "source": "javbus",
        "ok": False,
        "url": final or url,
        "http_status": status,
        "code": code,
        "title": "",
        "title_full": "",
        "date": "",
        "runtime": "",
        "director": "",
        "maker": "",
        "label": "",
        "series": "",
        "genres": [],
        "actresses": [],
        "cover": "",
        "samples": [],
        "plot": "",
        "magnets": [],
        "error": "",
    }
    if status != 200 or not html or ("識別碼" not in html and "识别码" not in html):
        if status == 200 and ("driver-verify" in final or "Age Verification" in html):
            result["error"] = "age_gate"
        elif status == 404 or (status == 200 and len(html) < 2000):
            result["error"] = "not_found"
        else:
            result["error"] = f"http_{status}"
        return result

    h3 = re.search(r"<h3>([^<]+)</h3>", html)
    title_full = unescape(h3.group(1)).strip() if h3 else ""
    result["title_full"] = title_full
    if title_full.upper().startswith(code.upper()):
        result["title"] = title_full[len(code) :].strip()
    else:
        result["title"] = title_full

    code_m = re.search(
        r"識別碼:</span>\s*<span[^>]*>\s*([^<]+)\s*</span>",
        html,
    ) or re.search(r"識別碼:</span>\s*([^<\n]+)", html)
    if code_m:
        result["code"] = normalize_code(code_m.group(1))

    def field_text(label: str) -> str:
        m = re.search(
            rf"{re.escape(label)}:</span>\s*(?:<a[^>]*>)?([^<\n]+)",
            html,
        )
        return unescape(m.group(1)).strip() if m else ""

    def field_link(label: str) -> str:
        m = re.search(
            rf"{re.escape(label)}:</span>\s*<a[^>]*>([^<]+)</a>",
            html,
        )
        return unescape(m.group(1)).strip() if m else ""

    result["date"] = field_text("發行日期") or field_text("发行日期")
    result["runtime"] = field_text("長度") or field_text("长度")
    result["director"] = field_link("導演") or field_link("导演") or field_text("導演")
    result["maker"] = field_link("製作商") or field_link("制作商")
    result["label"] = field_link("發行商") or field_link("发行商")
    result["series"] = field_link("系列")

    info_m = re.search(
        r'<div class="col-md-3 info">([\s\S]*?)<div id="magnet-table"|'
        r'<div class="col-md-3 info">([\s\S]*?)<ul>',
        html,
    )
    info_html = ""
    if info_m:
        info_html = info_m.group(1) or info_m.group(2) or ""
    if not info_html:
        info_m = re.search(r'<div class="col-md-3 info">([\s\S]{0,12000})', html)
        info_html = info_m.group(1) if info_m else html

    genres = re.findall(r'href="[^"]*/genre/[^"]*"[^>]*>([^<]+)</a>', info_html)
    genres = [
        unescape(g).strip()
        for g in genres
        if g.strip() and g.strip() not in ("多選提交", "有碼類別", "無碼類別")
    ]
    result["genres"] = unique_keep(genres)

    actresses = re.findall(r'href="[^"]*/star/[^"]*"[^>]*>([^<]+)</a>', info_html)
    if not actresses:
        actresses = re.findall(r'href="[^"]*/star/[^"]*"[^>]*>([^<]+)</a>', html)
    result["actresses"] = unique_keep([unescape(a) for a in actresses])

    cover = re.search(r'class="bigImage"[^>]*href="([^"]+)"', html)
    if cover:
        result["cover"] = abs_url(base, cover.group(1))
    else:
        img = re.search(r"var\s+img\s*=\s*'([^']+)'", html)
        if img:
            result["cover"] = abs_url(base, img.group(1))

    samples = re.findall(r'class="sample-box" href="([^"]+)"', html)
    result["samples"] = [abs_url(base, s) for s in samples]

    meta = re.search(
        r'<meta[^>]+name="description"[^>]+content="([^"]*)"',
        html,
        re.I,
    ) or re.search(
        r'<meta[^>]+content="([^"]*)"[^>]+name="description"',
        html,
        re.I,
    )
    if meta:
        result["plot"] = unescape(meta.group(1)).strip()

    gid_m = re.search(r"var\s+gid\s*=\s*(\d+)", html)
    uc_m = re.search(r"var\s+uc\s*=\s*(\d+)", html)
    img_m = re.search(r"var\s+img\s*=\s*'([^']+)'", html)
    if gid_m:
        gid = gid_m.group(1)
        uc = uc_m.group(1) if uc_m else "0"
        img = img_m.group(1) if img_m else ""
        qs = urllib.parse.urlencode(
            {
                "gid": gid,
                "lang": "zh",
                "img": img,
                "uc": uc,
                "floor": str(random.randint(100, 999)),
            }
        )
        mag_url = f"{base}/ajax/uncledatoolsbyajax.php?{qs}"
        st, mag_html, _ = http_get(
            mag_url,
            cookies=cookies,
            headers={
                "Referer": url,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            },
        )
        if st == 200 and mag_html:
            result["magnets"] = sort_magnets(parse_magnets_html(mag_html))

    result["ok"] = bool(result["code"] and (result["title_full"] or result["cover"]))
    if not result["ok"]:
        result["error"] = result["error"] or "parse_failed"
    return result


# ---------- JavDB ----------


def _javdb_blocked(html: str, status: int) -> bool:
    if status in (403, 429, 503):
        return True
    low = (html or "")[:800].lower()
    return any(
        x in low
        for x in (
            "banned your access",
            "異常行為",
            "异常行为",
            "just a moment",
            "cf-browser-verification",
            "redirecting...",
            "checking your browser",
        )
    )


def javdb_search_and_detail(mirrors: list[str], code: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": "javdb",
        "ok": False,
        "url": "",
        "http_status": 0,
        "code": code,
        "title": "",
        "title_full": "",
        "date": "",
        "runtime": "",
        "director": "",
        "maker": "",
        "label": "",
        "series": "",
        "genres": [],
        "actresses": [],
        "cover": "",
        "samples": [],
        "plot": "",
        "score": "",
        "magnets": [],
        "mirror": "",
        "error": "",
    }
    cookies = "over18=1; locale=zh; theme=auto"
    errors: list[str] = []

    for base in mirrors:
        base = base.rstrip("/")
        search_url = f"{base}/search?q={urllib.parse.quote(code)}&f=all"
        st, html, final = http_get(
            search_url,
            cookies=cookies,
            headers={"Referer": base + "/"},
            timeout=15,
        )
        if _javdb_blocked(html, st) or st != 200:
            errors.append(f"{base}:http_{st}_or_blocked")
            continue

        hrefs = re.findall(r'href="(/v/[A-Za-z0-9]+)"', html)
        hrefs = unique_keep(hrefs)

        detail_path = ""
        for m in re.finditer(
            r'href="(/v/[^"]+)"[\s\S]{0,400}?class="uid"[^>]*>([^<]+)<',
            html,
            re.I,
        ):
            if normalize_code(m.group(2)) == code:
                detail_path = m.group(1)
                break
        if not detail_path and hrefs:
            detail_path = hrefs[0] if isinstance(hrefs[0], str) else ""

        if not detail_path:
            errors.append(f"{base}:no_search_hit")
            continue

        detail_url = abs_url(base, detail_path)
        st2, dhtml, dfinal = http_get(
            detail_url,
            cookies=cookies,
            headers={"Referer": search_url},
            timeout=20,
        )
        result["http_status"] = st2
        result["url"] = dfinal or detail_url
        result["mirror"] = base
        if _javdb_blocked(dhtml, st2) or st2 != 200:
            errors.append(f"{base}:detail_blocked_{st2}")
            continue

        parsed = parse_javdb_detail(dhtml, base, code)
        result.update(parsed)
        result["source"] = "javdb"
        result["ok"] = bool(result.get("title_full") or result.get("cover") or result.get("magnets"))
        if result["ok"]:
            result["error"] = ""
            return result
        errors.append(f"{base}:parse_failed")

    result["error"] = "; ".join(errors) if errors else "all_mirrors_failed"
    return result


def parse_javdb_detail(html: str, base: str, code: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "code": code,
        "title": "",
        "title_full": "",
        "date": "",
        "runtime": "",
        "director": "",
        "maker": "",
        "label": "",
        "series": "",
        "genres": [],
        "actresses": [],
        "cover": "",
        "samples": [],
        "plot": "",
        "score": "",
        "magnets": [],
    }

    t = re.search(
        r'class="current-title"[^>]*>([^<]+)<',
        html,
    ) or re.search(r"<h2[^>]*>\s*<strong[^>]*>([^<]+)<", html) or re.search(
        r"<title>([^<]+)</title>", html
    )
    if t:
        title_full = unescape(t.group(1)).strip()
        title_full = re.sub(r"\s*\|\s*JavDB.*$", "", title_full).strip()
        out["title_full"] = title_full
        if title_full.upper().startswith(code.upper()):
            out["title"] = title_full[len(code) :].strip(" -|")
        else:
            out["title"] = title_full

    def panel(label_cn: str, label_en: str = "") -> str:
        pats = [
            rf"<strong>{re.escape(label_cn)}:</strong>\s*<span[^>]*>([^<]+)</span>",
            rf"<strong>{re.escape(label_cn)}:</strong>\s*<a[^>]*>([^<]+)</a>",
            rf"<strong>{re.escape(label_cn)}:</strong>\s*([^<\n]+)",
        ]
        if label_en:
            pats += [
                rf"<strong>{re.escape(label_en)}:</strong>\s*<span[^>]*>([^<]+)</span>",
                rf"<strong>{re.escape(label_en)}:</strong>\s*<a[^>]*>([^<]+)</a>",
            ]
        for p in pats:
            m = re.search(p, html, re.I)
            if m:
                return unescape(m.group(1)).strip()
        return ""

    out["code"] = normalize_code(panel("番號", "ID") or code)
    out["date"] = panel("日期", "Released Date") or panel("日期")
    out["runtime"] = panel("時長", "Duration")
    out["director"] = panel("導演", "Director")
    out["maker"] = panel("片商", "Maker")
    out["label"] = panel("發行", "Publisher") or panel("發行商")
    out["series"] = panel("系列", "Series")
    out["score"] = panel("評分", "Rating")

    genres = re.findall(
        r'href="/tags\?[^"]*"[^>]*>([^<]+)</a>',
        html,
    ) or re.findall(r'href="/tags/[^"]*"[^>]*>([^<]+)</a>', html)
    out["genres"] = unique_keep([unescape(g) for g in genres])

    acts = re.findall(r'href="/actors/[^"]*"[^>]*>([^<]+)</a>', html)
    out["actresses"] = unique_keep(
        [unescape(a) for a in acts if a.strip() and a.strip() != "？"]
    )

    cover = re.search(
        r'class="video-meta-panel"[\s\S]{0,2000}?src="(https?://[^"]+)"',
        html,
    ) or re.search(
        r'class="column[-\w\s]*video-cover[\s\S]{0,400}?src="([^"]+)"',
        html,
    ) or re.search(r'property="og:image"\s+content="([^"]+)"', html) or re.search(
        r'content="([^"]+)"\s+property="og:image"', html
    )
    if cover:
        out["cover"] = abs_url(base, cover.group(1))

    plot = re.search(
        r'class="panel-block"[^>]*>\s*<strong>簡介:</strong>\s*<span[^>]*>([\s\S]*?)</span>',
        html,
    ) or re.search(
        r"<strong>簡介:</strong>\s*<span[^>]*>([\s\S]*?)</span>",
        html,
    ) or re.search(
        r'itemprop="description"[^>]*>([\s\S]*?)</',
        html,
    )
    if plot:
        out["plot"] = re.sub(
            r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(plot.group(1)))
        ).strip()

    mags: list[dict[str, Any]] = []
    for m in re.finditer(r'href="(magnet:\?[^"]+)"', html, re.I):
        magnet = unescape(m.group(1))
        start = max(0, m.start() - 400)
        ctx = html[start : m.start()]
        name = ""
        nm = re.findall(r"<[^>]+>([^<]{2,120})</", ctx)
        if nm:
            name = unescape(nm[-1]).strip()
        tags = []
        ctx2 = html[m.start() : m.start() + 200] + ctx
        if re.search(r"字幕|SUB", ctx2, re.I):
            tags.append("SUB")
        if re.search(r"高清|HD|FHD", ctx2, re.I):
            tags.append("HD")
        size_m = SIZE_RE.search(ctx2)
        date_m = DATE_RE.search(ctx2)
        mags.append(
            {
                "name": name,
                "size": size_m.group(0) if size_m else "",
                "date": date_m.group(0) if date_m else "",
                "tags": unique_keep(tags),
                "magnet": magnet,
            }
        )
    if not mags:
        mags = parse_magnets_html(html)
    out["magnets"] = sort_magnets(mags)
    return out


# ---------- Plot fallback (javtxt) ----------


def fetch_plot_javtxt(base: str, code: str) -> dict[str, Any]:
    base = base.rstrip("/")
    search = f"{base}/search?type=id&q={urllib.parse.quote(code)}"
    st, html, _ = http_get(search, headers={"Referer": base + "/"})
    out = {"source": "javtxt", "ok": False, "plot": "", "url": search, "error": ""}
    if st != 200:
        out["error"] = f"http_{st}"
        return out
    paths = re.findall(r'href="(/v/\d+)"', html)
    if not paths:
        out["error"] = "not_found"
        return out
    detail = abs_url(base, paths[0])
    st2, dhtml, _ = http_get(detail, headers={"Referer": search})
    out["url"] = detail
    if st2 != 200:
        out["error"] = f"detail_http_{st2}"
        return out

    plot = ""
    for pat in [
        r'class="intro-txt"[^>]*>([\s\S]*?)</div>',
        r'class="panel-block"[^>]*>[\s\S]*?简介[\s\S]*?<p[^>]*>([\s\S]*?)</p>',
        r"剧情简介[\s\S]{0,20}</[^>]+>([\s\S]*?)</div>",
        r"简介[:：]</[^>]+>([\s\S]*?)</div>",
        r'<div class="origtozh">([\s\S]*?)</div>',
        r'class="text-zh"[^>]*>([\s\S]*?)</div>',
        r'class="intro"[\s\S]*?<p[^>]*>([\s\S]*?)</p>',
    ]:
        m = re.search(pat, dhtml, re.I)
        if m:
            plot = re.sub(
                r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(m.group(1)))
            ).strip()
            if len(plot) > 20:
                break
    if len(plot) < 20:
        paras = re.findall(r"<p[^>]*>([\s\S]*?)</p>", dhtml)
        cands = []
        for p in paras:
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(p))).strip()
            if len(t) >= 30 and re.search(r"[\u4e00-\u9fff]", t):
                cands.append(t)
        if cands:
            plot = max(cands, key=len)
    out["plot"] = plot
    out["ok"] = bool(plot)
    if not out["ok"]:
        out["error"] = "plot_not_found"
    return out


# ---------- Merge / CLI ----------


def merge_results(
    code: str,
    bus: dict[str, Any] | None,
    db: dict[str, Any] | None,
    plot_extra: dict[str, Any] | None,
) -> dict[str, Any]:
    bus = bus or {}
    db = db or {}
    plot_extra = plot_extra or {}

    def pick(*vals: Any) -> Any:
        for v in vals:
            if v is None:
                continue
            if isinstance(v, (list, dict)) and not v:
                continue
            if isinstance(v, str) and not v.strip():
                continue
            return v
        return vals[-1] if vals else None

    magnets = sort_magnets((db.get("magnets") or []) + (bus.get("magnets") or []))
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for m in magnets:
        h = BTIH_RE.search(m.get("magnet") or "")
        key = h.group(1).upper() if h else m.get("magnet")
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(m)

    plot = pick(db.get("plot"), plot_extra.get("plot"), bus.get("plot"), "")
    if plot and plot.startswith("【發行日期】") and plot_extra.get("plot"):
        plot = plot_extra["plot"]

    out = {
        "ok": bool(bus.get("ok") or db.get("ok")),
        "code": pick(db.get("code"), bus.get("code"), code),
        "title": pick(db.get("title"), bus.get("title"), ""),
        "title_full": pick(db.get("title_full"), bus.get("title_full"), ""),
        "date": pick(db.get("date"), bus.get("date"), ""),
        "runtime": pick(db.get("runtime"), bus.get("runtime"), ""),
        "director": pick(db.get("director"), bus.get("director"), ""),
        "maker": pick(db.get("maker"), bus.get("maker"), ""),
        "label": pick(db.get("label"), bus.get("label"), ""),
        "series": pick(db.get("series"), bus.get("series"), ""),
        "score": pick(db.get("score"), ""),
        "genres": unique_keep((db.get("genres") or []) + (bus.get("genres") or [])),
        "actresses": unique_keep(
            (db.get("actresses") or []) + (bus.get("actresses") or [])
        ),
        "cover": pick(db.get("cover"), bus.get("cover"), ""),
        "samples": unique_keep((bus.get("samples") or []) + (db.get("samples") or [])),
        "plot": plot or "",
        "magnets": uniq,
        "best_magnet": uniq[0] if uniq else None,
        "sources": {
            "javbus": {
                "ok": bool(bus.get("ok")),
                "url": bus.get("url") or "",
                "error": bus.get("error") or "",
                "magnet_count": len(bus.get("magnets") or []),
            },
            "javdb": {
                "ok": bool(db.get("ok")),
                "url": db.get("url") or "",
                "mirror": db.get("mirror") or "",
                "error": db.get("error") or "",
                "magnet_count": len(db.get("magnets") or []),
            },
            "plot": {
                "ok": bool(plot_extra.get("ok") or (db.get("plot") or "").strip()),
                "url": plot_extra.get("url") or db.get("url") or "",
                "error": plot_extra.get("error") or "",
            },
        },
    }
    return out


def load_mirrors_from_env() -> list[str]:
    raw = os.environ.get("AV_META_JAVDB_MIRRORS") or os.environ.get("JAVDB_BASE") or ""
    if raw:
        parts = re.split(r"[\s,;]+", raw.strip())
        return [p.rstrip("/") for p in parts if p.strip()]
    return list(DEFAULT_JAVDB_MIRRORS)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch AV metadata from JavBus/JavDB")
    p.add_argument("code", help="番号，如 SSIS-001")
    p.add_argument(
        "--source",
        choices=["auto", "javbus", "javdb", "both"],
        default="auto",
        help="数据源（auto=javbus 快速路径）",
    )
    p.add_argument("--javbus-base", default=os.environ.get("AV_META_JAVBUS", DEFAULT_JAVBUS))
    p.add_argument(
        "--javdb-mirror",
        action="append",
        default=None,
        help="JavDB 镜像，可重复；也可用环境变量 AV_META_JAVDB_MIRRORS",
    )
    p.add_argument("--no-plot-fallback", action="store_true", help="禁用 javtxt 剧情补全")
    p.add_argument("--plot-base", default=os.environ.get("AV_META_PLOT_BASE", DEFAULT_PLOTS[0]))
    p.add_argument("--limit-magnets", type=int, default=20, help="最多返回多少条磁力")
    p.add_argument("--pretty", action="store_true", help="格式化 JSON")
    p.add_argument("--download-cover", metavar="PATH", help="把封面下载到指定路径")
    p.add_argument(
        "--output-root",
        default=os.getcwd(),
        help="封面允许写入的根目录，默认当前工作目录",
    )
    args = p.parse_args(argv)

    code = normalize_code(args.code)
    if not code:
        print(json.dumps({"ok": False, "error": "invalid_code"}, ensure_ascii=False))
        return 2

    mirrors = args.javdb_mirror or load_mirrors_from_env()
    try:
        validate_external_url(args.javbus_base)
        validate_external_url(args.plot_base)
        for mirror in mirrors:
            validate_external_url(mirror)
    except ValueError as exc:
        print(
            json.dumps(
                {"ok": False, "error": "invalid_source", "detail": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    source = args.source
    if source == "auto":
        source = "javbus"

    bus = None
    db = None
    if source in ("javbus", "both"):
        bus = javbus_detail(args.javbus_base, code)
        time.sleep(0.2)
    if source in ("javdb", "both"):
        db = javdb_search_and_detail(mirrors, code)
        time.sleep(0.2)

    plot_extra = None
    need_plot = True
    if db and (db.get("plot") or "").strip() and not (db.get("plot") or "").startswith("【"):
        need_plot = False
    if not args.no_plot_fallback and need_plot:
        plot_extra = fetch_plot_javtxt(args.plot_base, code)

    result = merge_results(code, bus, db, plot_extra)
    if args.limit_magnets and result.get("magnets"):
        result["magnets"] = result["magnets"][: max(1, args.limit_magnets)]
        result["best_magnet"] = result["magnets"][0] if result["magnets"] else None

    if args.download_cover and result.get("cover"):
        try:
            validate_external_url(result["cover"])
            req = urllib.request.Request(
                result["cover"],
                headers={
                    "User-Agent": UA,
                    "Referer": args.javbus_base + "/",
                },
            )
            with urlopen_safe(req, 30) as resp:
                validate_external_url(resp.geturl())
                content_type = (resp.headers.get_content_type() or "").lower()
                if not content_type.startswith("image/"):
                    raise ValueError("cover_response_is_not_an_image")
                data = resp.read(MAX_COVER_BYTES + 1)
            if len(data) > MAX_COVER_BYTES:
                raise ValueError("cover_exceeds_20_mib")
            path = safe_output_path(args.output_root, args.download_cover, code)
            parent = os.path.dirname(path)
            os.makedirs(parent, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=parent, delete=False) as handle:
                handle.write(data)
                temp_path = handle.name
            try:
                os.chmod(temp_path, 0o644)
                os.replace(temp_path, path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            result["cover_file"] = path
        except Exception as e:  # noqa: BLE001
            result["cover_file_error"] = str(e)

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
