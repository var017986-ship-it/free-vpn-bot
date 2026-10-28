from __future__ import annotations

import tempfile
from pathlib import Path

from free_vpn_bot.database import AlreadyActivated, Database
from free_vpn_bot.plans import PLANS


def verified_database() -> tuple[tempfile.TemporaryDirectory[str], Database, int]:
    temp = tempfile.TemporaryDirectory()
    database = Database(Path(temp.name) / "free.sqlite3", "test-secret")
    telegram_id = 101
    challenge = database.issue_captcha(telegram_id)
    for choice in challenge["choices"]:
        if database.pass_captcha(telegram_id, challenge["id"], choice, "tester", "Test"):
            return temp, database, telegram_id
    raise AssertionError("captcha answer was not accepted")


def test_user_is_not_created_before_passing_captcha() -> None:
    with tempfile.TemporaryDirectory() as temp:
        database = Database(Path(temp) / "free.sqlite3", "test-secret")
        challenge = database.issue_captcha(101)
        assert not database.pass_captcha(101, challenge["id"], 999, "tester", "Test")
        assert database.statistics()["verified_users"] == 0


def test_plan_is_one_time_and_creates_one_finland_profile() -> None:
    temp, database, telegram_id = verified_database()
    try:
        activation, profiles = database.activate(telegram_id, "free")
        assert len(profiles) == 1
        assert profiles[0]["label"] == "Финляндия"
        assert activation["quota_bytes"] == PLANS["free"].quota_bytes
        try:
            database.activate(telegram_id, "free")
        except AlreadyActivated:
            pass
        else:
            raise AssertionError("same plan activation must be rejected")
    finally:
        temp.cleanup()


def test_device_limit_is_enforced_with_hashed_fingerprint() -> None:
    temp, database, telegram_id = verified_database()
    try:
        activation, _ = database.activate(telegram_id, "free")
        for number in range(PLANS["free"].slots):
            allowed, count = database.register_subscription_device(
                activation["id"], f"device-{number:04d}", "Happ", "203.0.113.10", PLANS["free"].slots
            )
            assert allowed
            assert count == number + 1
        allowed, count = database.register_subscription_device(
            activation["id"], "another-device-0001", "Happ", "203.0.113.11", PLANS["free"].slots
        )
        assert not allowed
        assert count == PLANS["free"].slots
    finally:
        temp.cleanup()
