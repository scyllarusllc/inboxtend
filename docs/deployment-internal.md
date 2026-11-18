# Internal subdomain status and deployment plan

## 2026-11-18 Gmail Web OAuth deployment

The reviewed application is staged at `/srv/inboxtend/current` and runs with `make dev PORT=8765` as the dedicated `inboxtend` user. This remains a manually managed process; no application systemd service was installed. Code is owned by root and readable by the service group. The private `.env` and SQLite database are owned by `inboxtend` with mode `600`; the data directory has mode `700`. A predeployment SQLite Backup API copy and private rollback instructions are under `/srv/inboxtend/backups` and `/srv/inboxtend/rollback/RESTART.txt`.

Caddy continues to proxy only this host to `127.0.0.1:8765`. Its Inbox Tend rule preserves the browser `Origin` while rewriting the upstream `Host`; the app accepts the configured HTTPS origin for writes. No Caddy access log directive is enabled. The proxy configuration was validated and reloaded after changing only this site's Origin rule.

The full staged suite passed under the service account. Public HTTPS checks showed unauthenticated Gmail APIs return `401`; a temporary signed-in session saw Gmail configured but disconnected; cross-origin writes returned `403`, same-origin writes with CSRF returned `200`, and missing-CSRF writes returned `403`. The temporary session was revoked. No Google consent or real-mail read occurred. The operator must still personally approve the exact Gmail read-only scope on Google's consent screen before the mailbox connects.

Target: `https://inboxtend.scyllar.us`. On 2026-11-18, Cloudflare was set to Full (strict) by the operator and this VM's Caddy was configured on 80/443 with a proxy to `127.0.0.1:8765`. HTTP redirects to HTTPS. The app serves public `/` and `/login` pages and requires its own session for `/user/dashboard` and data APIs. The initial admin username and password hash are in the Git-ignored, root-only `.env` file; the `users` table stores a password hash. The app is started manually with `make dev` by the operator, so availability depends on that process. It is still a fictional single-user demo, not a real-mail service.

## 2026-11-18 routing check

The first route attempt produced a Cloudflare HTTPS redirect loop while origin TLS was not Full (strict), and was rolled back. After the operator changed the Cloudflare setting, the route was re-enabled. Caddy obtained a valid origin certificate. A short-lived systemd service was tried for reliability, then disabled and removed at the operator's request to manage `make dev` manually. After application login was validated, Caddy Basic Auth was removed: external `/` and `/login` returned `200` without credentials; private APIs returned `401` without an app session; login, dashboard, logout and session revocation passed over HTTPS. No real email was connected or sent.

## Release gate

The current app has **single-admin login but no account isolation** and deliberately rejects non-loopback Host headers. The public landing and login pages contain no inbox or task data. Before connecting real mail, add account-scoped message/task queries and credential storage, then verify that one account cannot read another's records. The app's CSRF token is not a substitute for authentication.

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

Connecting a real mailbox is a per-user, explicit action; the app never publishes private correspondence or sends email.
