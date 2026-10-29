from __future__ import annotations

import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from free_vpn_bot.config import Settings
from free_vpn_bot.xray import build_xray_config, parse_stats, profile_uri


class FreeVpnXrayTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            bot_token="token", admin_ids=frozenset({1}), database_path=Path("/tmp/test.sqlite3"),
            public_host="9.247.112.11", vless_port=443, reality_server_name="www.microsoft.com",
            reality_public_key="public-key", reality_short_id="abcd1234", reality_private_key="private-key",
            reality_dest="www.microsoft.com:443", xray_binary="xray", xray_config_path=Path("/tmp/xray.json"),
            xray_api_server="127.0.0.1:10085", sync_command="sync", captcha_secret="secret",
            support_url="https://t.me/VPNsupportF",
            subscription_base_url="https://vpn.example.com",
        )

    def test_uri_is_vless_reality_vision(self):
        uri = profile_uri(self.settings, "11111111-1111-1111-1111-111111111111", "Телефон")
        parsed = urlsplit(uri)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "vless")
        self.assertEqual(parsed.hostname, "9.247.112.11")
        self.assertEqual(query["security"], ["reality"])
        self.assertEqual(query["flow"], ["xtls-rprx-vision"])
        self.assertEqual(query["encryption"], ["none"])

    def test_config_contains_only_given_clients_and_local_api(self):
        profiles = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]
        config = build_xray_config(self.settings, profiles)
        clients = config["inbounds"][0]["settings"]["clients"]
        self.assertEqual([client["id"] for client in clients], profiles)
        self.assertEqual(config["inbounds"][0]["port"], 443)
        self.assertEqual(len(config["inbounds"]), 1)
        self.assertEqual(config["api"]["listen"], "127.0.0.1:10085")
        self.assertTrue(config["policy"]["levels"]["0"]["statsUserDownlink"])

    def test_parses_json_statistics(self):
        profile_id = "11111111-1111-1111-1111-111111111111"
        payload = '{"stat":[{"name":"user>>>' + profile_id + '>>>traffic>>>uplink","value":11},{"name":"user>>>' + profile_id + '>>>traffic>>>downlink","value":22}]}'
        self.assertEqual(parse_stats(payload), {profile_id: (11, 22)})
