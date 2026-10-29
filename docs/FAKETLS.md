# Per-user MTProto FakeTLS Proxy Design

## Goal

Add an optional FakeTLS-based Telegram proxy to the free VPN bot, with one revocable proxy secret per verified Telegram account. Keep the currently working MTProxy available as a fallback and do not change Xray/VLESS listeners.

## Selected implementation

- Use `dolonet/mtg-multi` v1.13.0, a multi-secret fork of MTG with FakeTLS and SIGHUP secret reload.
- Run it as a separate service on TCP port `9445`; preserve the existing official MTProxy on `8444` and its fallback mapping on `9443`.
- Generate a stable FakeTLS secret per Telegram user using HMAC with the existing server-only `CAPTCHA_SECRET` and that user's Telegram ID. Do not persist plaintext secrets in SQLite.
- Store the active secret set in a root-owned MTG config file. A narrow root-owned sync helper atomically regenerates that file from verified, unblocked users and signals MTG to reload it.
- Add a distinct bot menu button for FakeTLS. It returns an `https://t.me/proxy?...` link using the user's FakeTLS secret and port `9443`. Keep the standard proxy option separately for fallback.
- On user block, synchronize the secret set so that the user's FakeTLS secret is revoked.

## FakeTLS domain

Use `www.microsoft.com` for the first test link, as previously selected. The VPS's provider ASN does not match Microsoft's network, so domain-fronting plausibility is not guaranteed. This is an explicit test configuration; validate from the target mobile carrier before considering it reliable. A domain associated with the VPS provider or a separately controlled, properly hosted domain may be a better follow-up.

## Failure handling

- Do not remove or stop the existing MTProxy on `8444` during initial deployment.
- Validate generated configuration with `mtg-multi doctor` before reloading.
- Write config atomically; on validation or reload failure, keep the current running config and report failure to the requesting bot user.
- Keep the new button hidden/unavailable until the MTG service and port are healthy.

## Verification

- Unit-test per-user secret stability, uniqueness across Telegram IDs, and required FakeTLS `ee` format/domain encoding.
- Test config generation and sync behavior for verified/unblocked users only.
- Check the MTG process, active config parsing, and TCP reachability on `9445`.
- Generate the bot link and verify host, port, and per-user secret match the loaded MTG config.
- Test with Telegram on the target mobile network; successful TCP reachability alone does not prove FakeTLS connectivity.

## Security

- Never put the root password, bot token, `CAPTCHA_SECRET`, or generated per-user secrets in logs, source, or Git history.
- Restrict MTG config and the sync helper to root; allow the bot account to invoke only the fixed helper via sudo.
- Keep the current VPN on port `443` and standard MTProxy fallback on `8444`.
