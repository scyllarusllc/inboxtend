# Inbox Tend

**Local-first email-to-task prototype · Apache-2.0**

Inbox Tend provides a **fictional inbox demo** and an optional Gmail Web OAuth connection. Review sample incoming and outgoing correspondence, preview fictional text/CSV attachments, inspect a rule-based task suggestion, edit and save a task, track its owner, priority, deadline and status, and prepare an unsent reply template. SQLite persists tasks and audit events locally. Demo agent assignments only record `pending_approval`; no agent runs or receives content. The fictional demo does not contact Gmail. The separately initiated Gmail panel reads only headers and snippets; no email is sent. See [QUICKSTART.md](QUICKSTART.md) to run it.

The sample website-project follow-up is fictional and dated 2026-11-18, with a proposed response deadline of 2026-11-20 at 17:00 Pacific/Saipan. It deliberately leaves the disputed service start date unverified. Suggestions are deterministic demo rules, not AI conclusions. Message bodies stay in the sample source file, not the task database.

## Current architecture

- `app.py`: loopback-only standard-library HTTP server, fictional-message API and authenticated Gmail routes. Writes require a per-session CSRF token, loopback Host, same-origin request, JSON content type and a 16 KiB body limit.
- `tasks.py`: SQLite tasks, migration from the initial branch schema, source-ID deduplication, optimistic revision conflicts, audit events and Saipan deadline states.
- `static/`: responsive browser workspace with overview cards, inbox review, task controls and privacy guidance. The visual hierarchy draws on the local `statistics.diy` project while keeping Inbox Tend's own HTML/CSS and no frontend build dependency. Dynamic content uses text nodes.
- `gmail_web.py` and `connectors/gmail_readonly.py`: session-bound Web OAuth and read-only Gmail inbox panel, documented in [docs/gmail-web.md](docs/gmail-web.md). `gmail_oauth_local.py` remains a separate one-shot Desktop prototype.
- `tests/`: local HTTP, persistence, migration, validation and connector tests.

## Security boundary

The app has public `/` and `/login` pages, with `/user/dashboard` and all mailbox/task APIs behind a single-admin session. Initial admin credentials are configured by `.env` and bootstrapped into the SQLite `users` table once; only a password hash is stored in the database. Sessions use HttpOnly cookies, a per-session CSRF token and a 12-hour expiry. Bind remains `127.0.0.1`; Caddy provides HTTPS while application login protects private routes. The demo task workflow remains fictional; the optional Gmail panel can show real mail headers and snippets for the signed-in user. This is a single-admin service. There is no send, delete-mail, agent execution or model API endpoint. `DELETE /api/tasks/{id}` is an explicit retention control and permanently removes that task and its audit entries.

For the internal subdomain's current setup and remaining gates, see [docs/deployment-internal.md](docs/deployment-internal.md). [docs/real-app-roadmap.md](docs/real-app-roadmap.md) sets out the next implementation stages. [PLAN.md](PLAN.md) tracks future work.

## License

[Apache License, Version 2.0](LICENSE). Copyright 2026 Scyllarus LLC.
