# Running Inbox Tend as a service

Unit files live in `deploy/systemd/`. They assume the checkout is at `/root/github/inboxtend` and the app runs as root, which is how the reference server is set up. If yours differs, edit `WorkingDirectory=` and add `User=`/`Group=` before installing; the user must own `data/` and `.env`.

## Install

```bash
sudo install -m 644 deploy/systemd/inboxtend.service deploy/systemd/inboxtend-backup.service deploy/systemd/inboxtend-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inboxtend.service inboxtend-backup.timer
```

`inboxtend.service` starts at boot and is restarted 3 seconds after any crash. The app listens on `127.0.0.1:8765` (`INBOX_TEND_PORT` in the unit); put HTTPS in front of it with Caddy or similar. After updating the code run `sudo systemctl restart inboxtend`.

To serve the Next.js frontend, run `npm ci && npm run next:build`, then install and enable its service:

```bash
sudo install -m 644 deploy/systemd/inboxtend-next.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inboxtend-next.service
```

It listens on port 3000 and proxies API and static requests to FastAPI on port 8765. Point your reverse proxy at port 3000. Keep the FastAPI service running; Next.js does not open the SQLite file.

```bash
systemctl status inboxtend          # state
journalctl -u inboxtend -f          # logs and tracebacks (stdout/stderr go to the journal)
```

## Backups

`inboxtend-backup.timer` runs `scripts/backup.py` daily at about 03:30 (plus a few minutes of random delay; a run missed while the machine was off happens at the next boot). It uses SQLite's online backup API, so it is safe while the app is running, checks the copy with `PRAGMA integrity_check`, compresses it and keeps the newest 14 files in `/var/backups/inboxtend` (directory mode 700, files 600).

```bash
sudo systemctl start inboxtend-backup.service     # back up now
systemctl list-timers inboxtend-backup.timer      # next run
python3 scripts/backup.py --dest /mnt/safe --keep 30   # custom location and retention
```

Settings can also come from the environment: `INBOX_TEND_BACKUP_DIR` and `INBOX_TEND_BACKUP_KEEP`.

Things to know:

- A backup holds your mail and attachments in plain form. Treat the backup directory like the database itself, and copy it **off this machine** (another disk or host, encrypted); backups on the same disk do not survive a disk failure.
- Backups do **not** include `.env`. The stored Gmail refresh token is encrypted with `INBOX_TEND_TOKEN_KEY`, so keep a private copy of `.env` too. If you lose the key you can still read everything else, and simply reconnect Gmail.

## Restore

```bash
sudo systemctl stop inboxtend
cd /root/github/inboxtend/data
sudo rm -f tasks.sqlite3 tasks.sqlite3-wal tasks.sqlite3-shm
zcat /var/backups/inboxtend/tasks-YYYYMMDD-HHMMSS-NNNNNN.sqlite3.gz | sudo tee tasks.sqlite3 >/dev/null
sudo chmod 600 tasks.sqlite3
sudo systemctl start inboxtend
```

Check the copy before relying on it: `zcat <file> > /tmp/check.sqlite3 && python3 -c "import sqlite3; print(sqlite3.connect('/tmp/check.sqlite3').execute('pragma integrity_check').fetchone())"`.
