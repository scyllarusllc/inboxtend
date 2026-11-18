# Inbox Tend quickstart

Python 3.10+ and the standard library run the app. Connecting Gmail requires `cryptography` (`python3 -m pip install -r requirements.txt`) and Google Web OAuth credentials (`INBOX_TEND_GOOGLE_*` and `INBOX_TEND_TOKEN_KEY` in `.env`); see [docs/gmail-web.md](docs/gmail-web.md).

```bash
make dev                 # 127.0.0.1:8765
make dev PORT=8766       # choose another local port
```

For a long-running install use systemd instead of `make dev` (auto-start, crash restart, daily database backups): see [docs/operations.md](docs/operations.md). Open `http://127.0.0.1:8765`. The public landing page is `/`, sign-in is `/login`, `/privacy` is public, and the private pages are `/user/inbox`, `/user/tasks` and `/user/settings` (timezone, task defaults, mail sync limits, password) (`/user/dashboard` redirects to the inbox). The server listens on `127.0.0.1` only. Its SQLite file is `data/tasks.sqlite3` (ignored by Git); keep it private and back it up before removing it. Override the port with `INBOX_TEND_PORT=8766` if needed.

Before starting, copy `.env.example` to a private `.env`, set its permissions to `600`, set the admin email, and generate `INBOX_TEND_ADMIN_PASSWORD_HASH` with `python3 -c 'import getpass; from auth import hash_password; print(hash_password(getpass.getpass("Admin password: ")))'`. The first start creates this administrator in the database if the `users` table is empty. Changing `.env` later does not reset an existing account. On the current VM these values are already present in its root-only `.env`; do not commit or print them. If no administrator is configured, private pages remain inaccessible.

To try Caddy locally, keep `make dev` running and start a separate terminal with `make caddy-dev`. Open `http://localhost:18766` on the same VM or through an SSH tunnel. Both commands accept the same `PORT` value, for example `make dev PORT=8766` and `make caddy-dev PORT=8766`. This example listens on loopback only; it does not modify the VM's existing Caddy service. The app requires sign-in for the dashboard and data APIs; see [docs/deployment-internal.md](docs/deployment-internal.md) before connecting real mail.

1. Sign in, open **Settings → Gmail connection**, click **Connect Gmail (read-only)** and approve the scope (you return to the Inbox). The first visit starts a background sync that saves your recent inbox and sent messages (full content and attachments) to SQLite; progress is shown on the page. Later visits read SQLite only, and **Refresh inbox** downloads just the new messages. The line under the status shows how much of your Gmail is synced (for example *Inbox 200 of 4,631*); raise **Messages per folder** in Settings to sync more. **Search** box above the list (press `/` to jump to it, `Esc` to clear): words must all match the subject, people, snippet or text, and Gmail-style operators narrow it down (`from:casey is:unread`, `subject:"website project"`, `has:attachment`); *Unread* and *With attachments* buttons filter quickly. Long lists show 100 rows at a time with a *Show more* button. Each message shows how long ago it arrived (`3 h ago`; the exact time is in the tooltip). With **Country flags** switched on in Settings, a flag marks where each correspondent probably is, guessed once per sender by the AI (hover it to see why). Unread messages are bold with a dot, and the Inbox tab shows how many there are. Opening a message marks it read (switchable in Settings), and there is a Mark as read/unread button; this is kept in Inbox Tend only and does not change Gmail, unless you opt in under Settings → Gmail access (needs Google's `gmail.modify` permission; see docs/gmail-web.md). Switch the list between **Conversations** (default), **Messages** and **People** above it; the choice is remembered (also in Settings).
2. Open a message and click **Suggest task**. The rule-based proposal uses the subject as the title and the snippet as the description; no deadline is guessed. Edit the fields (add a due date and time in Pacific/Saipan if needed), then create the task.
3. Refresh or restart; the task remains in SQLite. Creating a task for the same Gmail message again returns a duplicate warning and does not add a row.
4. Edit its status through `new`, `in_progress`, `waiting`, `done`, then reopen to `new`. The task list marks upcoming, due soon (within 48 hours), or overdue using Saipan time; completed tasks have no active deadline warning.
5. Prepare an **unsent template**. There is no send API.

The task store keeps IDs, labels, concise task notes, deadlines, and audit events. Synced mail lives in the `gmail_messages` and `gmail_attachments` tables of the same SQLite file, which therefore contains your correspondence: keep it private and encrypt backups. For local retention, use the task `DELETE /api/tasks/{id}` endpoint with the session CSRF token and JSON `{"confirm":"DELETE"}`. This permanently removes the task and its audit events, so back up the database first. No automatic deletion is performed.

Run all tests:

```bash
make test
```

The page bodies live in `static/pages/` (home, login, inbox, tasks, privacy); `static/layout.html` supplies their shared header, navigation and footer. After editing either, run `make build-pages` to refresh the full-page compatibility files used by older running processes. The current server composes the layout directly.

Gmail is contacted only when you connect and when you press **Refresh inbox** (new messages only); there is no automatic background sync, reminders or sending. The optional AI assistant (off by default, see [docs/ai-assistant.md](docs/ai-assistant.md)) contacts the chosen provider (OpenAI or Anthropic) only when you press an AI button on a message, or, if you switched on **Translation**, when you open a message that has not been translated yet (results are saved in SQLite and never translated twice). Review [docs/deployment-internal.md](docs/deployment-internal.md) before connecting a mailbox that holds client correspondence.
