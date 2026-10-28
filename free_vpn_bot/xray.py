from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, urlencode

from .config import Settings

try:
    import grp
except ImportError:  # pragma: no cover - Windows has no Unix groups.
    grp = None


class XrayError(RuntimeError):
    pass


def profile_uri(settings: Settings, profile_id: str, label: str) -> str:
    query = urlencode({
        "encryption": "none",
        "flow": "xtls-rprx-vision",
        "security": "reality",
        "sni": settings.reality_server_name,
        "fp": "chrome",
        "pbk": settings.reality_public_key,
        "sid": settings.reality_short_id,
        "type": "tcp",
    })
    return f"vless://{profile_id}@{settings.public_host}:{settings.vless_port}?{query}#{quote(label, safe='')}"


def build_xray_config(settings: Settings, profile_ids: list[str]) -> dict:
    if not settings.reality_private_key:
        raise XrayError("Не задан приватный ключ REALITY")
    clients = [{"id": item, "email": item, "flow": "xtls-rprx-vision", "level": 0} for item in profile_ids]
    return {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "listen": settings.xray_api_server, "services": ["StatsService"]},
        "stats": {},
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
        "inbounds": [
            {
                "tag": "vless-reality",
                "listen": "0.0.0.0",
                "port": settings.vless_port,
                "protocol": "vless",
                "settings": {"clients": clients, "decryption": "none"},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": settings.reality_dest,
                        "xver": 0,
                        "serverNames": [settings.reality_server_name],
                        "privateKey": settings.reality_private_key,
                        "shortIds": [settings.reality_short_id],
                    },
                },
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True},
            },
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct", "settings": {"domainStrategy": "UseIP"}},
            {"protocol": "blackhole", "tag": "block"},
        ],
        "routing": {"domainStrategy": "AsIs"},
    }


def _run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def sync_xray(settings: Settings, profile_ids: list[str]) -> None:
    config = build_xray_config(settings, profile_ids)
    destination = settings.xray_config_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    old = destination.read_bytes() if destination.exists() else None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", dir=destination.parent, delete=False) as temp:
        json.dump(config, temp, separators=(",", ":"))
        temp.write("\n")
        candidate = Path(temp.name)
    try:
        os.chmod(candidate, 0o640)
        if grp is not None:
            os.chown(candidate, 0, grp.getgrnam("xray").gr_gid)
        check = _run([settings.xray_binary, "run", "-test", "-config", str(candidate)], timeout=25)
        if check.returncode:
            diagnostic = (check.stderr + "\n" + check.stdout).strip()
            raise XrayError("Xray отклонил конфигурацию: " + diagnostic[-500:])
        os.replace(candidate, destination)
        restart = _run(["/bin/systemctl", "restart", "free-vpn-xray.service"], timeout=35)
        if restart.returncode:
            if old is not None:
                destination.write_bytes(old)
                _run(["/bin/systemctl", "restart", "free-vpn-xray.service"], timeout=35)
            raise XrayError("Не удалось перезапустить Xray: " + restart.stderr[-500:])
    finally:
        candidate.unlink(missing_ok=True)


_STAT_PATTERN = re.compile(
    r'user>>>([0-9a-f-]{36})>>>traffic>>>(uplink|downlink).*?value\s*:\s*(\d+)',
    re.IGNORECASE | re.DOTALL,
)


def parse_stats(payload: str) -> dict[str, tuple[int, int]]:
    raw: dict[str, list[int]] = {}
    try:
        items = json.loads(payload).get("stat", [])
    except (ValueError, AttributeError):
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        match = re.fullmatch(r"user>>>([0-9a-f-]{36})>>>traffic>>>(uplink|downlink)", str(item.get("name", "")), re.I)
        if not match:
            continue
        profile_id, direction = match.groups()
        counters = raw.setdefault(profile_id, [0, 0])
        counters[0 if direction.lower() == "uplink" else 1] = int(item.get("value", 0))
    if not raw:
        for profile_id, direction, value in _STAT_PATTERN.findall(payload):
            counters = raw.setdefault(profile_id, [0, 0])
            counters[0 if direction.lower() == "uplink" else 1] = int(value)
    return {profile_id: (values[0], values[1]) for profile_id, values in raw.items()}


def query_stats(settings: Settings) -> dict[str, tuple[int, int]]:
    result = _run([settings.xray_binary, "api", "statsquery", f"--server={settings.xray_api_server}", "-pattern", "user>>>"], timeout=20)
    if result.returncode:
        raise XrayError("Не удалось прочитать статистику Xray: " + result.stderr[-500:])
    return parse_stats(result.stdout)
