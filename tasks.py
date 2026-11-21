"""SQLite store for reviewed local tasks and cached Gmail messages and attachments. Gmail credentials are stored encrypted, never in plaintext."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import sqlite3
import settings
from email.utils import getaddresses, parseaddr

STATES = ('new', 'in_progress', 'waiting', 'done')
PRIORITIES = ('low', 'medium', 'high')
# INBOX_TEND_DB points the app at another database file (used by `make demo`); normally unset.
DEFAULT_DB = Path(os.environ.get('INBOX_TEND_DB') or Path(__file__).resolve().parent / 'data' / 'tasks.sqlite3')


def peer_for(label_ids, from_addr, to_addr):
    """The other party of a message: the sender of mail you received, the first recipient of mail you sent.
    Returns (normalized address, display name); the name is empty when the header has none."""
    if '"SENT"' in (label_ids or ''):
        pairs = getaddresses([to_addr or ''])
        name, addr = pairs[0] if pairs else ('', '')
    else:
        name, addr = parseaddr(from_addr or '')
    return (addr or '').strip().lower()[:254], (name or '').strip()[:120]


def connection(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    db = sqlite3.connect(path, timeout=5)
    path.chmod(0o600)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=5000')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL, description TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
        due_date TEXT, priority TEXT NOT NULL CHECK(priority IN ('low','medium','high')),
        status TEXT NOT NULL CHECK(status IN ('new','in_progress','waiting','done')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
    additions = {
        'source_provider': "TEXT NOT NULL DEFAULT 'demo'",
        'thread_id': "TEXT NOT NULL DEFAULT ''",
        'project_label': "TEXT NOT NULL DEFAULT ''",
        'customer_label': "TEXT NOT NULL DEFAULT ''",
        'due_time': 'TEXT',
        'due_timezone': "TEXT NOT NULL DEFAULT 'Pacific/Saipan'",
        'source_url': 'TEXT',
        'review_state': "TEXT NOT NULL DEFAULT 'reviewed'",
        'revision': 'INTEGER NOT NULL DEFAULT 1',
        'assigned_agent': "TEXT NOT NULL DEFAULT ''",
        'agent_status': "TEXT NOT NULL DEFAULT 'unassigned'",
        'agentsteers_job_id': "TEXT NOT NULL DEFAULT ''",
        'agentsteers_result': "TEXT NOT NULL DEFAULT ''",
        'user_id': 'INTEGER NOT NULL DEFAULT 1',
    }
    with db:
        for name, definition in additions.items():
            if name not in columns:
                db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {definition}')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS tasks_source ON tasks(source_provider,message_id)')
        db.execute('''CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            action TEXT NOT NULL, changes TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS task_attachments (
            id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            content BLOB, skipped_reason TEXT NOT NULL DEFAULT '')''')
        db.execute('''CREATE TABLE IF NOT EXISTS task_image_reviews (
            task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS user_sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS user_sessions_expiry ON user_sessions(expires_at)')
        db.execute('''CREATE TABLE IF NOT EXISTS gmail_oauth_pending (state_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, session_hash TEXT NOT NULL, verifier TEXT NOT NULL, expires_at INTEGER NOT NULL)''')
        # One row per connected Gmail account (a user may connect several). Databases from before multiple accounts
        # have one row per user with user_id as the key: it becomes that user's first account.
        legacy = {r['name'] for r in db.execute('PRAGMA table_info(gmail_connections)')}
        if legacy and 'id' not in legacy:
            db.execute('ALTER TABLE gmail_connections RENAME TO gmail_connections_v1')
        db.execute('''CREATE TABLE IF NOT EXISTS gmail_connections (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, email TEXT NOT NULL DEFAULT '',
            refresh_token BLOB NOT NULL, connected_at INTEGER NOT NULL, last_sync INTEGER,
            remote_counts TEXT NOT NULL DEFAULT '',   -- JSON: totals in Gmail, cached
            scopes TEXT NOT NULL DEFAULT '',          -- '' = read-only (older connections)
            prefix TEXT NOT NULL DEFAULT '',          -- put before this account's Gmail ids so ids never clash between accounts ('' for the first)
            UNIQUE (user_id, email))''')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='gmail_connections_v1'").fetchone():
            old = {r['name'] for r in db.execute('PRAGMA table_info(gmail_connections_v1)')}
            pick = lambda column, fallback: column if column in old else fallback
            db.execute(f"INSERT INTO gmail_connections(user_id,refresh_token,connected_at,last_sync,remote_counts,scopes) "
                       f"SELECT user_id,refresh_token,connected_at,{pick('last_sync','NULL')},{pick('remote_counts',chr(39)*2)},{pick('scopes',chr(39)*2)} FROM gmail_connections_v1")
            db.execute('DROP TABLE gmail_connections_v1')
        if 'account_id' not in {r['name'] for r in db.execute('PRAGMA table_info(gmail_oauth_pending)')}:
            db.execute('ALTER TABLE gmail_oauth_pending ADD COLUMN account_id INTEGER')  # Set when an existing account is being re-authorised
        if 'level' not in {r['name'] for r in db.execute('PRAGMA table_info(gmail_oauth_pending)')}:
            db.execute("ALTER TABLE gmail_oauth_pending ADD COLUMN level TEXT NOT NULL DEFAULT 'readonly'")
        if 'next' not in {r['name'] for r in db.execute('PRAGMA table_info(gmail_oauth_pending)')}:
            db.execute("ALTER TABLE gmail_oauth_pending ADD COLUMN next TEXT NOT NULL DEFAULT 'inbox'")  # Page to return to after Google
        db.execute('''CREATE TABLE IF NOT EXISTS imap_connections (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, email TEXT NOT NULL, host TEXT NOT NULL, username TEXT NOT NULL, password BLOB NOT NULL, connected_at INTEGER NOT NULL, last_sync INTEGER, UNIQUE(user_id,email))''')
        # Local copy of Gmail messages (headers, bodies, attachments), per app user.
        db.execute('''CREATE TABLE IF NOT EXISTS gmail_messages (
            user_id INTEGER NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '',
            from_addr TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', date_header TEXT NOT NULL DEFAULT '',
            snippet TEXT NOT NULL DEFAULT '', internal_date INTEGER NOT NULL DEFAULT 0,
            in_inbox INTEGER NOT NULL DEFAULT 1, fetched_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, message_id))''')
        message_columns = {r['name'] for r in db.execute('PRAGMA table_info(gmail_messages)')}
        for name, definition in {
            'to_addr': "TEXT NOT NULL DEFAULT ''", 'cc_addr': "TEXT NOT NULL DEFAULT ''",
            'label_ids': "TEXT NOT NULL DEFAULT '[]'", 'headers_json': "TEXT NOT NULL DEFAULT '[]'",
            'body_text': "TEXT NOT NULL DEFAULT ''", 'body_html': "TEXT NOT NULL DEFAULT ''",
            'size_estimate': 'INTEGER NOT NULL DEFAULT 0', 'has_full': 'INTEGER NOT NULL DEFAULT 0',
            'in_sent': 'INTEGER NOT NULL DEFAULT 0',
            'account_id': 'INTEGER',   # The gmail_connections row the message came from (NULL: stored before accounts existed)
            'peer_email': "TEXT NOT NULL DEFAULT ''", 'peer_name': "TEXT NOT NULL DEFAULT ''",
            'read_override': 'INTEGER',  # NULL = follow Gmail's UNREAD label; 1 = read here; 0 = unread here.
        }.items():
            if name not in message_columns:
                db.execute(f'ALTER TABLE gmail_messages ADD COLUMN {name} {definition}')
        if 'peer_email' not in message_columns:  # Columns were just added: fill them in for mail stored earlier.
            for r in db.execute("SELECT user_id,message_id,label_ids,from_addr,to_addr FROM gmail_messages").fetchall():
                email_address, display = peer_for(r['label_ids'], r['from_addr'], r['to_addr'])
                db.execute('UPDATE gmail_messages SET peer_email=?,peer_name=? WHERE user_id=? AND message_id=?',
                           (email_address, display, r['user_id'], r['message_id']))
        if 'account_id' not in message_columns:
            db.execute('UPDATE gmail_messages SET account_id=(SELECT c.id FROM gmail_connections c WHERE c.user_id=gmail_messages.user_id ORDER BY c.id LIMIT 1) '
                       'WHERE account_id IS NULL AND EXISTS (SELECT 1 FROM gmail_connections c WHERE c.user_id=gmail_messages.user_id)')  # One-time migration for mail from before accounts existed.
        db.execute('CREATE INDEX IF NOT EXISTS gmail_messages_account ON gmail_messages(user_id,account_id)')
        db.execute('CREATE INDEX IF NOT EXISTS gmail_messages_thread ON gmail_messages(user_id,thread_id)')
        db.execute('CREATE INDEX IF NOT EXISTS gmail_messages_peer ON gmail_messages(user_id,peer_email)')
        db.execute('''CREATE TABLE IF NOT EXISTS gmail_attachments (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, message_id TEXT NOT NULL,
            part_id TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL, mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0, content BLOB, skipped_reason TEXT NOT NULL DEFAULT '')''')
        db.execute('''CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key))''')
        db.execute('''CREATE TABLE IF NOT EXISTS message_translations (
            user_id INTEGER NOT NULL, message_id TEXT NOT NULL, language_key TEXT NOT NULL, language TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', source_language TEXT NOT NULL DEFAULT '',
            same_language INTEGER NOT NULL DEFAULT 0, provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL, PRIMARY KEY (user_id, message_id, language_key))''')
        db.execute('''CREATE TABLE IF NOT EXISTS sender_countries (
            user_id INTEGER NOT NULL, peer_email TEXT NOT NULL, country TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'low', reason TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
            guessed_at INTEGER NOT NULL, PRIMARY KEY (user_id, peer_email))''')  # country '' = the AI could not tell
        existing = {r['name'] for r in db.execute('PRAGMA table_info(sender_countries)')}
        for column in ('city', 'company', 'role', 'language', 'relationship', 'formality', 'brevity'):  # The person card (AI guesses)
            if column not in existing:
                db.execute(f"ALTER TABLE sender_countries ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if 'basis' not in {r['name'] for r in db.execute('PRAGMA table_info(sender_countries)')}:
            db.execute("ALTER TABLE sender_countries ADD COLUMN basis TEXT NOT NULL DEFAULT 'meta'")  # meta = address/name/timezone only; content = also message excerpts
        db.execute('''CREATE TABLE IF NOT EXISTS message_topics (
            user_id INTEGER NOT NULL, message_id TEXT NOT NULL, topics TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '', guessed_at INTEGER NOT NULL, PRIMARY KEY (user_id, message_id))''')  # topics: 'money,job'; '' = none fits
        db.execute('''CREATE TABLE IF NOT EXISTS message_websites (
            user_id INTEGER NOT NULL, message_id TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '', guessed_at INTEGER NOT NULL, PRIMARY KEY (user_id, message_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS google_identities (
            id INTEGER PRIMARY KEY, sub TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            email TEXT NOT NULL DEFAULT '', linked_at INTEGER NOT NULL)''')   # Google accounts that may sign in as a user (by Google's stable id)
        db.execute('''CREATE TABLE IF NOT EXISTS google_login_pending (
            state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, verifier TEXT NOT NULL, nonce TEXT NOT NULL,
            purpose TEXT NOT NULL, user_id INTEGER, expires_at INTEGER NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS gmail_attachments_message ON gmail_attachments(user_id,message_id)')
        db.execute('CREATE INDEX IF NOT EXISTS gmail_messages_inbox ON gmail_messages(user_id,in_inbox,internal_date DESC)')
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)')
        if not db.execute("SELECT 1 FROM schema_migrations WHERE name='task_images_backfill_v1'").fetchone():
            # Tasks created before image copying was added still need their cached mail images.
            db.execute('''INSERT INTO task_attachments(task_id,filename,mime_type,size_bytes,content,skipped_reason)
                SELECT t.id,a.filename,a.mime_type,a.size_bytes,a.content,a.skipped_reason
                FROM tasks t JOIN gmail_attachments a
                  ON a.user_id=t.user_id AND a.message_id=t.message_id
                WHERE t.source_provider IN ('gmail','imap') AND a.mime_type LIKE 'image/%'
                  AND NOT EXISTS (SELECT 1 FROM task_attachments x WHERE x.task_id=t.id)''')
            db.execute("INSERT INTO schema_migrations(name) VALUES('task_images_backfill_v1')")
    db.execute('PRAGMA foreign_keys=ON')
    return db


def _text(value, field, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'Invalid {field}')
    return value.strip()


def _deadline(date, time, zone):
    if date is None and time is None:
        return None
    if not isinstance(date, str) or not isinstance(time, str) or not isinstance(zone, str):
        raise ValueError('Date and time are required together')
    tz = ZoneInfo(settings.valid_timezone(zone))
    try:
        if len(date) != 10 or len(time) != 5:
            raise ValueError()
        local = datetime.fromisoformat(f'{date}T{time}').replace(tzinfo=tz)
        if local.strftime('%Y-%m-%d') != date or local.strftime('%H:%M') != time:
            raise ValueError()
        return local
    except ValueError as exc:
        raise ValueError('Invalid due date or time') from exc


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def decorate(row, now=None, due_soon_hours=48):
    item = dict(row)
    item['deadline_state'] = 'none'
    if item['status'] != 'done' and item['due_date']:
        due_time = item['due_time'] or '23:59'  # Legacy date-only rows expire at end of day.
        due = _deadline(item['due_date'], due_time, item['due_timezone'])
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError('now must be timezone-aware')
        delta = due - current  # Both are timezone-aware, so the task's own zone is respected.
        item['deadline_state'] = 'overdue' if delta.total_seconds() < 0 else ('due_soon' if delta <= timedelta(hours=due_soon_hours) else 'upcoming')
    return item


def _soon(db, user_id):
    return settings.stored(db, user_id).get('due_soon_hours', settings.DEFAULTS['due_soon_hours'])


def list_tasks(db, now=None, user_id=1):
    hours = _soon(db, user_id)
    rows = db.execute("SELECT t.*,(SELECT COUNT(*) FROM task_attachments a WHERE a.task_id=t.id) AS image_count FROM tasks t WHERE user_id=? ORDER BY status='done', due_date IS NULL, due_date, due_time, id", (user_id,))
    return [decorate(r, now, hours) for r in rows]



def task_summary(db, now=None, user_id=1):
    """Aggregated read-only counters for a local dashboard."""
    rows = list_tasks(db, now, user_id)
    by_status = {status: 0 for status in STATES}
    overdue = due_soon = 0
    for item in rows:
        by_status[item['status']] += 1
        overdue += item['deadline_state'] == 'overdue'
        due_soon += item['deadline_state'] == 'due_soon'
    return {'total': len(rows), 'by_status': by_status,
            'overdue': overdue, 'due_soon': due_soon}



def get_task(db, task_id, now=None, user_id=1):
    row = db.execute('SELECT t.*,(SELECT COUNT(*) FROM task_attachments a WHERE a.task_id=t.id) AS image_count FROM tasks t WHERE id=? AND user_id=?', (task_id,user_id)).fetchone()
    return decorate(row, now, _soon(db, user_id)) if row else None


def task_for_source(db, provider, message_id, user_id=1):
    row = db.execute('SELECT * FROM tasks WHERE source_provider=? AND message_id=? AND user_id=?', (provider, message_id,user_id)).fetchone()
    return decorate(row, due_soon_hours=_soon(db, user_id)) if row else None


def create_task(db, message, fields, user_id=1):
    title = _text(fields.get('title'), 'title', 200, True)
    description = _text(fields.get('description'), 'description', 1000)
    owner = _text(fields.get('owner', ''), 'owner', 120)
    priority = fields.get('priority')
    if priority not in PRIORITIES:
        raise ValueError('Invalid priority')
    date, time = fields.get('due_date'), fields.get('due_time')
    zone = fields.get('due_timezone', 'Pacific/Saipan')
    _deadline(date, time, zone)
    provider = _text(message.get('provider'), 'provider', 40, True)
    message_id = _text(message.get('id'), 'message ID', 200, True)
    thread_id = _text(message.get('thread_id') or '', 'thread ID', 200)
    project = _text(fields.get('project_label', ''), 'project label', 120)
    customer = _text(fields.get('customer_label', ''), 'customer label', 120)
    source_url = fields.get('source_url')
    if source_url is not None:
        source_url = _text(source_url, 'source URL', 500)
        if not source_url.startswith('https://'):
            raise ValueError('Source URL must use HTTPS')
    now = _now()
    try:
        with db:
            cursor = db.execute('''INSERT INTO tasks
                (message_id,title,description,owner,due_date,priority,status,created_at,updated_at,
                 source_provider,thread_id,project_label,customer_label,due_time,due_timezone,source_url,review_state,user_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (message_id,title,description,owner,date,priority,'new',now,now,
                 provider,thread_id,project,customer,time,zone,source_url,'reviewed',user_id))
            task_id = cursor.lastrowid
            db.execute('INSERT INTO task_events(task_id,action,changes,created_at) VALUES (?,?,?,?)',
                       (task_id,'created',json.dumps({'source_provider':provider,'message_id':message_id,
                                                    **({'ai_assisted':True} if fields.get('ai_assisted') is True else {})}),now))
            db.execute('''INSERT INTO task_attachments(task_id,filename,mime_type,size_bytes,content,skipped_reason)
                SELECT ?,a.filename,a.mime_type,a.size_bytes,a.content,a.skipped_reason
                FROM gmail_attachments a WHERE a.user_id=? AND a.message_id=? AND a.mime_type LIKE 'image/%' ''',
                (task_id,user_id,message_id))
    except sqlite3.IntegrityError as exc:
        raise ValueError('A task already exists for this message') from exc
    return get_task(db, task_id, user_id=user_id)


def task_images(db, task_id, user_id):
    if not db.execute('SELECT 1 FROM tasks WHERE id=? AND user_id=?',(task_id,user_id)).fetchone():
        return None
    return [dict(id=r['id'],name=r['filename'],mime_type=r['mime_type'],size_bytes=r['size_bytes'],
                 stored=bool(r['has_content']),skipped_reason=r['skipped_reason'])
            for r in db.execute('SELECT id,filename,mime_type,size_bytes,content IS NOT NULL AS has_content,skipped_reason FROM task_attachments WHERE task_id=? ORDER BY id',(task_id,))]


def task_image(db, task_id, image_id, user_id):
    return db.execute('''SELECT a.* FROM task_attachments a JOIN tasks t ON t.id=a.task_id
        WHERE t.id=? AND t.user_id=? AND a.id=?''',(task_id,user_id,image_id)).fetchone()


def task_image_review(db, task_id, user_id):
    row = db.execute('SELECT result_json,created_at FROM task_image_reviews WHERE task_id=? AND user_id=?',(task_id,user_id)).fetchone()
    return dict(json.loads(row['result_json']),created_at=row['created_at']) if row else None


def save_task_image_review(db, task_id, user_id, result):
    now = _now()
    with db:
        db.execute('''INSERT INTO task_image_reviews(task_id,user_id,result_json,created_at) VALUES(?,?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET result_json=excluded.result_json,created_at=excluded.created_at''',
            (task_id,user_id,json.dumps(result,ensure_ascii=False),now))
        db.execute('INSERT INTO task_events(task_id,action,changes,created_at) VALUES(?,?,?,?)',
                   (task_id,'images_reviewed',json.dumps({'image_count':len(result['images'])}),now))
    return dict(result,created_at=now)


def update_task(db, task_id, fields, user_id=1):
    allowed = {'title','description','owner','priority','due_date','due_time','due_timezone','status','project_label','customer_label','review_state','assigned_agent'}
    if not fields or set(fields) - allowed - {'revision'}:
        raise ValueError('Invalid update fields')
    with db:
        row = db.execute('SELECT * FROM tasks WHERE id=? AND user_id=?', (task_id,user_id)).fetchone()
        if row is None:
            return None
        if fields.get('revision') != row['revision']:
            raise ConflictError('Task changed; reload before saving')
        changes = {}
        for key in allowed & fields.keys():
            value = fields[key]
            if key in ('title','description','owner','project_label','customer_label'):
                value = _text(value,key,{'title':200,'description':1000,'owner':120,'project_label':120,'customer_label':120}[key],key=='title')
            if key == 'assigned_agent':
                value = _text(value, 'assigned agent', 120)
            if key == 'priority' and value not in PRIORITIES:
                raise ValueError('Invalid priority')
            if key == 'status' and value not in STATES:
                raise ValueError('Invalid status')
            if key == 'review_state' and value not in ('proposed','reviewed'):
                raise ValueError('Invalid review state')
            if key in ('due_date','due_time','due_timezone') and value is not None and not isinstance(value,str):
                raise ValueError('Invalid deadline')
            if value != row[key]:
                changes[key] = value
        if 'assigned_agent' in changes:
            # Assignment records a proposal only; it does not run any agent.
            changes['agent_status'] = 'pending_approval' if changes['assigned_agent'] else 'unassigned'
        date = changes.get('due_date',row['due_date'])
        time = changes.get('due_time',row['due_time'])
        zone = changes.get('due_timezone',row['due_timezone'])
        _deadline(date,time,zone)
        if not changes:
            return decorate(row, due_soon_hours=_soon(db, user_id))
        now = _now()
        assignments = ', '.join(f'{key}=?' for key in changes)
        cursor = db.execute(f'UPDATE tasks SET {assignments}, updated_at=?, revision=revision+1 WHERE id=? AND revision=?',
                            (*changes.values(),now,task_id,row['revision']))
        if cursor.rowcount != 1:
            raise ConflictError('Task changed; reload before saving')
        db.execute('INSERT INTO task_events(task_id,action,changes,created_at) VALUES (?,?,?,?)',
                   (task_id,'updated',json.dumps(changes),now))
    return get_task(db,task_id,user_id=user_id)


def reserve_agentsteers_dispatch(db, task_id, revision, user_id=1):
    """Atomically reserve explicit dispatch; never allow a second submission."""
    now = _now()
    with db:
        row = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)).fetchone()
        if row is None:
            return None
        if row["revision"] != revision:
            raise ConflictError("Task changed; reload before dispatching")
        if row["review_state"] != "reviewed" or row["status"] == "done":
            raise ValueError("Only reviewed, unfinished tasks may be dispatched")
        if row["agentsteers_job_id"] or row["agent_status"] in ("submitting", "dispatch_error"):
            raise ConflictError("Task already dispatched or being submitted")
        db.execute("""UPDATE tasks SET assigned_agent='AgentSteers Mock',
            agent_status='submitting', agentsteers_result='', revision=revision+1,
            updated_at=? WHERE id=? AND user_id=?""", (now, task_id, user_id))
        db.execute("INSERT INTO task_events(task_id,action,changes,created_at) VALUES (?,?,?,?)",
                   (task_id, "agent_dispatch_requested",
                    json.dumps({"mode": "mock", "approved": True}), now))
    return get_task(db, task_id, user_id=user_id)


def record_agentsteers_update(db, task_id, user_id, status, job_id=None, result=None):
    """Persist only a known mock task status/result, scoped to the task owner."""
    allowed = {"queued_mock", "cancelled", "submitting", "dispatch_error", "recovery_needed"}
    if status not in allowed:
        raise ValueError("Unknown mock agent status")
    if job_id is not None and (not isinstance(job_id, str) or len(job_id) != 32 or
                               any(c not in "0123456789abcdef" for c in job_id)):
        raise ValueError("Invalid agent task identifier")
    now = _now()
    with db:
        row = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)).fetchone()
        if row is None:
            return None
        # Never let another task replace an already recorded remote job.
        if job_id and row["agentsteers_job_id"] and row["agentsteers_job_id"] != job_id:
            raise ConflictError("Conflicting agent task identifier")
        if job_id and row["agent_status"] not in ("submitting", "queued_mock", "cancelled", "recovery_needed"):
            raise ConflictError("Task was not approved for dispatch")
        if not job_id and status == "dispatch_error" and row["agent_status"] != "submitting":
            raise ConflictError("Task is not being dispatched")
        text_result = json.dumps(result or {}, ensure_ascii=False)[:4000]
        if (row["agent_status"] == status and
            row["agentsteers_job_id"] == (job_id or row["agentsteers_job_id"]) and
            row["agentsteers_result"] == text_result):
            return decorate(row, due_soon_hours=_soon(db, user_id))
        db.execute("""UPDATE tasks SET agent_status=?, agentsteers_job_id=?,
            agentsteers_result=?, revision=revision+1, updated_at=?
            WHERE id=? AND user_id=?""",
            (status, job_id or row["agentsteers_job_id"], text_result, now, task_id, user_id))
        db.execute("INSERT INTO task_events(task_id,action,changes,created_at) VALUES (?,?,?,?)",
                   (task_id, "agent_status_updated",
                    json.dumps({"status": status, "task_id": job_id or row["agentsteers_job_id"]}), now))
    return get_task(db, task_id, user_id=user_id)


def events(db, task_id, user_id=1):
    return [dict(r) for r in db.execute('SELECT e.* FROM task_events e JOIN tasks t ON t.id=e.task_id WHERE e.task_id=? AND t.user_id=? ORDER BY e.id',(task_id,user_id))]


def delete_task(db, task_id, user_id=1):
    with db:
        return db.execute('DELETE FROM tasks WHERE id=? AND user_id=?',(task_id,user_id)).rowcount == 1


class ConflictError(Exception):
    pass
