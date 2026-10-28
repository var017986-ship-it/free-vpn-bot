from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _ids(value: str) -> frozenset[int]:
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return frozenset(result)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    database_path: Path
    public_host: str
    vless_port: int
    reality_server_name: str
    reality_public_key: str
    reality_short_id: str
    reality_private_key: str
    reality_dest: str
    xray_binary: str
    xray_config_path: Path
    xray_api_server: str
    sync_command: str
    captcha_secret: str
    support_url: str
    subscription_base_url: str

    @property
    def is_complete_for_bot(self) -> bool:
        return bool(self.bot_token and self.public_host and self.reality_public_key and self.captcha_secret)


def get_settings() -> Settings:
    return Settings(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        admin_ids=_ids(os.environ.get("ADMIN_IDS", "")),
        database_path=Path(os.environ.get("DATABASE_PATH", "/var/lib/free-vpn-bot/free-vpn.sqlite3")),
        public_host=os.environ.get("PUBLIC_HOST", "").strip(),
        vless_port=int(os.environ.get("VLESS_PORT", "443")),
        reality_server_name=os.environ.get("REALITY_SERVER_NAME", "www.microsoft.com").strip(),
        reality_public_key=os.environ.get("REALITY_PUBLIC_KEY", "").strip(),
        reality_short_id=os.environ.get("REALITY_SHORT_ID", "").strip(),
        reality_private_key=os.environ.get("REALITY_PRIVATE_KEY", "").strip(),
        reality_dest=os.environ.get("REALITY_DEST", "www.microsoft.com:443").strip(),
        xray_binary=os.environ.get("XRAY_BINARY", "/usr/local/bin/xray").strip(),
        xray_config_path=Path(os.environ.get("XRAY_CONFIG_PATH", "/etc/free-vpn-bot/xray.json")),
        xray_api_server=os.environ.get("XRAY_API_SERVER", "127.0.0.1:10085").strip(),
        sync_command=os.environ.get("SYNC_COMMAND", "/usr/bin/sudo -n /usr/local/bin/free-vpn-sync").strip(),
        captcha_secret=os.environ.get("CAPTCHA_SECRET", "").strip(),
        support_url=os.environ.get("SUPPORT_URL", "https://t.me/VPNsupportF").strip(),
        subscription_base_url=os.environ.get("SUBSCRIPTION_BASE_URL", "").strip().rstrip("/"),
    )
