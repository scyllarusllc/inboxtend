# Inbox Tend plan

Updated 2026-11-18. Internal validation on a Scyllarus subdomain is the near-term strategy. Keep the project open source; reassess an independent domain after real usage and community contribution, not stars alone.

## Implemented local MVP

- [x] Bundled fictional demo messages and attachments removed (2026-11-18); the workspace lists the user's own Gmail inbox and creates tasks from server-fetched Gmail headers and snippets.
- [x] Source metadata separated from rule-based proposal and user-edited task values.
- [x] SQLite task persistence, legacy schema migration, provider/message deduplication, audit events, revision conflict handling and explicit deletion.
- [x] Review, create, list, edit, transition and reopen tasks in the browser.
- [x] Saipan-time upcoming/due-soon/overdue indicators and unsent reply templates.
- [x] Local HTTP and unit tests without Gmail or secrets.
- [x] Public `/` and `/login` pages, private `/user/dashboard`, single-admin SQLite user/session tables and session-bound CSRF.

## Before real-mail or internal web deployment

- [x] Gmail connections and task queries are scoped to the signed-in app user. The service still has one bootstrapped administrator; legacy task uniqueness needs migration before multiuser rollout.
- [x] Web OAuth with session-bound state and PKCE, encrypted refresh token storage, and explicit connect/reconnect/disconnect. The Desktop prototype remains separate.
- [ ] Gmail pagination, incremental history sync, retry/backoff, account-scoped deduplication and deleted/moved-message reconciliation. The existing connector only reads IDs and metadata.
- [ ] Privacy review, encrypted backups, retention policy and operational monitoring before ingesting real correspondence.
- [ ] Optional AI extraction behind configuration, with cited evidence, uncertainty labels and no provider secrets in code.
- [ ] Reminder delivery design; no background reminders are currently scheduled.
- [ ] A separate approval interface and audited authorization before any future send or mail-write operation. No send/delete Gmail API exists today.
- [x] Verify public routes, private API responses, HTTPS session cookies, logout and revocation through Cloudflare after enabling application login. Keep real mail disabled.

Internal validation should measure correct tasks, correction rate, duplicates, missed deadlines, unauthorized actions and repeat weekly use. A domain or brand decision should follow sustained use, not a star threshold.

## Conversation, attachment and agent roadmap (from main)

Attachment and conversation (sent mail) views were part of the removed demo and are not implemented for Gmail. Attachments are untrusted content and are never executed or shared with an agent. The task editor can record an agent assignment as `pending_approval`; it does not execute an agent or disclose email content. Future work requires explicit, scoped approval before execution or external transfer, evidence before marking work complete, and separate human approval before sending.

Real-mail integration needs account-scoped OAuth, pagination, incremental reconciliation, stable provider IDs, safe attachment parsing with size and decompression limits, and local retention controls. PDF, Office, image and real Gmail attachment previews are not implemented.
