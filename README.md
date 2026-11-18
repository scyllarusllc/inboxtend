# Inbox Tend

**Local-first email-to-task prototype · Apache-2.0**

Inbox Tend currently provides a working **fictional inbox demo**. Review sample incoming and outgoing correspondence, preview fictional text/CSV attachments, inspect a rule-based task suggestion, edit and save a task, track its owner, priority, deadline and status, and prepare an unsent reply template. SQLite persists tasks and audit events locally. Demo agent assignments only record `pending_approval`; no agent runs or receives content. The demo never contacts Gmail or sends email. See [QUICKSTART.md](QUICKSTART.md) to run it.

The sample website-project follow-up is fictional and dated 2026-11-18, with a proposed response deadline of 2026-11-20 at 17:00 Pacific/Saipan. It deliberately leaves the disputed service start date unverified. Suggestions are deterministic demo rules, not AI conclusions. Message bodies stay in the sample source file, not the task database.

## Current architecture

- `app.py`: loopback-only standard-library HTTP server and fictional-message API. Writes require a per-process CSRF token, loopback Host, same-origin request, JSON content type and a 16 KiB body limit.
- `tasks.py`: SQLite tasks, migration from the initial branch schema, source-ID deduplication, optimistic revision conflicts, audit events and Saipan deadline states.
- `static/`: browser review and task UI; dynamic content uses text nodes.
- `connectors/gmail_readonly.py` and `gmail_oauth_local.py`: separate experimental Gmail metadata reader and one-shot OAuth flow, documented in [docs/gmail-oauth-local.md](docs/gmail-oauth-local.md). Neither is wired to the dashboard.
- `tests/`: local HTTP, persistence, migration, validation and connector tests.

## Security boundary

This is a single-user local demo, **without authentication**. Bind remains `127.0.0.1`; do not publish it through a reverse proxy or accept real mail until authentication, account isolation, trusted-origin handling, secure credential storage and a deployment review are complete. The CSRF token protects local browser writes but is not login. There is no send, delete-mail, agent execution or model API endpoint. `DELETE /api/tasks/{id}` is a local explicit retention control and permanently removes that task and its audit entries.

For an internal subdomain plan and open questions, see [docs/deployment-internal.md](docs/deployment-internal.md). [PLAN.md](PLAN.md) tracks future work. The domain `inboxtend.scyllar.us` is a proposed internal validation address, not an assertion that Inbox Tend is deployed there.

## License

[Apache License, Version 2.0](LICENSE). Copyright 2026 Scyllarus LLC.
