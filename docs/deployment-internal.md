# Internal subdomain status and deployment plan

Target: `https://inboxtend.scyllar.us`. On 2026-11-18, Cloudflare was set to Full (strict) by the operator and this VM's Caddy was configured on 80/443 with an HTTPS Basic Auth gate and a proxy to `127.0.0.1:8765`. Caddy removes the Authorization header before forwarding. HTTP redirects to HTTPS. The password is in the Git-ignored, root-only `.env` file; only a bcrypt hash is in `/etc/caddy/Caddyfile`. The app is started manually with `make dev` by the operator, so availability depends on that process. It is still a fictional single-user demo, not a real-mail service.

## 2026-11-18 routing check

The first route attempt produced a Cloudflare HTTPS redirect loop while origin TLS was not Full (strict), and was rolled back. After the operator changed the Cloudflare setting, the route was re-enabled. External unauthenticated `/api/messages` returned `401`; authenticated `/`, `/api/health` and `/api/tasks` returned `200`; an authenticated draft request returned `sent: false`. Caddy obtained a valid origin certificate. A short-lived systemd service was tried for reliability, then disabled and removed at the operator's request to manage `make dev` manually. No real email was connected or sent.

## Release gate

The current app has **no built-in login or account isolation** and deliberately rejects non-loopback Host headers. Caddy Basic Auth provides a single shared gate for this fictional demo only; it is not adequate for real mail or multiple users. Before connecting real mail, implement authenticated sessions and account isolation, verify trusted-origin handling, and confirm the origin cannot be bypassed. Do not use the CSRF token as an auth credential.

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

The running demo route is not approval to connect a real mailbox, publish private correspondence, or send email.
