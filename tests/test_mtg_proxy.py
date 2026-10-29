from __future__ import annotations

import tempfile
from pathlib import Path

from free_vpn_bot.database import Database


def verified_database(secret: str = "captchasecret") -> tuple[tempfile.TemporaryDirectory, Database]:
    temp = tempfile.TemporaryDirectory()
    database = Database(Path(temp.name) / "bot.sqlite3", secret)
    for telegram_id in (123, 456):
        challenge = database.issue_captcha(telegram_id)
        assert any(
            database.pass_captcha(telegram_id, challenge["id"], answer, "user", "First")
            for answer in challenge["choices"]
        )
    return temp, database


def test_fake_tls_secrets_are_stable_unique_and_include_domain() -> None:
    temp, database = verified_database()
    try:
        first = database.mtg_secret(123, "www.microsoft.com")
        assert first == database.mtg_secret(123, "www.microsoft.com")
        assert first.startswith("ee")
        assert first.endswith("7777772e6d6963726f736f66742e636f6d")
        assert first != database.mtg_secret(456, "www.microsoft.com")
    finally:
        temp.cleanup()


def test_blocked_users_are_removed_from_active_secret_list() -> None:
    temp, database = verified_database()
    try:
        assert len(database.active_mtg_secrets("www.microsoft.com")) == 2
        database.revoke_user(123)
        remaining = database.active_mtg_secrets("www.microsoft.com")
        assert [telegram_id for telegram_id, _ in remaining] == [456]
    finally:
        temp.cleanup()
