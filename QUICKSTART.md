# Inbox Tend local demo quickstart

Python 3.10+ and the standard library are sufficient. No real account, model, or secrets are needed.

```bash
python3 app.py
```

Open `http://127.0.0.1:8765`. The server listens on `127.0.0.1` only. Its SQLite file is `data/tasks.sqlite3` (ignored by Git); keep it private and back it up before removing it. Override the port with `INBOX_TEND_PORT=8766` if needed.

1. Open the fictional **Website project: response and start date** message, dated 2026-11-18. The demo also shows fictional outgoing correspondence and read-only text/CSV attachment previews.
2. Review its rule-based proposal. The suggested deadline is 2026-11-20 at 17:00 Pacific/Saipan. Edit fields, then create the task.
3. Refresh or restart; the task remains in SQLite. Creating it again returns a duplicate warning and does not add a row.
4. Edit its status through `new`, `in_progress`, `waiting`, `done`, then reopen to `new`. The task list marks upcoming, due soon (within 48 hours), or overdue using Saipan time; completed tasks have no active deadline warning.
5. Prepare an **unsent template**. There is no send API.

The task store keeps IDs, labels, concise task notes, deadlines, and audit events; it does not persist email bodies. For local retention, use the task `DELETE /api/tasks/{id}` endpoint with the session CSRF token and JSON `{"confirm":"DELETE"}`. This permanently removes the task and its audit events, so back up the database first. No automatic deletion is performed.

Run all tests:

```bash
python3 -m unittest discover -s tests -v
```

This is a local demo, not a production authenticated site. The separate Gmail OAuth command and read-only connector are experimental; they are not connected to this task flow. Demo agent assignment only records pending approval; no agent runs or receives message content. No Gmail sync, model extraction, reminders, sending, login, or multiuser isolation exists. Do not expose this server publicly or feed it real client email.
