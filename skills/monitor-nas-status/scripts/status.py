#!/usr/bin/env python3
"""Read-only NAS health collector for the monitor-nas-status skill."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_PLATFORMS = {"auto", "fnos", "synology", "zspace", "ugreen", "linux"}
HOST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")

REMOTE_SCRIPT = r'''set -u
clean_value() {
    printf '%s' "$1" | tr '\t\r\n|' '    '
}
kv() {
    printf 'KV|%s|' "$1"
    clean_value "$2"
    printf '\n'
}

printf 'NASMON|1\n'
kv hostname "$(hostname -f 2>/dev/null || hostname 2>/dev/null || printf unknown)"
kv kernel "$(uname -r 2>/dev/null || printf unknown)"
kv architecture "$(uname -m 2>/dev/null || printf unknown)"
kv cpu_count "$(getconf _NPROCESSORS_ONLN 2>/dev/null || awk '/^processor/{n++} END{print n+0}' /proc/cpuinfo 2>/dev/null || printf 0)"
kv uptime_seconds "$(awk '{printf "%d", $1}' /proc/uptime 2>/dev/null || printf 0)"
kv load1 "$(awk '{print $1}' /proc/loadavg 2>/dev/null || printf 0)"
kv cpu_percent "$(awk 'NR==1 { for (i=2;i<=NF;i++) a[i]=$i; idle1=$5+$6; total1=0; for(i=2;i<=NF;i++) total1+=$i; system("sleep 1"); getline line < "/proc/stat"; n=split(line,b," "); idle2=b[5]+b[6]; total2=0; for(i=2;i<=n;i++) total2+=b[i]; dt=total2-total1; di=idle2-idle1; if(dt>0) printf "%.1f", 100*(dt-di)/dt; else printf "0.0"; exit }' /proc/stat 2>/dev/null || printf 0)"
kv memory_total_kb "$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || printf 0)"
kv memory_available_kb "$(awk '/^MemAvailable:/{print $2; found=1} /^MemFree:|^Buffers:|^Cached:/{fallback+=$2} END{if(!found) print fallback+0}' /proc/meminfo 2>/dev/null || printf 0)"
kv temperature_c "$(for p in /sys/class/thermal/thermal_zone*/temp /sys/class/hwmon/hwmon*/temp*_input; do [ -r "$p" ] || continue; cat "$p" 2>/dev/null; done | awk '$1>=1000 && $1<=150000 {v=$1/1000; if(v>m)m=v} END{if(m>0)printf "%.1f",m}' 2>/dev/null)"
kv dmi_vendor "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)"
kv dmi_product "$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"

printf 'BEGIN|os_release\n'
for f in /etc/os-release /etc.defaults/VERSION /etc/fnos-release /etc/ugreen-release /etc/zos-release; do
    [ -r "$f" ] || continue
    printf 'FILE|%s\n' "$f"
    sed -n '1,80p' "$f" 2>/dev/null || true
done
printf 'END|os_release\n'

printf 'BEGIN|filesystems\n'
df -Pk 2>/dev/null | sed -n '2,120p' || true
printf 'END|filesystems\n'

printf 'BEGIN|lsblk\n'
if command -v lsblk >/dev/null 2>&1; then
    lsblk -b -J -d -o NAME,SIZE,TYPE,MODEL,ROTA,TRAN 2>/dev/null || true
fi
printf 'END|lsblk\n'

printf 'BEGIN|mdstat\n'
cat /proc/mdstat 2>/dev/null || true
printf 'END|mdstat\n'

printf 'BEGIN|services\n'
if command -v systemctl >/dev/null 2>&1; then
    for service in docker smbd nmbd nfs-server nfs-kernel-server nginx; do
        state=$(systemctl is-active "$service" 2>/dev/null || true)
        [ -n "$state" ] && [ "$state" != "unknown" ] && printf '%s|%s\n' "$service" "$state"
    done
fi
printf 'END|services\n'

printf 'BEGIN|docker\n'
if command -v docker >/dev/null 2>&1; then
    printf 'installed|true\n'
    if docker info >/dev/null 2>&1; then
        total=$(docker ps -a -q 2>/dev/null | wc -l | awk '{print $1}')
        running=$(docker ps -q 2>/dev/null | wc -l | awk '{print $1}')
        unhealthy=$(docker ps --filter health=unhealthy -q 2>/dev/null | wc -l | awk '{print $1}')
        printf 'accessible|true\ntotal|%s\nrunning|%s\nunhealthy|%s\n' "$total" "$running" "$unhealthy"
    else
        printf 'accessible|false\n'
    fi
fi
printf 'END|docker\n'
'''


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    user: str
    port: int = 22
    key_path: str = ""
    platform: str = "auto"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _target_from_mapping(value: dict[str, Any], index: int) -> Target:
    host = str(value.get("host") or "").strip()
    user = str(value.get("user") or "").strip()
    name = str(value.get("name") or host or f"nas-{index + 1}").strip()
    platform = str(value.get("platform") or "auto").strip().lower()
    port = _as_int(value.get("port"), 22)
    key_path = str(value.get("key_path") or "").strip()

    if not HOST_RE.fullmatch(host) or host.startswith("-"):
        raise ValueError(f"target {name!r} has an invalid host")
    if not USER_RE.fullmatch(user) or user.startswith("-"):
        raise ValueError(f"target {name!r} has an invalid SSH user")
    if not NAME_RE.fullmatch(name):
        raise ValueError("target name is empty, too long, or contains control characters")
    if port < 1 or port > 65535:
        raise ValueError(f"target {name!r} has an invalid SSH port")
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"target {name!r} has unsupported platform {platform!r}")
    if key_path:
        key = Path(key_path).expanduser()
        if not key.is_absolute():
            raise ValueError(f"target {name!r} key_path must be absolute")
        key_path = str(key)
    return Target(name=name, host=host, user=user, port=port, key_path=key_path, platform=platform)


def _config_dir(create: bool = True) -> Path:
    configured = str(os.environ.get("LIGHTAGENT_SKILL_CONFIG") or "").strip()
    path = Path(configured) if configured else Path.home() / ".lightagent" / "skill-config" / "monitor-nas-status"
    if create:
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass
    return path


def load_saved_config() -> dict[str, Any]:
    path = _config_dir(create=False) / "config.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("saved NAS monitor configuration is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("saved NAS monitor configuration must be an object")
    return value


def load_targets(env: dict[str, str] | None = None) -> list[Target]:
    env = env or dict(os.environ)
    raw = str(env.get("NAS_MONITOR_TARGETS") or "").strip()
    if raw:
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"NAS_MONITOR_TARGETS is not valid JSON: {exc.msg}") from exc
        if isinstance(values, dict):
            values = [values]
        if not isinstance(values, list) or not values or not all(isinstance(item, dict) for item in values):
            raise ValueError("NAS_MONITOR_TARGETS must be a non-empty object or array of objects")
    else:
        saved_targets = load_saved_config().get("targets", [])
        if saved_targets:
            if not isinstance(saved_targets, list) or not all(isinstance(item, dict) for item in saved_targets):
                raise ValueError("saved NAS monitor targets must be an array of objects")
            values = saved_targets
        else:
            values = []
        host = str(env.get("NAS_MONITOR_HOST") or "").strip()
        if host:
            values = [{
                "host": host,
                "name": env.get("NAS_MONITOR_NAME") or host,
                "user": env.get("NAS_MONITOR_USER") or "",
                "port": env.get("NAS_MONITOR_PORT") or 22,
                "key_path": env.get("NAS_MONITOR_KEY_PATH") or "",
                "platform": env.get("NAS_MONITOR_PLATFORM") or "auto",
            }]
        if not values:
            raise ValueError("configure NAS_MONITOR_TARGETS or NAS_MONITOR_HOST first")
    targets = [_target_from_mapping(item, index) for index, item in enumerate(values)]
    names = [target.name for target in targets]
    if len(names) != len(set(names)):
        raise ValueError("NAS target names must be unique")
    return targets
def build_ssh_command(target: Target, known_hosts: Path, timeout: int) -> list[str]:
    command = [
        "ssh", "-F", os.devnull,
        "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "ConnectionAttempts=1",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "HashKnownHosts=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "LogLevel=ERROR",
        "-p", str(target.port),
    ]
    if target.key_path:
        command.extend(["-o", "IdentitiesOnly=yes", "-i", target.key_path])
    command.extend([f"{target.user}@{target.host}", "sh", "-s"])
    return command


def _friendly_ssh_error(stderr: str) -> str:
    lowered = stderr.lower()
    if "remote host identification has changed" in lowered or "host key verification failed" in lowered:
        return "SSH host key verification failed; verify the NAS identity before changing the pinned key"
    if "permission denied" in lowered or "authentication failed" in lowered:
        return "SSH key authentication failed"
    if "connection refused" in lowered:
        return "SSH connection was refused; verify that SSH is enabled and the configured port is correct"
    if "timed out" in lowered or "no route to host" in lowered or "network is unreachable" in lowered:
        return "NAS is unreachable over SSH"
    return "SSH collection failed"


def collect_remote(target: Target, timeout: int) -> str:
    config_dir = _config_dir()
    known_hosts = config_dir / "known_hosts"
    if target.key_path:
        key = Path(target.key_path)
        if not key.is_file():
            raise RuntimeError("configured SSH private key is unavailable inside LightAgent")
    command = build_ssh_command(target, known_hosts, timeout)
    try:
        completed = subprocess.run(
            command,
            input=REMOTE_SCRIPT,
            text=True,
            capture_output=True,
            timeout=max(10, timeout + 20),
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("OpenSSH client is not installed in the LightAgent runtime") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("NAS status collection timed out") from exc
    if known_hosts.exists():
        try:
            known_hosts.chmod(0o600)
        except OSError:
            pass
    if completed.returncode != 0:
        raise RuntimeError(_friendly_ssh_error(completed.stderr))
    if not completed.stdout.startswith("NASMON|1\n"):
        raise RuntimeError("NAS returned an invalid status payload")
    return completed.stdout


def _parse_sections(payload: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    values: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in payload.splitlines()[1:]:
        if raw_line.startswith("KV|"):
            _, key, value = raw_line.split("|", 2)
            values[key] = value.strip()
        elif raw_line.startswith("BEGIN|"):
            current = raw_line.split("|", 1)[1]
            sections[current] = []
        elif raw_line.startswith("END|"):
            current = ""
        elif current:
            sections[current].append(raw_line)
    return values, sections


def detect_platform(os_lines: list[str], values: dict[str, str], configured: str) -> tuple[str, str]:
    text = "\n".join([*os_lines, values.get("dmi_vendor", ""), values.get("dmi_product", "")])
    lowered = text.lower()
    if "synology" in lowered or "dsm" in lowered or "/etc.defaults/version" in lowered:
        platform = "synology"
    elif any(token in lowered for token in ("fnos", "fn os", "fnnas", "feiniu", "trim")):
        platform = "fnos"
    elif any(token in lowered for token in ("zspace", "z-space", "zsos", "极空间")):
        platform = "zspace"
    elif any(token in lowered for token in ("ugreen", "ugos", "绿联")):
        platform = "ugreen"
    elif configured != "auto":
        platform = configured
    else:
        platform = "linux"

    pretty = ""
    product_version = ""
    build_number = ""
    for line in os_lines:
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("productversion="):
            product_version = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("buildnumber="):
            build_number = line.split("=", 1)[1].strip().strip('"')
    if platform == "synology" and product_version:
        pretty = f"DSM {product_version}" + (f"-{build_number}" if build_number else "")
    return platform, pretty or "Linux"


def _parse_filesystems(lines: list[str]) -> list[dict[str, Any]]:
    filesystems = []
    ignored_types = {"tmpfs", "devtmpfs", "overlay", "squashfs"}
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            continue
        device, blocks, used, available, capacity = fields[:5]
        mountpoint = " ".join(fields[5:])
        if device in ignored_types or device.startswith(("tmpfs", "devtmpfs", "overlay", "shm")):
            continue
        if device.startswith(("/dev/loop", "/dev/zram")) or mountpoint.startswith(("/snap/", "/var/lib/docker/")):
            continue
        total_kb = _as_int(blocks)
        if total_kb <= 0:
            continue
        filesystems.append({
            "mountpoint": mountpoint,
            "total_gb": round(total_kb / 1024 / 1024, 2),
            "used_gb": round(_as_int(used) / 1024 / 1024, 2),
            "available_gb": round(_as_int(available) / 1024 / 1024, 2),
            "used_percent": _as_int(capacity.rstrip("%")),
        })
    filesystems.sort(key=lambda item: (-item["total_gb"], item["mountpoint"]))
    return filesystems[:32]


def _parse_disks(lines: list[str]) -> list[dict[str, Any]]:
    if not lines:
        return []
    try:
        payload = json.loads("\n".join(lines))
    except json.JSONDecodeError:
        return []
    disks = []
    for item in payload.get("blockdevices") or []:
        if str(item.get("type") or "") != "disk":
            continue
        disks.append({
            "name": str(item.get("name") or ""),
            "model": str(item.get("model") or "").strip(),
            "size_gb": round(_as_int(item.get("size")) / 1000 / 1000 / 1000, 2),
            "transport": str(item.get("tran") or ""),
            "rotational": bool(item.get("rota")),
        })
    return disks[:32]


def _parse_mdstat(lines: list[str]) -> list[dict[str, Any]]:
    arrays = []
    for index, line in enumerate(lines):
        match = re.match(r"^(md\S+)\s*:\s*(\w+)\s+(\S+)", line.strip())
        if not match:
            continue
        detail = lines[index + 1].strip() if index + 1 < len(lines) else ""
        bitmap = "".join(re.findall(r"\[([U_]+)\]", detail))
        degraded = "_" in bitmap or match.group(2).lower() not in {"active", "readonly"}
        arrays.append({
            "name": match.group(1),
            "level": match.group(3),
            "state": "degraded" if degraded else "healthy",
            "members": bitmap,
        })
    return arrays


def _parse_key_values(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        if "|" in line:
            key, value = line.split("|", 1)
            result[key] = value
    return result


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _alert(alerts: list[dict[str, str]], severity: str, code: str, message: str) -> None:
    alerts.append({"severity": severity, "code": code, "message": message})


def parse_report(target: Target, payload: str) -> dict[str, Any]:
    values, sections = _parse_sections(payload)
    platform, os_name = detect_platform(sections.get("os_release", []), values, target.platform)
    cpu = round(_as_float(values.get("cpu_percent")), 1)
    cpu_count = max(1, _as_int(values.get("cpu_count"), 1))
    load1 = round(_as_float(values.get("load1")), 2)
    total_kb = _as_int(values.get("memory_total_kb"))
    available_kb = _as_int(values.get("memory_available_kb"))
    memory_percent = round(100 * (total_kb - available_kb) / total_kb, 1) if total_kb else 0.0
    temperature = round(_as_float(values.get("temperature_c")), 1) if values.get("temperature_c") else None
    filesystems = _parse_filesystems(sections.get("filesystems", []))
    arrays = _parse_mdstat(sections.get("mdstat", []))
    docker_raw = _parse_key_values(sections.get("docker", []))
    docker = {
        "installed": _as_bool(docker_raw.get("installed")),
        "accessible": _as_bool(docker_raw.get("accessible")),
        "total": _as_int(docker_raw.get("total")),
        "running": _as_int(docker_raw.get("running")),
        "unhealthy": _as_int(docker_raw.get("unhealthy")),
    }
    services = _parse_key_values(sections.get("services", []))
    alerts: list[dict[str, str]] = []

    if cpu >= 95:
        _alert(alerts, "critical", "cpu_high", f"CPU usage is {cpu}%")
    elif cpu >= 85:
        _alert(alerts, "warning", "cpu_high", f"CPU usage is {cpu}%")
    if memory_percent >= 95:
        _alert(alerts, "critical", "memory_high", f"Memory usage is {memory_percent}%")
    elif memory_percent >= 85:
        _alert(alerts, "warning", "memory_high", f"Memory usage is {memory_percent}%")
    normalized_load = round(load1 / cpu_count, 2)
    if normalized_load >= 2:
        _alert(alerts, "critical", "load_high", f"1-minute load per CPU is {normalized_load}")
    elif normalized_load >= 1:
        _alert(alerts, "warning", "load_high", f"1-minute load per CPU is {normalized_load}")
    if temperature is not None:
        if temperature >= 85:
            _alert(alerts, "critical", "temperature_high", f"Highest reported temperature is {temperature} C")
        elif temperature >= 75:
            _alert(alerts, "warning", "temperature_high", f"Highest reported temperature is {temperature} C")
    for filesystem in filesystems:
        used = filesystem["used_percent"]
        if used >= 95:
            _alert(alerts, "critical", "filesystem_full", f"{filesystem['mountpoint']} is {used}% full")
        elif used >= 85:
            _alert(alerts, "warning", "filesystem_full", f"{filesystem['mountpoint']} is {used}% full")
    for array in arrays:
        if array["state"] == "degraded":
            _alert(alerts, "critical", "raid_degraded", f"RAID array {array['name']} is degraded")
    if docker["unhealthy"]:
        _alert(alerts, "critical", "docker_unhealthy", f"{docker['unhealthy']} Docker container(s) are unhealthy")
    failed_services = sorted(name for name, state in services.items() if state == "failed")
    if failed_services:
        _alert(alerts, "warning", "service_failed", "Failed services: " + ", ".join(failed_services))

    severity_rank = {"healthy": 0, "warning": 1, "critical": 2}
    overall = "healthy"
    for alert in alerts:
        if severity_rank[alert["severity"]] > severity_rank[overall]:
            overall = alert["severity"]

    return {
        "name": target.name,
        "status": overall,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "hostname": values.get("hostname") or "unknown",
        "system": {
            "os": os_name,
            "kernel": values.get("kernel") or "unknown",
            "architecture": values.get("architecture") or "unknown",
            "uptime_seconds": _as_int(values.get("uptime_seconds")),
        },
        "resources": {
            "cpu_percent": cpu,
            "cpu_count": cpu_count,
            "load_1m": load1,
            "load_per_cpu": normalized_load,
            "memory_used_percent": memory_percent,
            "memory_total_gb": round(total_kb / 1024 / 1024, 2) if total_kb else 0.0,
            "temperature_c": temperature,
        },
        "storage": {
            "filesystems": filesystems,
            "disks": _parse_disks(sections.get("lsblk", [])),
            "raid_arrays": arrays,
            "smart_health": "unavailable_without_vendor_or_privileged_access",
        },
        "docker": docker,
        "services": services,
        "alerts": alerts,
    }


def collect_target(target: Target, timeout: int) -> dict[str, Any]:
    try:
        return parse_report(target, collect_remote(target, timeout))
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "name": target.name,
            "status": "critical",
            "platform": target.platform if target.platform != "auto" else "unknown",
            "alerts": [{
                "severity": "critical",
                "code": "collection_failed",
                "message": str(exc),
            }],
        }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1:
        print(json.dumps({"error": "status accepts at most one target name"}, ensure_ascii=False))
        return 2
    try:
        targets = load_targets()
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    if argv:
        targets = [target for target in targets if target.name == argv[0]]
        if not targets:
            print(json.dumps({"error": "requested NAS target is not configured"}, ensure_ascii=False))
            return 2
    saved_timeout = load_saved_config().get("timeout_seconds", 8)
    timeout = max(3, min(30, _as_int(os.environ.get("NAS_MONITOR_TIMEOUT_SECONDS"), saved_timeout)))
    reports = [collect_target(target, timeout) for target in targets]
    rank = {"healthy": 0, "warning": 1, "critical": 2}
    overall = max((report["status"] for report in reports), key=lambda value: rank[value])
    output = {
        "status": overall,
        "target_count": len(reports),
        "healthy_count": sum(report["status"] == "healthy" for report in reports),
        "warning_count": sum(report["status"] == "warning" for report in reports),
        "critical_count": sum(report["status"] == "critical" for report in reports),
        "targets": reports,
    }
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
