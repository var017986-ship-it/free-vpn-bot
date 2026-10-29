from __future__ import annotations

import hashlib
import hmac
import random
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .plans import Plan, get_plan


class DatabaseError(RuntimeError):
    pass


class AlreadyActivated(DatabaseError):
    pass


class NotVerified(DatabaseError):
    pass


class UnknownUser(DatabaseError):
    pass


class Database:
    def __init__(self, path: Path, captcha_secret: str):
        self.path = path
        self.captcha_secret = captcha_secret.encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.session() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    blocked_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS captcha_challenges (
                    telegram_id INTEGER PRIMARY KEY,
                    challenge_id TEXT NOT NULL UNIQUE,
                    answer_digest TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS activations (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    plan_code TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','expired','exhausted','revoked')),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    quota_bytes INTEGER NOT NULL,
                    used_bytes INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(telegram_id, plan_code)
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    activation_id TEXT NOT NULL REFERENCES activations(id),
                    label TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
                    created_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS profile_usage (
                    profile_id TEXT PRIMARY KEY REFERENCES profiles(id),
                    last_uplink INTEGER NOT NULL DEFAULT 0,
                    last_downlink INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    sampled_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscription_devices (
                    id TEXT PRIMARY KEY,
                    activation_id TEXT NOT NULL REFERENCES activations(id),
                    fingerprint TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    last_ip TEXT NOT NULL DEFAULT '',
                    UNIQUE(activation_id, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_activations_active ON activations(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_profiles_active ON profiles(status);
                CREATE INDEX IF NOT EXISTS idx_subscription_devices_activation ON subscription_devices(activation_id);
                """
            )
            activation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(activations)")}
            if "subscription_token" not in activation_columns:
                conn.execute("ALTER TABLE activations ADD COLUMN subscription_token TEXT")
            for row in conn.execute("SELECT id FROM activations WHERE subscription_token IS NULL OR subscription_token='' "):
                conn.execute(
                    "UPDATE activations SET subscription_token=? WHERE id=?",
                    (secrets.token_urlsafe(32), row["id"]),
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_activations_subscription_token "
                "ON activations(subscription_token)"
            )
            now = int(time.time())
            for activation in conn.execute("SELECT id FROM activations WHERE status='active'").fetchall():
                profiles = conn.execute(
                    "SELECT id FROM profiles WHERE activation_id=? AND status='active' ORDER BY created_at,id",
                    (activation["id"],),
                ).fetchall()
                if not profiles:
                    continue
                conn.execute("UPDATE profiles SET label='Финляндия' WHERE id=?", (profiles[0]["id"],))
                for profile in profiles[1:]:
                    conn.execute(
                        "UPDATE profiles SET status='revoked',revoked_at=? WHERE id=?",
                        (now, profile["id"]),
                    )

    def _answer_digest(self, telegram_id: int, challenge_id: str, answer: int) -> str:
        value = f"{telegram_id}:{challenge_id}:{answer}".encode("utf-8")
        return hmac.new(self.captcha_secret, value, hashlib.sha256).hexdigest()

    def issue_captcha(self, telegram_id: int) -> dict:
        rng = random.SystemRandom()
        left, right = rng.randint(4, 30), rng.randint(3, 25)
        answer = left + right
        choices = {answer}
        while len(choices) < 4:
            choices.add(max(0, answer + rng.choice((-8, -5, -3, 3, 5, 8))))
        ordered = list(choices)
        rng.shuffle(ordered)
        challenge_id = secrets.token_urlsafe(9)
        with self.session() as conn:
            conn.execute(
                """INSERT INTO captcha_challenges(telegram_id,challenge_id,answer_digest,expires_at,attempts)
                   VALUES(?,?,?,?,0)
                   ON CONFLICT(telegram_id) DO UPDATE SET challenge_id=excluded.challenge_id,
                   answer_digest=excluded.answer_digest,expires_at=excluded.expires_at,attempts=0""",
                (telegram_id, challenge_id, self._answer_digest(telegram_id, challenge_id, answer), int(time.time()) + 600),
            )
        return {"id": challenge_id, "question": f"{left} + {right} = ?", "choices": ordered}

    def pass_captcha(self, telegram_id: int, challenge_id: str, answer: int, username: str | None, first_name: str | None) -> bool:
        now = int(time.time())
        with self.session() as conn:
            row = conn.execute("SELECT * FROM captcha_challenges WHERE telegram_id=?", (telegram_id,)).fetchone()
            if not row or row["challenge_id"] != challenge_id or row["expires_at"] < now:
                return False
            valid = hmac.compare_digest(row["answer_digest"], self._answer_digest(telegram_id, challenge_id, answer))
            if not valid:
                conn.execute("UPDATE captcha_challenges SET attempts=attempts+1 WHERE telegram_id=?", (telegram_id,))
                return False
            conn.execute(
                """INSERT INTO users(telegram_id,username,first_name,created_at)
                   VALUES(?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username,
                   first_name=excluded.first_name""",
                (telegram_id, username or "", first_name or "", now),
            )
            conn.execute("DELETE FROM captcha_challenges WHERE telegram_id=?", (telegram_id,))
            return True

    def is_verified(self, telegram_id: int) -> bool:
        with self.session() as conn:
            return conn.execute("SELECT 1 FROM users WHERE telegram_id=? AND blocked_at IS NULL", (telegram_id,)).fetchone() is not None

    def mtg_secret(self, telegram_id: int, hostname: str) -> str:
        """Return a stable, per-user FakeTLS secret for the configured domain."""
        digest = hmac.new(self.captcha_secret, f"mtg:{telegram_id}".encode("utf-8"), hashlib.sha256).digest()[:16]
        host = hostname.encode("utf-8")
        return "ee" + digest.hex() + host.hex()

    def active_mtg_secrets(self, hostname: str) -> list[tuple[int, str]]:
        with self.session() as conn:
            rows = conn.execute("SELECT telegram_id FROM users WHERE blocked_at IS NULL ORDER BY telegram_id").fetchall()
            return [(row["telegram_id"], self.mtg_secret(row["telegram_id"], hostname)) for row in rows]

    def activate(self, telegram_id: int, plan_code: str) -> tuple[dict, list[dict]]:
        plan = get_plan(plan_code)
        now = int(time.time())
        activation_id = str(uuid.uuid4())
        subscription_token = secrets.token_urlsafe(32)
        with self.session() as conn:
            user = conn.execute("SELECT blocked_at FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
            if not user or user["blocked_at"] is not None:
                raise NotVerified("Сначала пройдите проверку")
            if conn.execute("SELECT 1 FROM activations WHERE telegram_id=? AND plan_code=?", (telegram_id, plan.code)).fetchone():
                raise AlreadyActivated("Этот тариф уже был активирован на вашем аккаунте")
            conn.execute(
                """INSERT INTO activations(id,telegram_id,plan_code,status,created_at,expires_at,quota_bytes,used_bytes,subscription_token)
                   VALUES(?,?,?,?,?,?,?,0,?)""",
                (activation_id, telegram_id, plan.code, "active", now, now + plan.days * 86_400, plan.quota_bytes, subscription_token),
            )
            profile_id = str(uuid.uuid4())
            profiles = [{"id": profile_id, "label": "Финляндия", "status": "active"}]
            conn.execute(
                "INSERT INTO profiles(id,activation_id,label,status,created_at) VALUES(?,?,?,?,?)",
                (profile_id, activation_id, "Финляндия", "active", now),
            )
            conn.execute("INSERT INTO profile_usage(profile_id,sampled_at) VALUES(?,?)", (profile_id, now))
        activation = {
            "id": activation_id,
            "plan": plan,
            "expires_at": now + plan.days * 86_400,
            "quota_bytes": plan.quota_bytes,
            "subscription_token": subscription_token,
        }
        return activation, profiles

    def subscription(self, token: str) -> dict | None:
        now = int(time.time())
        with self.session() as conn:
            row = conn.execute(
                """SELECT a.*,u.blocked_at FROM activations a
                   JOIN users u ON u.telegram_id=a.telegram_id
                   WHERE a.subscription_token=?""",
                (token,),
            ).fetchone()
            if not row:
                return None
            activation = dict(row)
            if (
                activation["status"] != "active"
                or activation["expires_at"] <= now
                or activation["blocked_at"] is not None
            ):
                return None
            activation["profiles"] = [
                dict(profile)
                for profile in conn.execute(
                    "SELECT * FROM profiles WHERE activation_id=? AND status='active' ORDER BY created_at",
                    (activation["id"],),
                ).fetchall()
            ]
            activation["device_count"] = conn.execute(
                "SELECT COUNT(*) FROM subscription_devices WHERE activation_id=?",
                (activation["id"],),
            ).fetchone()[0]
            return activation

    def register_subscription_device(
        self,
        activation_id: str,
        raw_hwid: str,
        label: str,
        ip_address: str,
        limit: int,
    ) -> tuple[bool, int]:
        fingerprint = hmac.new(
            self.captcha_secret,
            f"{activation_id}:{raw_hwid}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        now = int(time.time())
        with self.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM subscription_devices WHERE activation_id=? AND fingerprint=?",
                (activation_id, fingerprint),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE subscription_devices SET label=?,last_seen_at=?,last_ip=?
                       WHERE id=?""",
                    (label[:100], now, ip_address[:45], existing["id"]),
                )
                count = conn.execute(
                    "SELECT COUNT(*) FROM subscription_devices WHERE activation_id=?",
                    (activation_id,),
                ).fetchone()[0]
                return True, count
            count = conn.execute(
                "SELECT COUNT(*) FROM subscription_devices WHERE activation_id=?",
                (activation_id,),
            ).fetchone()[0]
            if count >= limit:
                return False, count
            conn.execute(
                """INSERT INTO subscription_devices
                   (id,activation_id,fingerprint,label,first_seen_at,last_seen_at,last_ip)
                   VALUES(?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), activation_id, fingerprint, label[:100], now, now, ip_address[:45]),
            )
            return True, count + 1

    def revoke_activation(self, activation_id: str, status: str = "revoked") -> None:
        if status not in {"revoked", "expired", "exhausted"}:
            raise ValueError("Некорректный статус")
        now = int(time.time())
        with self.session() as conn:
            conn.execute("UPDATE activations SET status=? WHERE id=? AND status='active'", (status, activation_id))
            conn.execute("UPDATE profiles SET status='revoked',revoked_at=? WHERE activation_id=? AND status='active'", (now, activation_id))

    def discard_activation(self, activation_id: str) -> None:
        """Remove a just-created activation when applying it to Xray failed.

        This is deliberately different from revocation: a user must be able to
        retry a tariff if the server itself was temporarily unavailable.
        """
        with self.session() as conn:
            conn.execute(
                "DELETE FROM profile_usage WHERE profile_id IN (SELECT id FROM profiles WHERE activation_id=?)",
                (activation_id,),
            )
            conn.execute("DELETE FROM profiles WHERE activation_id=?", (activation_id,))
            conn.execute("DELETE FROM activations WHERE id=?", (activation_id,))

    def revoke_user(self, telegram_id: int) -> int:
        now = int(time.time())
        with self.session() as conn:
            if not conn.execute("SELECT 1 FROM users WHERE telegram_id=?", (telegram_id,)).fetchone():
                raise UnknownUser("Пользователь не найден")
            conn.execute("UPDATE users SET blocked_at=? WHERE telegram_id=?", (now, telegram_id))
            ids = [row["id"] for row in conn.execute("SELECT id FROM activations WHERE telegram_id=? AND status='active'", (telegram_id,))]
            conn.execute("UPDATE activations SET status='revoked' WHERE telegram_id=? AND status='active'", (telegram_id,))
            conn.execute("""UPDATE profiles SET status='revoked',revoked_at=? WHERE activation_id IN
                         (SELECT id FROM activations WHERE telegram_id=?) AND status='active'""", (now, telegram_id))
            return len(ids)

    def active_profile_ids(self) -> list[str]:
        now = int(time.time())
        with self.session() as conn:
            rows = conn.execute(
                """SELECT p.id FROM profiles p JOIN activations a ON a.id=p.activation_id
                   JOIN users u ON u.telegram_id=a.telegram_id
                   WHERE p.status='active' AND a.status='active' AND a.expires_at>? AND u.blocked_at IS NULL
                   ORDER BY p.created_at""", (now,)
            ).fetchall()
            return [row["id"] for row in rows]

    def user_activations(self, telegram_id: int) -> list[dict]:
        now = int(time.time())
        with self.session() as conn:
            activations = []
            for row in conn.execute("SELECT * FROM activations WHERE telegram_id=? ORDER BY created_at", (telegram_id,)).fetchall():
                activation = dict(row)
                if activation["status"] == "active" and activation["expires_at"] <= now:
                    activation["status"] = "expired"
                activation["profiles"] = [dict(p) for p in conn.execute(
                    "SELECT * FROM profiles WHERE activation_id=? AND status='active' ORDER BY created_at",
                    (activation["id"],),
                ).fetchall()]
                activation["device_count"] = conn.execute(
                    "SELECT COUNT(*) FROM subscription_devices WHERE activation_id=?",
                    (activation["id"],),
                ).fetchone()[0]
                activations.append(activation)
            return activations

    def reconcile_usage(self, samples: dict[str, tuple[int, int]], now: int | None = None) -> bool:
        now = now or int(time.time())
        changed = False
        with self.session() as conn:
            for profile_id, (uplink, downlink) in samples.items():
                row = conn.execute(
                    """SELECT u.last_uplink,u.last_downlink,u.total_bytes,p.activation_id
                       FROM profile_usage u JOIN profiles p ON p.id=u.profile_id
                       WHERE u.profile_id=? AND p.status='active'""", (profile_id,)
                ).fetchone()
                if not row:
                    continue
                delta_up = uplink - row["last_uplink"] if uplink >= row["last_uplink"] else uplink
                delta_down = downlink - row["last_downlink"] if downlink >= row["last_downlink"] else downlink
                delta = max(0, delta_up) + max(0, delta_down)
                conn.execute("""UPDATE profile_usage SET last_uplink=?,last_downlink=?,total_bytes=total_bytes+?,sampled_at=?
                             WHERE profile_id=?""", (uplink, downlink, delta, now, profile_id))
                if delta:
                    conn.execute("UPDATE activations SET used_bytes=used_bytes+? WHERE id=?", (delta, row["activation_id"]))
            due = conn.execute("""SELECT id,CASE WHEN expires_at<=? THEN 'expired' ELSE 'exhausted' END AS next_status
                                  FROM activations WHERE status='active' AND (expires_at<=? OR used_bytes>=quota_bytes)""", (now, now)).fetchall()
            for row in due:
                conn.execute("UPDATE activations SET status=? WHERE id=?", (row["next_status"], row["id"]))
                conn.execute("UPDATE profiles SET status='revoked',revoked_at=? WHERE activation_id=? AND status='active'", (now, row["id"]))
                changed = True
        return changed

    def statistics(self) -> dict:
        with self.session() as conn:
            result = {
                "verified_users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "blocked_users": conn.execute("SELECT COUNT(*) FROM users WHERE blocked_at IS NOT NULL").fetchone()[0],
                "active_free": conn.execute("SELECT COUNT(*) FROM activations WHERE status='active' AND plan_code='free'").fetchone()[0],
                "active_family": conn.execute("SELECT COUNT(*) FROM activations WHERE status='active' AND plan_code='family'").fetchone()[0],
                "traffic_bytes": conn.execute("SELECT COALESCE(SUM(used_bytes),0) FROM activations").fetchone()[0],
            }
            return result

    def tariff_summary(self) -> list[dict]:
        with self.session() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT plan_code,status,COUNT(*) AS count FROM activations GROUP BY plan_code,status ORDER BY plan_code,status"
            ).fetchall()]

    def broadcast_targets(self) -> list[int]:
        with self.session() as conn:
            return [row["telegram_id"] for row in conn.execute("SELECT telegram_id FROM users WHERE blocked_at IS NULL ORDER BY created_at").fetchall()]
