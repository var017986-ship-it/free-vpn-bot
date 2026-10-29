from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from free_vpn_bot.database import AlreadyActivated, Database, NotVerified
from free_vpn_bot.plans import PLANS


class FreeVpnDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "free.sqlite3", "test-secret")
        self.user = 101

    def tearDown(self):
        self.temp.cleanup()

    def verify(self) -> None:
        challenge = self.db.issue_captcha(self.user)
        correct = next(value for value in challenge["choices"] if self.db.pass_captcha(self.user, challenge["id"], value, "tester", "Test"))
        self.assertIsInstance(correct, int)

    def test_wrong_captcha_never_creates_user(self):
        challenge = self.db.issue_captcha(self.user)
        wrong = max(challenge["choices"]) + 100
        self.assertFalse(self.db.pass_captcha(self.user, challenge["id"], wrong, "tester", "Test"))
        self.assertFalse(self.db.is_verified(self.user))
        self.assertEqual(self.db.statistics()["verified_users"], 0)

    def test_captcha_creates_verified_user_only_after_correct_answer(self):
        challenge = self.db.issue_captcha(self.user)
        accepted = any(self.db.pass_captcha(self.user, challenge["id"], value, "tester", "Test") for value in challenge["choices"])
        self.assertTrue(accepted)
        self.assertTrue(self.db.is_verified(self.user))
        self.assertEqual(self.db.statistics()["verified_users"], 1)

    def test_plan_is_one_time_and_creates_correct_slot_count(self):
        self.verify()
        activation, profiles = self.db.activate(self.user, "free")
        self.assertEqual(len(profiles), PLANS["free"].slots)
        self.assertEqual(activation["quota_bytes"], PLANS["free"].quota_bytes)
        with self.assertRaises(AlreadyActivated):
            self.db.activate(self.user, "free")
        _, family_profiles = self.db.activate(self.user, "family")
        self.assertEqual(len(family_profiles), PLANS["family"].slots)

    def test_failed_server_apply_can_be_retried(self):
        self.verify()
        activation, _ = self.db.activate(self.user, "free")
        self.db.discard_activation(activation["id"])
        retried, _ = self.db.activate(self.user, "free")
        self.assertEqual(retried["plan"].code, "free")

    def test_unverified_user_cannot_activate_plan(self):
        with self.assertRaises(NotVerified):
            self.db.activate(self.user, "free")

    def test_quota_exhaustion_revokes_every_profile(self):
        self.verify()
        activation, profiles = self.db.activate(self.user, "family")
        first = profiles[0]["id"]
        self.assertTrue(self.db.reconcile_usage({first: (PLANS["family"].quota_bytes, 0)}))
        item = self.db.user_activations(self.user)[0]
        self.assertEqual(item["status"], "exhausted")
        self.assertTrue(all(profile["status"] == "revoked" for profile in item["profiles"]))

    def test_block_revokes_access(self):
        self.verify()
        self.db.activate(self.user, "free")
        self.assertEqual(self.db.revoke_user(self.user), 1)
        self.assertFalse(self.db.is_verified(self.user))
        self.assertEqual(self.db.active_profile_ids(), [])
