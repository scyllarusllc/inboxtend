# Inbox Tend plan

Updated 2026-11-18. Internal validation on a Scyllarus subdomain is the near-term strategy. Keep the project open source; reassess an independent domain after real usage and community contribution, not stars alone.

## Implemented local MVP

- [x] Four fictional messages, including a website-project follow-up with a contested start date and a 2026-11-20 reply deadline.
- [x] Source metadata separated from rule-based proposal and user-edited task values.
- [x] SQLite task persistence, legacy schema migration, provider/message deduplication, audit events, revision conflict handling and explicit deletion.
- [x] Review, create, list, edit, transition and reopen tasks in the browser.
- [x] Saipan-time upcoming/due-soon/overdue indicators and unsent reply templates.
- [x] Local HTTP and unit tests without Gmail or secrets.

## Before real-mail or internal web deployment

- [ ] Authenticated user sessions and account isolation; explicit trusted-origin configuration for a reverse proxy. The local CSRF token is not authentication.
- [ ] Secure OAuth token storage outside Git, reconnect/disconnect flow and explicit per-account consent. Current one-shot local OAuth prototype retains access tokens only in memory and is not in the dashboard.
- [ ] Gmail pagination, incremental history sync, retry/backoff, account-scoped deduplication and deleted/moved-message reconciliation. The existing connector only reads IDs and metadata.
- [ ] Privacy review, encrypted backups, retention policy and operational monitoring before ingesting real correspondence.
- [ ] Optional AI extraction behind configuration, with cited evidence, uncertainty labels and no provider secrets in code.
- [ ] Reminder delivery design; no background reminders are currently scheduled.
- [ ] A separate approval interface and audited authorization before any future send or mail-write operation. No send/delete Gmail API exists today.
- [ ] Verify DNS target, origin ownership, TLS route, authentication boundary and firewall before considering `inboxtend.scyllar.us` live.

Internal validation should measure correct tasks, correction rate, duplicates, missed deadlines, unauthorized actions and repeat weekly use. A domain or brand decision should follow sustained use, not a star threshold.
