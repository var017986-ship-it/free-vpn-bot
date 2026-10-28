from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from free_vpn_bot.config import Settings
from free_vpn_bot.xray import build_xray_config, parse_stats, profile_uri


def settings() -> Settings:
    return Settings(
        bot_token="token",
        admin_ids=frozenset({1}),
        database_path=Path("/tmp/test.sqlite3"),
        public_host="vpn.example.com",
        vless_port=443,
        reality_server_name="www.microsoft.com",
        reality_public_key="public-key",
        reality_short_id="abcd1234",
        reality_private_key="private-key",
        reality_dest="www.microsoft.com:443",
        xray_binary="xray",
        xray_config_path=Path("/tmp/xray.json"),
        xray_api_server="127.0.0.1:10085",
        sync_command="sync",
        captcha_secret="secret",
        support_url="https://t.me/example_support",
        subscription_base_url="https://vpn.example.com",
    )


def test_uri_is_vless_reality_vision() -> None:
    uri = profile_uri(settings(), "11111111-1111-1111-1111-111111111111", "Finland")
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "vless"
    assert parsed.hostname == "vpn.example.com"
    assert query["security"] == ["reality"]
    assert query["flow"] == ["xtls-rprx-vision"]


def test_config_keeps_stats_api_local_and_only_active_clients() -> None:
    profile_id = "11111111-1111-1111-1111-111111111111"
    config = build_xray_config(settings(), [profile_id])
    assert config["api"]["listen"] == "127.0.0.1:10085"
    assert config["inbounds"][0]["settings"]["clients"][0]["id"] == profile_id


def test_parses_xray_json_statistics() -> None:
    profile_id = "11111111-1111-1111-1111-111111111111"
    payload = (
        '{"stat":[{"name":"user>>>' + profile_id + '>>>traffic>>>uplink","value":11},'
        '{"name":"user>>>' + profile_id + '>>>traffic>>>downlink","value":22}]}'
    )
    assert parse_stats(payload) == {profile_id: (11, 22)}
