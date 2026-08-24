#!/usr/bin/env python3
"""Read-only Sub2API usage reporter for group conversations."""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from urllib import request
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo


DEFAULT_STATUS_URL = "http://hj.wwszxc.tax:31635/status.json"
DEFAULT_ADMIN_BASE_URL = "http://hj.wwszxc.tax:31634"
DEFAULT_TIMEOUT_SECONDS = 8
MAX_RESPONSE_BYTES = 512 * 1024
ADMIN_PAGE_SIZE = 200
SHANGHAI = ZoneInfo("Asia/Shanghai")


class StatusError(RuntimeError):
    """Raised when the read-only status source cannot be used safely."""


def _clamp_integer(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed))


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(round(number))


def _box(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    return value if isinstance(value, dict) else {}


def _available(box: dict[str, Any], require_ok: bool = True) -> bool:
    if require_ok and box.get("ok") is not True:
        return False
    return _number(box.get("tokens")) is not None


def _format_integer(value: Any) -> str:
    parsed = _integer(value)
    return "--" if parsed is None else f"{parsed:,}"


def _format_compact(value: Any) -> str:
    parsed = _number(value)
    if parsed is None:
        return "--"
    if abs(parsed) < 1_000_000:
        return f"{int(round(parsed)):,}"
    if abs(parsed) < 1_000_000_000:
        return f"{parsed / 1_000_000:.1f}M"
    return f"{parsed / 1_000_000_000:.1f}B"


def _format_box(box: dict[str, Any], compact: bool, require_ok: bool = True) -> str:
    if not _available(box, require_ok=require_ok):
        return "--"
    formatter = _format_compact if compact else _format_integer
    return formatter(box.get("tokens"))


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _format_time(value: Any, include_year: bool = False) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return "--"
    return parsed.strftime("%Y-%m-%d %H:%M" if include_year else "%m-%d %H:%M")


def _progress_bar(used_percent: Any) -> str:
    parsed = _integer(used_percent)
    if parsed is None:
        return "░" * 10
    normalized = max(0, min(100, parsed))
    filled = max(0, min(10, int(round(normalized / 10))))
    return "█" * filled + "░" * (10 - filled)


def _format_hours(value: Any) -> str:
    parsed = _number(value)
    return "--" if parsed is None else f"{max(0.0, parsed):.1f} 小时"


def _format_percent(value: Any) -> str:
    parsed = _number(value)
    return "--" if parsed is None else f"{max(0.0, parsed):.1f}%"


def _safe_username(value: Any, user_id: Any) -> str:
    username = " ".join(str(value or "").split()).strip()
    if username:
        return username[:40]
    parsed_id = _integer(user_id)
    return "未设置用户名" if parsed_id is None else f"未设置用户名（ID {parsed_id}）"


def _ranking_rows(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    rows = data.get("ranking") if isinstance(data, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _user_rows(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    rows = data.get("items") if isinstance(data, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _trend_rows(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    rows = data.get("trend") if isinstance(data, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _period_map(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        user_id = _integer(row.get("user_id"))
        if user_id is not None:
            result[user_id] = row
    return result


def _peak_label(value: Any) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return "--"
    return f"{parsed:%H}:00–{parsed:%H}:59"


def build_admin_report(
    users_payload: dict[str, Any],
    today_payload: dict[str, Any],
    week_payload: dict[str, Any],
    month_payload: dict[str, Any],
    trend_payload: dict[str, Any],
) -> dict[str, Any]:
    users = _user_rows(users_payload)
    today_rows = _ranking_rows(today_payload)
    week_rows = _ranking_rows(week_payload)
    month_rows = _ranking_rows(month_payload)
    today_map = _period_map(today_rows)
    week_map = _period_map(week_rows)
    month_map = _period_map(month_rows)

    usernames: dict[int, str] = {}
    for user in users:
        user_id = _integer(user.get("id"))
        if user_id is not None:
            usernames[user_id] = _safe_username(user.get("username"), user_id)
    for row in today_rows + week_rows + month_rows:
        user_id = _integer(row.get("user_id"))
        if user_id is not None and user_id not in usernames:
            usernames[user_id] = _safe_username(row.get("username"), user_id)

    today_data = today_payload.get("data", today_payload)
    week_data = week_payload.get("data", week_payload)
    month_data = month_payload.get("data", month_payload)
    total_today_tokens = _integer(
        today_data.get("total_tokens") if isinstance(today_data, dict) else None
    )
    if total_today_tokens is None:
        total_today_tokens = sum(
            max(0, _integer(row.get("tokens")) or 0) for row in today_rows
        )

    members: list[dict[str, Any]] = []
    for user_id, username in usernames.items():
        today_row = today_map.get(user_id, {})
        week_row = week_map.get(user_id, {})
        month_row = month_map.get(user_id, {})
        today_tokens = max(0, _integer(today_row.get("tokens")) or 0)
        week_tokens = max(0, _integer(week_row.get("tokens")) or 0)
        month_tokens = max(0, _integer(month_row.get("tokens")) or 0)
        today_requests = max(0, _integer(today_row.get("requests")) or 0)
        share = 0.0 if total_today_tokens <= 0 else today_tokens / total_today_tokens * 100
        members.append({
            "user_id": user_id,
            "username": username,
            "today_tokens": today_tokens,
            "week_tokens": week_tokens,
            "month_tokens": month_tokens,
            "today_requests": today_requests,
            "today_share_pct": share,
        })
    members.sort(
        key=lambda item: (
            -item["today_tokens"],
            -item["week_tokens"],
            -item["month_tokens"],
            item["username"],
        )
    )

    peaks: list[dict[str, Any]] = []
    for row in _trend_rows(trend_payload):
        tokens = max(0, _integer(row.get("total_tokens")) or 0)
        if tokens <= 0:
            continue
        peaks.append({
            "label": _peak_label(row.get("date")),
            "tokens": tokens,
            "requests": max(0, _integer(row.get("requests")) or 0),
            "today_share_pct": 0.0 if total_today_tokens <= 0 else tokens / total_today_tokens * 100,
        })
    peaks.sort(key=lambda item: (-item["tokens"], item["label"]))
    return {
        "members": members,
        "peaks": peaks[:3],
        "totals": {
            "today_tokens": total_today_tokens,
            "week_tokens": _integer(
                week_data.get("total_tokens") if isinstance(week_data, dict) else None
            ),
            "month_tokens": _integer(
                month_data.get("total_tokens") if isinstance(month_data, dict) else None
            ),
            "today_requests": _integer(
                today_data.get("total_requests") if isinstance(today_data, dict) else None
            ),
        },
    }


def render_status(payload: dict[str, Any], admin_report: dict[str, Any] | None = None) -> str:
    if not isinstance(payload, dict):
        raise StatusError("Sub2API 状态响应不是 JSON 对象")

    today = _box(payload, "today")
    week = _box(payload, "week")
    month = _box(payload, "month")
    usage = _box(payload, "usage")
    forecast = _box(payload, "forecast")
    week_forecast = _box(forecast, "week")
    month_forecast = _box(forecast, "month")
    daily_forecast = _box(forecast, "daily")
    week_pool = _box(payload, "week_pool")

    admin_totals = admin_report.get("totals", {}) if isinstance(admin_report, dict) else {}
    today_tokens = (
        _format_integer(admin_totals.get("today_tokens"))
        if _number(admin_totals.get("today_tokens")) is not None
        else _format_box(today, compact=False)
    )
    today_requests = (
        _format_integer(admin_totals.get("today_requests"))
        if _number(admin_totals.get("today_requests")) is not None
        else _format_integer(usage.get("requests")) if usage.get("ok") is True else "--"
    )
    week_tokens = (
        _format_integer(admin_totals.get("week_tokens"))
        if _number(admin_totals.get("week_tokens")) is not None
        else _format_box(week, compact=True)
    )
    month_tokens = (
        _format_integer(admin_totals.get("month_tokens"))
        if _number(admin_totals.get("month_tokens")) is not None
        else _format_box(month, compact=True)
    )
    week_estimate = _format_box(week_forecast, compact=True, require_ok=False)
    month_estimate = _format_box(month_forecast, compact=True, require_ok=False)
    daily_pace = _format_box(daily_forecast, compact=True, require_ok=False)

    pool_ok = week_pool.get("ok") is True
    used_percent = _integer(week_pool.get("used_pct")) if pool_ok else None
    remain_percent = _integer(week_pool.get("remain_pct")) if pool_ok else None
    used_label = "--" if used_percent is None else f"{max(0, min(100, used_percent))}%"
    remain_label = "--" if remain_percent is None else f"{max(0, min(100, remain_percent))}%"
    reset_at = _format_time(week_pool.get("reset_at")) if pool_ok else "--"
    hours_left = _format_hours(week_pool.get("hours_left")) if pool_ok else "--"

    lines = [
        "📊 Sub2API 用量分析",
        "━━━━━━━━━━━━━━━━",
        f"🕒 数据更新：{_format_time(payload.get('ts'), include_year=True)}（北京时间）",
        "",
        "📈 总体用量",
        f"今日用量：{today_tokens} Token",
        f"本周用量：{week_tokens} Token",
        f"本月用量：{month_tokens} Token",
        f"今日请求：{today_requests} 次",
        "",
        "🔭 用量预估",
        f"本周预计：{week_estimate} Token · 剩余 {_format_integer(forecast.get('days_left_week'))} 天",
        f"本月预计：{month_estimate} Token · 剩余 {_format_integer(forecast.get('days_left_month'))} 天",
        f"日均用量：{daily_pace} Token",
    ]

    members = admin_report.get("members", []) if isinstance(admin_report, dict) else []
    peaks = admin_report.get("peaks", []) if isinstance(admin_report, dict) else []
    if members:
        lines.extend(["", "👥 账号用量排行"])
        for index, member in enumerate(members, start=1):
            lines.extend([
                f"{index}. {member['username']}",
                f"今日用量：{_format_integer(member['today_tokens'])} Token",
                f"本周用量：{_format_integer(member['week_tokens'])} Token",
                f"本月用量：{_format_integer(member['month_tokens'])} Token",
                f"今日请求：{_format_integer(member['today_requests'])} 次",
                f"占今日总量：{_format_percent(member['today_share_pct'])}",
            ])

        top_member = members[0]
        request_member = max(members, key=lambda item: item["today_requests"])
        top_three_share = sum(member["today_share_pct"] for member in members[:3])
        lines.extend([
            "",
            "🧠 今日用量分析",
            (
                f"• 今日用量最高：{top_member['username']}，"
                f"共 {_format_integer(top_member['today_tokens'])} Token，"
                f"占今日总量 {_format_percent(top_member['today_share_pct'])}。"
            ),
            (
                f"• 今日请求最多：{request_member['username']}，"
                f"共 {_format_integer(request_member['today_requests'])} 次。"
            ),
            f"• 今日用量前三名合计占比：{_format_percent(top_three_share)}。",
        ])
        if peaks:
            peak = peaks[0]
            lines.append(
                f"• 今日用量高峰时段：{peak['label']}，"
                f"使用 {_format_integer(peak['tokens'])} Token，"
                f"共 {_format_integer(peak['requests'])} 次请求，"
                f"占今日总量 {_format_percent(peak['today_share_pct'])}。"
            )
    else:
        lines.extend(["", "👥 账号用量排行", "成员用量数据暂不可用"])

    lines.extend([
        "",
        "⚡ Sub2API 周池额度",
        f"{_progress_bar(used_percent)}  已用 {used_label}",
        f"剩余 {remain_label} · 重置 {reset_at} · 约 {hours_left}",
    ])

    if not pool_ok:
        lines.append("⚠️ 周池快照暂不可用，请稍后重试")
    elif week_pool.get("stale") is True:
        lines.append("⚠️ 周池快照较旧，当前百分比可能存在延迟")
    elif week_pool.get("will_exhaust") is True:
        pace = _integer(week_pool.get("pace_end_pct"))
        suffix = "" if pace is None else f"，预计周期末达到 {pace}%"
        lines.append(f"⚠️ 按当前节奏可能在重置前耗尽{suffix}")
    else:
        lines.append("✅ 按当前节奏预计可平稳使用至重置")

    if any(box.get("ok") is not True for box in (today, week, month)):
        lines.append("ℹ️ 部分用量字段暂不可用，已用 -- 标记")
    return "\n".join(lines)


def _selected_data(payload: dict[str, Any]) -> dict[str, Any]:
    forecast = _box(payload, "forecast")
    week_pool = _box(payload, "week_pool")
    usage = _box(payload, "usage")
    return {
        "ts": payload.get("ts"),
        "today_tokens": _box(payload, "today").get("tokens"),
        "today_requests": usage.get("requests"),
        "week_tokens": _box(payload, "week").get("tokens"),
        "month_tokens": _box(payload, "month").get("tokens"),
        "week_forecast_tokens": _box(forecast, "week").get("tokens"),
        "month_forecast_tokens": _box(forecast, "month").get("tokens"),
        "daily_tokens": _box(forecast, "daily").get("tokens"),
        "week_pool_used_pct": week_pool.get("used_pct"),
        "week_pool_remain_pct": week_pool.get("remain_pct"),
        "week_pool_reset_at": week_pool.get("reset_at"),
        "week_pool_stale": week_pool.get("stale"),
        "week_pool_will_exhaust": week_pool.get("will_exhaust"),
    }


def _fetch_json(url: str, timeout_seconds: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StatusError("Sub2API 查询地址必须是 http 或 https 地址")
    if parsed.username or parsed.password:
        raise StatusError("Sub2API 查询地址不允许内嵌账号或密码")
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "LightAgent-sub2api-usage/2.0",
    }
    request_headers.update(headers or {})
    http_request = request.Request(
        url,
        headers=request_headers,
        method="GET",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        raise StatusError("无法读取 Sub2API 状态，请稍后重试") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise StatusError("Sub2API 状态响应过大")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatusError("Sub2API 状态响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise StatusError("Sub2API 状态响应不是 JSON 对象")
    return payload


def fetch_status(url: str, timeout_seconds: int) -> dict[str, Any]:
    return _fetch_json(url, timeout_seconds)


def _admin_endpoint(base_url: str, path: str, params: dict[str, Any]) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/api/v1"):
        root = normalized
    else:
        root = f"{normalized}/api/v1"
    return f"{root}{path}?{urlencode(params)}"


def fetch_admin_report(
    base_url: str,
    api_key: str,
    timeout_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not api_key.strip():
        raise StatusError("Sub2API 成员统计密钥未配置")
    current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    today = current.date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    common = {"end_date": today.isoformat(), "timezone": "Asia/Shanghai"}
    requests = {
        "users": _admin_endpoint(base_url, "/admin/users", {
            "page": 1,
            "page_size": ADMIN_PAGE_SIZE,
            "sort_by": "id",
            "sort_order": "asc",
        }),
        "today": _admin_endpoint(base_url, "/admin/dashboard/users-ranking", {
            **common,
            "start_date": today.isoformat(),
            "sort_by": "total_tokens",
            "limit": ADMIN_PAGE_SIZE,
        }),
        "week": _admin_endpoint(base_url, "/admin/dashboard/users-ranking", {
            **common,
            "start_date": week_start.isoformat(),
            "sort_by": "total_tokens",
            "limit": ADMIN_PAGE_SIZE,
        }),
        "month": _admin_endpoint(base_url, "/admin/dashboard/users-ranking", {
            **common,
            "start_date": month_start.isoformat(),
            "sort_by": "total_tokens",
            "limit": ADMIN_PAGE_SIZE,
        }),
        "trend": _admin_endpoint(base_url, "/admin/dashboard/trend", {
            **common,
            "start_date": today.isoformat(),
            "granularity": "hour",
        }),
    }
    headers = {"x-api-key": api_key.strip()}
    try:
        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            futures = {
                name: executor.submit(_fetch_json, url, timeout_seconds, headers)
                for name, url in requests.items()
            }
            payloads = {name: future.result() for name, future in futures.items()}
    except Exception as exc:
        if isinstance(exc, StatusError):
            raise
        raise StatusError("无法读取 Sub2API 成员用量，请稍后重试") from exc
    return build_admin_report(
        payloads["users"],
        payloads["today"],
        payloads["week"],
        payloads["month"],
        payloads["trend"],
    )


def main() -> int:
    status_url = str(os.getenv("SUB2API_STATUS_URL") or DEFAULT_STATUS_URL).strip()
    admin_base_url = str(
        os.getenv("SUB2API_ADMIN_BASE_URL") or DEFAULT_ADMIN_BASE_URL
    ).strip()
    admin_api_key = str(os.getenv("SUB2API_ADMIN_API_KEY") or "").strip()
    timeout_seconds = _clamp_integer(
        os.getenv("SUB2API_STATUS_TIMEOUT_SECONDS"), 1, 30, DEFAULT_TIMEOUT_SECONDS
    )
    try:
        payload = fetch_status(status_url, timeout_seconds)
        admin_report = fetch_admin_report(
            admin_base_url,
            admin_api_key,
            timeout_seconds,
        )
        data = _selected_data(payload)
        data["members"] = admin_report.get("members", [])
        data["peaks"] = admin_report.get("peaks", [])
        result = {
            "status": "success",
            "text": render_status(payload, admin_report),
            "data": data,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except StatusError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
