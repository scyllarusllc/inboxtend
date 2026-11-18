# Inbox Tend quickstart

Python 3.10+ and the standard library run the app. Connecting Gmail requires `cryptography` (`python3 -m pip install -r requirements.txt`) and Google Web OAuth credentials (`INBOX_TEND_GOOGLE_*` and `INBOX_TEND_TOKEN_KEY` in `.env`); see [docs/gmail-web.md](docs/gmail-web.md).

```bash
make dev                 # 127.0.0.1:8765
make dev PORT=8766       # choose another local port
```

Open `http://127.0.0.1:8765`. The public landing page is `/`, sign-in is `/login`, `/privacy` is public, and the private pages are `/user/inbox` and `/user/tasks` (`/user/dashboard` redirects to the inbox). The server listens on `127.0.0.1` only. Its SQLite file is `data/tasks.sqlite3` (ignored by Git); keep it private and back it up before removing it. Override the port with `INBOX_TEND_PORT=8766` if needed.

Before starting, copy `.env.example` to a private `.env`, set its permissions to `600`, set the admin email, and generate `INBOX_TEND_ADMIN_PASSWORD_HASH` with `python3 -c 'import getpass; from auth import hash_password; print(hash_password(getpass.getpass("Admin password: ")))'`. The first start creates this administrator in the database if the `users` table is empty. Changing `.env` later does not reset an existing account. On the current VM these values are already present in its root-only `.env`; do not commit or print them. If no administrator is configured, private pages remain inaccessible.

To try Caddy locally, keep `make dev` running and start a separate terminal with `make caddy-dev`. Open `http://localhost:18766` on the same VM or through an SSH tunnel. Both commands accept the same `PORT` value, for example `make dev PORT=8766` and `make caddy-dev PORT=8766`. This example listens on loopback only; it does not modify the VM's existing Caddy service. The app requires sign-in for the dashboard and data APIs; see [docs/deployment-internal.md](docs/deployment-internal.md) before connecting real mail.

1. Sign in, then click **Connect Gmail** and approve the read-only scope. Your 20 most recent inbox messages (headers and snippets) are listed.
2. Open a message and click **Suggest task**. The rule-based proposal uses the subject as the title and the snippet as the description; no deadline is guessed. Edit the fields (add a due date and time in Pacific/Saipan if needed), then create the task.
3. Refresh or restart; the task remains in SQLite. Creating a task for the same Gmail message again returns a duplicate warning and does not add a row.
4. Edit its status through `new`, `in_progress`, `waiting`, `done`, then reopen to `new`. The task list marks upcoming, due soon (within 48 hours), or overdue using Saipan time; completed tasks have no active deadline warning.
5. Prepare an **unsent template**. There is no send API.

The task store keeps IDs, labels, concise task notes, deadlines, and audit events; it does not persist email bodies. For local retention, use the task `DELETE /api/tasks/{id}` endpoint with the session CSRF token and JSON `{"confirm":"DELETE"}`. This permanently removes the task and its audit events, so back up the database first. No automatic deletion is performed.

Run all tests:

```bash
make test
```

The page bodies live in `static/pages/` (home, login, inbox, tasks, privacy); `static/layout.html` supplies their shared header, navigation and footer. After editing either, run `make build-pages` to refresh the full-page compatibility files used by older running processes. The current server composes the layout directly.

The Gmail panel displays real headers and snippets after explicit consent. The VM's internal subdomain uses HTTPS and application login; Gmail connections and task queries are scoped by app user ID. The separate Desktop OAuth command remains experimental. Agent assignment only records pending approval; no agent runs or receives message content. Gmail is read on demand; no background sync, model extraction, reminders or sending exists. Review [docs/deployment-internal.md](docs/deployment-internal.md) before connecting a mailbox that holds client correspondence.
