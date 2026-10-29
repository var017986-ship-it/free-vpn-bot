from __future__ import annotations

import json
import shlex
import subprocess

from .config import get_settings
from .database import Database


def main() -> None:
    settings = get_settings()
    if not settings.mtg_host or not settings.mtg_sync_command:
        raise RuntimeError("MTG_HOST or MTG_SYNC_COMMAND is not configured")

    secrets = Database(settings.database_path, settings.captcha_secret).active_mtg_secrets(settings.mtg_domain)
    payload = json.dumps({"secrets": [{"user": str(telegram_id), "secret": secret} for telegram_id, secret in secrets]})
    result = subprocess.run(
        shlex.split(settings.mtg_sync_command),
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    if result.returncode:
        raise RuntimeError("MTG sync failed: " + result.stderr[-500:])


if __name__ == "__main__":
    main()
