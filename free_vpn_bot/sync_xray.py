from __future__ import annotations

from .config import get_settings
from .database import Database
from .xray import sync_xray


def main() -> None:
    settings = get_settings()
    database = Database(settings.database_path, settings.captcha_secret)
    sync_xray(settings, database.active_profile_ids())


if __name__ == "__main__":
    main()
