from __future__ import annotations

from dataclasses import dataclass

GIB = 1024 * 1024 * 1024


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    days: int
    quota_bytes: int
    slots: int


PLANS = {
    "free": Plan("free", "Бесплатный VPN", 30, 50 * GIB, 3),
    "family": Plan("family", "Семейный VPN", 7, 150 * GIB, 10),
}


def get_plan(code: str) -> Plan:
    try:
        return PLANS[code]
    except KeyError as exc:
        raise ValueError("Неизвестный тариф") from exc
