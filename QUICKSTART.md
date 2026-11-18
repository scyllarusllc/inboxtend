# Inbox Tend local demo quickstart

Python 3.10+ and the standard library are sufficient. No real account, model, or secrets are needed.

```bash
make dev                 # 127.0.0.1:8765
make dev PORT=8766       # choose another local port
```

Open `http://127.0.0.1:8765`. The server listens on `127.0.0.1` only. Its SQLite file is `data/tasks.sqlite3` (ignored by Git); keep it private and back it up before removing it. Override the port with `INBOX_TEND_PORT=8766` if needed.

To try Caddy locally, keep `make dev` running and start a separate terminal with `make caddy-dev`. Open `http://localhost:18766` on the same VM or through an SSH tunnel. Both commands accept the same `PORT` value, for example `make dev PORT=8766` and `make caddy-dev PORT=8766`. This example listens on loopback only; it does not modify the VM's existing Caddy service. Public Caddy routing needs authentication and a reviewed trusted-origin design first; see [docs/deployment-internal.md](docs/deployment-internal.md).

1. Open the fictional **Website project: response and start date** message, dated 2026-11-18. The demo also shows fictional outgoing correspondence and read-only text/CSV attachment previews.
2. Review its rule-based proposal. The suggested deadline is 2026-11-20 at 17:00 Pacific/Saipan. Edit fields, then create the task.
3. Refresh or restart; the task remains in SQLite. Creating it again returns a duplicate warning and does not add a row.
4. Edit its status through `new`, `in_progress`, `waiting`, `done`, then reopen to `new`. The task list marks upcoming, due soon (within 48 hours), or overdue using Saipan time; completed tasks have no active deadline warning.
5. Prepare an **unsent template**. There is no send API.

The task store keeps IDs, labels, concise task notes, deadlines, and audit events; it does not persist email bodies. For local retention, use the task `DELETE /api/tasks/{id}` endpoint with the session CSRF token and JSON `{"confirm":"DELETE"}`. This permanently removes the task and its audit events, so back up the database first. No automatic deletion is performed.

Run all tests:

```bash
make test
```

This is a fictional demo, not a production email site. The VM's internal subdomain has a Caddy Basic Auth gate, but the app itself has no login or account isolation. The separate Gmail OAuth command and read-only connector are experimental; they are not connected to this task flow. Demo agent assignment only records pending approval; no agent runs or receives message content. No Gmail sync, model extraction, reminders, sending, login, or multiuser isolation exists. Do not feed it real client email.
