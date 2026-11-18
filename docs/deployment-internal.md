# Internal subdomain deployment plan (not executed)

Target: `https://inboxtend.scyllar.us`. This document is a checklist, not a deployment instruction to run now. On 2026-11-18, read-only DNS lookup returned Cloudflare addresses and an HTTPS HEAD request returned `200` from Cloudflare. This does **not** establish the DNS origin target, VM ownership, route content, authentication or end-to-end TLS. This VM has Caddy listening on 80/443 and unrelated localhost services; no Inbox Tend service was found. Do not alter their configuration while preparing this project.

## Release gate

The current app has **no login or account isolation** and deliberately rejects non-loopback Host headers. It is not safe to expose through the public reverse proxy, even on an "internal" name. Before deployment, implement and test an authenticated session or a tightly controlled identity-aware proxy, plus trusted proxy/origin handling and per-user data separation. Confirm the authenticated boundary cannot be bypassed by reaching the origin directly. Do not use the CSRF token as an auth credential.

## Required operator inputs

- Confirm the DNS record and Cloudflare configuration, actual origin VM and ownership, desired identity provider, authorized users, TLS termination points and proxy trust boundary.
- Choose an unused loopback port, a dedicated unprivileged service account, persistent data directory, and secret storage outside Git. Do not guess IP addresses or reuse existing ports.
- Define database backup destination, encryption, restore test, log retention and who receives alerts. Treat task labels and audit entries as potentially sensitive.

## When the gates are met

1. Stage a reviewed release in a separate directory under a dedicated service account. Keep `data/tasks.sqlite3` on a persistent private volume with restrictive ownership; back it up before upgrade. Schema migration runs on first database open and adds columns/indexes/events without deleting existing rows. Test migration against a copied database first.
2. Run the authenticated app on `127.0.0.1:<chosen-port>` under the VM's service manager with restart-on-failure and a writable data path. Do not run as root. Configure secrets through a protected service environment or secret manager, never repository files or command-line arguments.
3. Route only the authenticated virtual host through Caddy (or the verified proxy), with HTTPS and a valid certificate. Ensure the firewall exposes only intended public ports and the app port is loopback-only. Preserve existing Caddy sites and test the candidate configuration before reload.
4. Verify an unauthenticated request cannot see the inbox or task API; verify authorized access, CSRF, task isolation, persistence after restart, TLS certificate, logs, backup and restore. Only then enable real-mail consent and separately review OAuth scopes and retention.
5. Roll back by routing to the previous release and restoring the pre-upgrade database copy only if data written since upgrade can safely be discarded. Otherwise stop writes and perform a planned data-preserving rollback. Do not blindly restore an old database over newer tasks.

No production changes, DNS edits, services, tokens or real mail were used to create this plan.
