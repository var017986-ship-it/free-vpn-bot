from __future__ import annotations

import shlex
import subprocess

from .config import get_settings
from .database import Database
from .xray import query_stats


def main() -> None:
    settings = get_settings()
    database = Database(settings.database_path, settings.captcha_secret)
    samples = query_stats(settings)
    if database.reconcile_usage(samples):
        result = subprocess.run(shlex.split(settings.sync_command), text=True, capture_output=True, timeout=60, check=False)
        if result.returncode:
            raise RuntimeError("Не удалось обновить Xray после ограничения тарифа: " + result.stderr[-500:])


if __name__ == "__main__":
    main()
