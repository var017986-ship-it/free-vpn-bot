from __future__ import annotations

import base64
import html
import re
from urllib.parse import quote

from aiohttp import web

from .config import get_settings
from .database import Database
from .plans import PLANS
from .xray import profile_uri


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def create_app() -> web.Application:
    settings = get_settings()
    database = Database(settings.database_path, settings.captcha_secret)
    app = web.Application()

    async def subscription(request: web.Request) -> web.Response:
        token = request.match_info["token"]
        activation = database.subscription(token)
        if not activation or not activation["profiles"]:
            raise web.HTTPNotFound(text="Подписка не найдена или больше не активна")

        plan = PLANS.get(activation["plan_code"])
        title = plan.title if plan else "Free VPN"
        slots = plan.slots if plan else 1
        raw_hwid = request.headers.get("X-Hwid", "").strip()
        device_count = activation["device_count"]
        if raw_hwid and re.fullmatch(r"[A-Za-z0-9._~+/=-]{10,128}", raw_hwid):
            forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            device_label = (
                request.headers.get("X-Device-Model")
                or request.headers.get("X-Device-Name")
                or request.headers.get("User-Agent")
                or "Happ"
            )
            allowed, device_count = database.register_subscription_device(
                activation["id"], raw_hwid, device_label, forwarded, slots
            )
            if not allowed:
                notice = _b64(f"Лимит устройств исчерпан: занято {device_count} из {slots}")
                dummy = "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1?security=none&type=tcp#Лимит%20устройств"
                return web.Response(
                    text=_b64(dummy),
                    content_type="text/plain",
                    headers={
                        "Cache-Control": "no-store",
                        "profile-title": "base64:" + _b64(title),
                        "announce": "base64:" + notice,
                        "profile-notice": "base64:" + notice,
                        "x-hwid-max-devices-reached": "1",
                        "x-hwid-limit": "true",
                    },
                )
        lines = [profile_uri(settings, activation["profiles"][0]["id"], "Финляндия")]
        public_url = f"{settings.subscription_base_url}/s/{token}"
        user_agent = request.headers.get("User-Agent", "").lower()
        accept = request.headers.get("Accept", "")
        if "text/html" in accept and "happ" not in user_agent:
            deep_link = f"happ://add/{public_url}"
            page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Free VPN</title><style>
body{{font-family:Arial,sans-serif;background:#0b1220;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0;padding:20px;box-sizing:border-box}}
.card{{max-width:420px;background:#111c2f;border:1px solid #29405f;border-radius:18px;padding:28px;text-align:center}}
a,button{{display:block;width:100%;box-sizing:border-box;padding:14px;margin-top:12px;border:0;border-radius:11px;font-size:16px;font-weight:700}}
a{{background:#35d0ba;color:#06110f;text-decoration:none}}button{{background:#243650;color:#fff}}
p{{color:#a9bad0;line-height:1.5}}code{{word-break:break-all;color:#79ead9}}</style></head>
<body><div class="card"><h1>Free VPN</h1><p>Подписка готова. Откройте её в Happ или скопируйте ссылку.</p>
<a href="{html.escape(deep_link)}">Открыть в Happ</a>
<button onclick="navigator.clipboard.writeText('{html.escape(public_url)}').then(()=>this.textContent='Скопировано')">Скопировать ссылку</button>
<p><code>{html.escape(public_url)}</code></p></div></body></html>"""
            return web.Response(text=page, content_type="text/html")

        remaining = max(0, slots - device_count)
        notice = _b64(f"Устройства: занято {device_count} из {slots} • осталось {remaining}")
        headers = {
            "Cache-Control": "no-store",
            "profile-title": "base64:" + _b64(title),
            "profile-update-interval": "6",
            "subscription-auto-update-open-enable": "1",
            "subscription-always-hwid-enable": "1",
            "subscription-userinfo": (
                f"upload=0; download={activation['used_bytes']}; "
                f"total={activation['quota_bytes']}; expire={activation['expires_at']}"
            ),
            "announce": "base64:" + notice,
            "profile-notice": "base64:" + notice,
            "support-url": settings.support_url,
            "Content-Disposition": f"inline; filename*=UTF-8''{quote('free-vpn.txt')}",
        }
        body = _b64("\n".join(lines))
        return web.Response(text=body, content_type="text/plain", charset="utf-8", headers=headers)

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/s/{token}", subscription)
    app.router.add_get("/health", health)
    return app


def main() -> None:
    web.run_app(create_app(), host="127.0.0.1", port=8080, access_log=None)


if __name__ == "__main__":
    main()
