# Inbox Tend — local demo quickstart

This is a **sample-data prototype**, not a Gmail client or production email service. No tokens, API keys, cloud models, or third-party libraries are required.

## Requirements
- Python 3.10 or newer

## Run
```bash
python3 app.py
```
Open http://127.0.0.1:8765 in a browser. The server binds to **127.0.0.1 only**.

Explore four fictional messages, read deterministic excerpts (not AI summaries), generate **unsent** reply templates, and review suggested tasks before saving them to a local SQLite database. Update task statuses and inspect due-soon/overdue counts. No email is ever sent or synchronized.

## Local task workflow
1. Select a fictional email and choose **Suggest task**.
2. Review and edit the proposal, then choose **Confirm and create local task**.
3. See the saved task in **Local task tracker** and change its status using **Save status**.
4. A repeated creation for the same fictional message is rejected to avoid duplicates.
5. The web server is loopback-only; data is stored in `data/tasks.sqlite3` on the machine running the demo. Do not expose it publicly.

## Tests
```bash
python3 -m unittest discover -s tests -v
```

## Not implemented yet
- Gmail OAuth, IMAP, real mailbox synchronization
- LLM summaries and generated responses
- Production user authentication (local task storage uses SQLite; do not expose this demo to a network)
- Agent Skills, remote agents, orchestration, approvals
- Sending or deleting messages

Before connecting real accounts, the project needs credential storage, OAuth scope review, CSRF protections, retention controls, and an explicit authorization model.
