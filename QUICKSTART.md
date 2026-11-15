# Inbox Tend — local demo quickstart

This is a **sample-data prototype**, not a Gmail client or production email service. No tokens, API keys, cloud models, or third-party libraries are required.

## Requirements
- Python 3.10 or newer

## Run
```bash
python3 app.py
```
Open http://127.0.0.1:8765 in a browser. The server binds to **127.0.0.1 only**.

Explore three fictional messages, read deterministic excerpts (not AI summaries), and generate **unsent** reply templates. No email is ever sent or synchronized.

## Tests
```bash
python3 -m unittest discover -s tests -v
```

## Not implemented yet
- Gmail OAuth, IMAP, real mailbox synchronization
- LLM summaries and generated responses
- Persistent storage or production user authentication
- Agent Skills, remote agents, orchestration, approvals
- Sending or deleting messages

Before connecting real accounts, the project needs credential storage, OAuth scope review, CSRF protections, retention controls, and an explicit authorization model.
