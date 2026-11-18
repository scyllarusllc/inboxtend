# From preview to real app

The `statistics.diy` project provided a useful UI reference: clear navigation, an overview before detail, consistent cards and form controls, responsive layouts, visible privacy information and keyboard focus states. Inbox Tend now applies those patterns with its existing Python server and local CSS. This is a visual and usability step, not a claim that the mailbox is connected.

## Current boundary

The workspace shows the signed-in user's Gmail inbox (headers and snippets) with no bundled sample data. Tasks are local SQLite records. The app has one admin user, server-side sessions, login/logout and protected APIs. Caddy supplies HTTPS; public pages are open while the dashboard and data APIs require the app session. Task records are not yet partitioned by account. A real Gmail account, private message body or credential must not be introduced yet. The existing one-shot Gmail OAuth prototype is separate from the web app and does not store a connected account.

## Next implementation gates

1. **Application accounts and sessions.** The first single-admin slice is implemented using the security concepts in `statistics.diy/src/auth.mjs`: server-side session records, hashed password verification, secure HttpOnly cookies behind HTTPS, session-bound CSRF, logout and login throttling. Next add account IDs to every message, task and audit query before allowing multiple accounts.
2. **Private source store.** Add a provider/account-scoped message index with minimal metadata and explicit retention. Keep raw bodies and attachments separate and encrypted or otherwise protected at rest according to a documented threat model. Define migrations and backup/restore tests before touching real data.
3. **Consent-based Gmail read sync.** Build an in-app OAuth flow with state and PKCE, least-privilege read scope, secure token storage outside Git, account isolation, paging, incremental history sync, retries and reconciliation. No send/delete scopes.
4. **Review-first task flow.** Show source evidence, uncertainty and user edits separately. Preserve source IDs for deduplication; let the user correct a proposal before creating a task. Add reminders only after timezone, delivery and opt-out behavior are specified.
5. **Outbound approval.** Keep response suggestions unsent. A future send feature requires a distinct approval screen that shows recipients, exact body and attachments, followed by an auditable send action. Agent assignment must remain inert until an equally explicit permission flow exists.

## Release criteria for real mail

Tests must prove unauthenticated users cannot read any message/task API, one account cannot access another's records, cross-origin writes fail, secrets never appear in Git or logs, sync is idempotent, and no draft or task action sends mail. Perform a restore test with a copied database. Only then connect a test mailbox with fictional correspondence; do not use real client mail during initial validation.
