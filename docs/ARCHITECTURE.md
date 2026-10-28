# Architecture

```text
Telegram user
    |
    | /start, captcha, plan activation
    v
Telegram Bot (aiogram) ---- SQLite database
    |                              |
    | synchronizes active UUIDs    | traffic, expiry, device records
    v                              v
Xray: VLESS TCP + REALITY <--- maintenance job (every 5 minutes)
    ^
    | VLESS connection
    |
VPN client

VPN client / Happ
    |
    | HTTPS GET /s/<secret-token>, X-Hwid
    v
Nginx -> Subscription service (aiohttp) -> SQLite database
```

## Components

| Component | Responsibility |
|---|---|
| `free_vpn_bot.bot` | Telegram menu, captcha, plan activation, admin actions, broadcasts |
| `free_vpn_bot.database` | SQLite schema, one-time activations, device limit, traffic and status |
| `free_vpn_bot.subscription` | Authenticated subscription feed and Happ-compatible response headers |
| `free_vpn_bot.xray` | VLESS URI generation, Xray configuration generation and stats parsing |
| `free_vpn_bot.sync_xray` | Rebuilds the Xray client list from active profiles |
| `free_vpn_bot.maintenance` | Accounts traffic, expires access and triggers synchronization |
| Nginx | HTTPS termination for the subscription endpoint |
| systemd | Restarts long-running services and runs maintenance periodically |

## Access lifecycle

1. A person passes the captcha. Only then is a user record created.
2. The person activates a free plan. A unique activation, VLESS UUID and subscription token are generated.
3. The bot runs the synchronization command. It builds an Xray configuration containing only active UUIDs.
4. Happ fetches `https://vpn.example.com/s/<token>`. The service returns one VLESS profile named `Finland` and quota/expiry metadata.
5. If Happ sends a valid `X-Hwid`, its HMAC-derived fingerprint is added to the activation until the plan's device limit is reached.
6. The maintenance timer reads Xray per-user counters every five minutes. It adds the traffic delta to the activation.
7. When a quota is exhausted, access expires, or an administrator blocks a user, the UUID is removed during the next Xray synchronization.

## VLESS and REALITY

The project uses Xray's VLESS inbound over TCP. Each active profile becomes an Xray client with a unique UUID. The client URI uses:

- `security=reality` for REALITY;
- `flow=xtls-rprx-vision` for Vision;
- an Xray server-side private REALITY key;
- a public key, short ID, SNI and destination supplied to clients.

The private REALITY key must stay only on the server. Do not commit it, put it in a public issue, or send it to users.
