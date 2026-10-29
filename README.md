# Free VPN Bot

> A Telegram bot that automates free VLESS access: captcha, one-time plans, HTTPS subscriptions, device limits and Xray traffic accounting.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-aiogram-26A5E4?logo=telegram&logoColor=white)
![Xray](https://img.shields.io/badge/Xray-VLESS%20REALITY-111827)
![License](https://img.shields.io/badge/license-MIT-16a34a)

This repository contains **only the free VPN bot**. It has no payments, paid plans, checkout flow, payment-provider integration or customer billing data.

## What It Does

- Requires a short captcha before a Telegram account becomes a user.
- Provides two free, one-time plans per Telegram account.
- Creates a personal HTTPS subscription URL for each activation.
- Serves one VLESS TCP + REALITY + Vision profile named **Finland**.
- Supports Happ subscription metadata: quota, expiry, auto-update and a device-limit notice.
- Tracks device slots separately from VLESS profiles using `X-Hwid`.
- Reads per-profile upload/download counters from the local Xray API every five minutes.
- Removes access automatically on expiry, quota exhaustion or administrator block.
- Includes admin actions for statistics, plan summary, blocking and Telegram broadcasts.
- Optionally provides per-user Telegram FakeTLS proxy links, revocable by blocking the account.

## Free Plans

| Plan | Duration | Traffic | Devices | Rule |
|---|---:|---:|---:|---|
| Free VPN | 30 days | 50 GiB | 3 | One activation per Telegram account |
| Family VPN | 7 days | 150 GiB | 10 | One activation per Telegram account |

Plan definitions are intentionally small and explicit in [`free_vpn_bot/plans.py`](free_vpn_bot/plans.py).

## Architecture

```text
Telegram -> aiogram bot -> SQLite
                         |
                         +-> sync active UUIDs -> Xray -> Internet

Happ -> HTTPS subscription -> Nginx -> aiohttp subscription service -> SQLite
```

See [architecture details](docs/ARCHITECTURE.md), including the activation lifecycle and VLESS/REALITY parameters.

## Repository Layout

```text
free_vpn_bot/       Application package
deploy/             systemd, Nginx and sudo templates
docs/               Architecture and data-handling notes
.env.example        Safe environment-variable template
requirements.txt    Python dependencies
```

## Requirements

- A Linux VPS with a public IP address.
- Python 3.11 or newer.
- Xray installed at `/usr/local/bin/xray` or another path set in `XRAY_BINARY`.
- Nginx and a valid TLS certificate for the subscription domain.
- Optional: the pinned `mtg-multi` v1.13.0 binary for the FakeTLS feature.
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- A non-root service account named `freevpn` and an Xray service account/group named `xray`.

## Quick Start

The commands below use Ubuntu/Debian-style paths. Review and adapt them before running on a production host.

1. Copy this repository to `/opt/free-vpn-bot` and create a virtual environment.

   ```bash
   sudo mkdir -p /opt/free-vpn-bot /etc/free-vpn-bot
   sudo chown -R "$USER":"$USER" /opt/free-vpn-bot
   cd /opt/free-vpn-bot
   python3 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -r requirements.txt
   ```

2. Create a dedicated service account and writable state directory.

   ```bash
   sudo useradd --system --home /var/lib/free-vpn-bot --shell /usr/sbin/nologin freevpn
   sudo install -d -o freevpn -g freevpn -m 0750 /var/lib/free-vpn-bot
   ```

3. Create `/etc/free-vpn-bot/bot.env` from [`.env.example`](.env.example). Generate the REALITY key pair using the Xray build you installed:

   ```bash
   /usr/local/bin/xray x25519
   openssl rand -hex 8
   openssl rand -hex 32
   ```

   Put the private key and captcha secret only in `/etc/free-vpn-bot/bot.env`, then restrict it:

   ```bash
   sudo chown root:freevpn /etc/free-vpn-bot/bot.env
   sudo chmod 0640 /etc/free-vpn-bot/bot.env
   sudo install -o root -g root -m 0600 /dev/null /etc/free-vpn-bot/xray.env
   ```

4. Install the synchronization wrapper and narrowly scoped sudo rule.

   ```bash
   sudo install -o root -g root -m 0755 deploy/free-vpn-sync /usr/local/bin/free-vpn-sync
   sudo install -o root -g root -m 0440 deploy/free-vpn-sync.sudoers /etc/sudoers.d/free-vpn-sync
   sudo visudo -cf /etc/sudoers.d/free-vpn-sync
   ```

5. Install the systemd unit files and start the application services.

   ```bash
   sudo install -o root -g root -m 0644 deploy/free-vpn-*.service /etc/systemd/system/
   sudo install -o root -g root -m 0644 deploy/free-vpn-maintenance.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now free-vpn-bot free-vpn-subscription free-vpn-maintenance.timer
   ```

   The first successful plan activation generates `/etc/free-vpn-bot/xray.json` and restarts `free-vpn-xray`. You can also run `sudo -u freevpn /usr/local/bin/free-vpn-sync` after the database exists.

6. Adapt `deploy/free-vpn-subscription-nginx.conf.example` with your domain and certificate paths. Install it as an Nginx site, test Nginx, then reload it.

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

7. Check service health.

   ```bash
   sudo systemctl status free-vpn-bot free-vpn-subscription free-vpn-maintenance.timer
   curl -fsS http://127.0.0.1:8080/health
   ```

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `BOT_TOKEN` | Yes | Telegram bot token |
| `ADMIN_IDS` | Yes | Comma-separated Telegram numeric IDs for administrators |
| `CAPTCHA_SECRET` | Yes | Long random secret for captcha and device-fingerprint HMACs |
| `SUBSCRIPTION_BASE_URL` | Yes | Public HTTPS base, for example `https://vpn.example.com` |
| `PUBLIC_HOST` | Yes | Public VLESS host or IP |
| `REALITY_PUBLIC_KEY` | Yes | Public REALITY key included in the client URI |
| `REALITY_PRIVATE_KEY` | Yes | Private REALITY key, server only |
| `REALITY_SHORT_ID` | Yes | REALITY short ID |
| `REALITY_SERVER_NAME` | Yes | SNI accepted by REALITY |
| `REALITY_DEST` | Yes | REALITY destination in `host:port` form |
| `DATABASE_PATH` | No | SQLite path, default: `/var/lib/free-vpn-bot/free-vpn.sqlite3` |
| `VLESS_PORT` | No | VLESS listener port, default: `443` |
| `XRAY_BINARY` | No | Xray executable path |
| `XRAY_CONFIG_PATH` | No | Generated Xray configuration path |
| `XRAY_API_SERVER` | No | Local Xray stats API, default: `127.0.0.1:10085` |
| `SYNC_COMMAND` | No | Command the bot runs after access changes |
| `SUPPORT_URL` | No | HTTPS Telegram support URL |
| `MTG_HOST` | No | Public hostname or IPv4 for the optional FakeTLS proxy |
| `MTG_PORT` | No | FakeTLS listener port, default `9445` |
| `MTG_DOMAIN` | No | FakeTLS SNI hostname, default `www.microsoft.com` |
| `MTG_SYNC_COMMAND` | No | Restricted root helper that reloads active per-user secrets |

### Optional Telegram FakeTLS proxy

The main menu includes a separate **Telegram FakeTLS** button when `MTG_HOST` and `MTG_SYNC_COMMAND` are configured. It creates an `ee` FakeTLS secret derived per verified user, updates the MTG multi-secret configuration, and returns a Telegram proxy link. Blocking an account removes its secret on the next sync. This feature is separate from VLESS and the regular MTProxy fallback. FakeTLS availability depends on the network and fronting hostname; a working TCP port alone does not prove the Telegram handshake works.

Deployment templates and the design/operational notes are in [`deploy/`](deploy/) and [`docs/FAKETLS.md`](docs/FAKETLS.md). Install a pinned `mtg-multi` release, configure `/etc/free-vpn-bot/bot.env`, allow only the fixed sync helper through sudo, install `deploy/mtg-faketls.service`, then open the selected TCP port in the host firewall.

## Privacy and Logs

The application stores account, activation, quota, device-limit and subscription data required to operate the free service. It does not record browsing history or VPN payload contents. Nginx and the operating system may still retain normal access and service logs.

Read the full [data and privacy note](docs/PRIVACY.md) before operating a public instance.

## Security Checklist

- Keep `.env`, database files, REALITY private keys, VLESS UUIDs and subscription tokens out of Git.
- Keep the Xray stats API bound to `127.0.0.1` only.
- Use a real HTTPS certificate for subscription links.
- Restrict `/etc/free-vpn-bot/bot.env` to the service account group.
- Review the sudo rule before installing it; it should allow only the sync wrapper.
- Back up the SQLite database securely if retaining users and activations matters.
- Apply the laws and hosting-provider rules that apply to your server and users.

## Development

Run the bot and subscription service manually after exporting environment variables from a local, untracked `.env` file:

```bash
.venv/bin/python -m free_vpn_bot.bot
.venv/bin/python -m free_vpn_bot.subscription
```

The subscription service listens only on `127.0.0.1:8080`; put Nginx in front of it for public HTTPS access.

## License

Released under the [MIT License](LICENSE).
