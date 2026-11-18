# Inbox Tend plan

Updated 2026-11-18. Internal validation on a Scyllarus subdomain is the near-term strategy. Keep the project open source; reassess an independent domain after real usage and community contribution, not stars alone.

## Implemented local MVP

- [x] Bundled fictional demo messages and attachments removed (2026-11-18); the workspace lists the user's own Gmail inbox and creates tasks from server-fetched Gmail headers and snippets.
- [x] Source metadata separated from rule-based proposal and user-edited task values.
- [x] SQLite task persistence, legacy schema migration, provider/message deduplication, audit events, revision conflict handling and explicit deletion.
- [x] Review, create, list, edit, transition and reopen tasks in the browser.
- [x] Saipan-time upcoming/due-soon/overdue indicators and unsent reply templates.
- [x] Local HTTP and unit tests without Gmail or secrets.
- [x] Public `/`, `/login` and `/privacy` pages, separate private `/user/inbox` and `/user/tasks` pages, single-admin SQLite user/session tables and session-bound CSRF.

## Before real-mail or internal web deployment

- [x] Gmail connections and task queries are scoped to the signed-in app user. The service still has one bootstrapped administrator; legacy task uniqueness needs migration before multiuser rollout.
- [x] Web OAuth with session-bound state and PKCE, encrypted refresh token storage, and explicit connect/reconnect/disconnect. The Desktop prototype remains separate.
- [x] Full mail mirrored into SQLite (`gmail_messages`, `gmail_attachments`): complete headers, text/HTML bodies, labels and size-capped attachments; background sync with progress, resumable, paginated up to a configured limit; reading is local; disconnect deletes the copy. Sent mail is mirrored too (Inbox/Sent tabs, conversation list from stored threads). Open: Gmail history-API incremental sync, at-rest encryption of the mail tables, retention policy.
- [ ] Gmail pagination, history-API incremental sync, retry/backoff, account-scoped deduplication and deleted/moved-message reconciliation. The existing connector only reads IDs and metadata.
- [x] Translation (opt-in, Settings): third Inbox column with the open message translated into the user's language by the chosen AI provider; saved per message and language in SQLite so no message is translated twice.
- [x] Settings page (`/user/settings`): timezone for deadlines and displayed mail times, due-soon window, default priority, sync limits and sent-mail toggle, password change.
- [x] systemd service (boot start, crash restart) and daily verified SQLite backups with retention: see `docs/operations.md`.
- [ ] Privacy review, off-machine encrypted backups, retention policy and operational monitoring before ingesting real correspondence.
- [x] Optional AI assistant via the Codex CLI (default) or Claude Code CLI, provider and model chosen in Settings (off by default): task suggestions with verified evidence and uncertainty notes, reply drafts, read-only sandbox, validated JSON output (`docs/ai-assistant.md`). Open: batch triage of new mail, running it automatically after sync.
- [ ] Reminder delivery design; no background reminders are currently scheduled.
- [ ] A separate approval interface and audited authorization before any future send or mail-write operation. No send/delete Gmail API exists today.
- [x] Verify public routes, private API responses, HTTPS session cookies, logout and revocation through Cloudflare after enabling application login. Keep real mail disabled.

Internal validation should measure correct tasks, correction rate, duplicates, missed deadlines, unauthorized actions and repeat weekly use. A domain or brand decision should follow sustained use, not a star threshold.

## Conversation, attachment and agent roadmap (from main)

Attachment and conversation (sent mail) views were part of the removed demo and are not implemented for Gmail. Attachments are untrusted content and are never executed or shared with an agent. The task editor can record an agent assignment as `pending_approval`; it does not execute an agent or disclose email content. Future work requires explicit, scoped approval before execution or external transfer, evidence before marking work complete, and separate human approval before sending.

Real-mail integration needs account-scoped OAuth, pagination, incremental reconciliation, stable provider IDs, safe attachment parsing with size and decompression limits, and local retention controls. PDF, Office, image and real Gmail attachment previews are not implemented.
