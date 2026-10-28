# Data and Privacy

This project stores operational data needed to issue and limit free access. It does not inspect or store the contents of VPN traffic.

## Stored in SQLite

| Data | Purpose |
|---|---|
| Telegram ID, first name and username when available | Bot identity, access control and broadcasts |
| Captcha challenge digest, expiry and attempt count | Prevents creating user records before verification |
| Plan type, activation and expiry time, quota, status | Enforces free-plan limits |
| VLESS profile UUID | Authenticates a client to Xray |
| Subscription token | Authenticates a subscription URL |
| Traffic counters and aggregate byte total | Applies the quota |
| HMAC-derived device fingerprint, label, timestamps and latest subscription-request IP | Applies the device limit and supports troubleshooting |

## Not stored by the application

- Website history.
- DNS request history.
- VPN payload contents.
- Telegram passwords or user messages.
- Payment data. This repository implements free plans only.

## Operational logs

The operating system, Nginx and systemd can keep logs. Nginx access logs commonly include the requesting IP, time, path and user agent. Configure log rotation and retention according to the rules that apply to the operator.

Xray is generated with `loglevel: warning`; it is not configured by this project to write a per-site browsing log.

## Operator responsibilities

Before deploying publicly, publish a privacy notice with the actual server location, retention periods, support contact and applicable legal basis. Protect the database and environment file with filesystem permissions and do not expose the local Xray API outside `127.0.0.1`.
