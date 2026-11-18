"""SQLite store for reviewed local tasks and cached Gmail messages and attachments. Gmail credentials are stored encrypted, never in plaintext."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import sqlite3

STATES = ('new', 'in_progress', 'waiting', 'done')
PRIORITIES = ('low', 'medium', 'high')
DEFAULT_DB = Path(__file__).resolve().parent / 'data' / 'tasks.sqlite3'
SAIPAN = ZoneInfo('Pacific/Saipan')


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
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS user_sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS user_sessions_expiry ON user_sessions(expires_at)')
        db.execute('''CREATE TABLE IF NOT EXISTS gmail_oauth_pending (state_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, session_hash TEXT NOT NULL, verifier TEXT NOT NULL, expires_at INTEGER NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS gmail_connections (user_id INTEGER PRIMARY KEY, refresh_token BLOB NOT NULL, connected_at INTEGER NOT NULL)''')
        if 'last_sync' not in {r['name'] for r in db.execute('PRAGMA table_info(gmail_connections)')}:
            db.execute('ALTER TABLE gmail_connections ADD COLUMN last_sync INTEGER')
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
        }.items():
            if name not in message_columns:
                db.execute(f'ALTER TABLE gmail_messages ADD COLUMN {name} {definition}')
        db.execute('''CREATE TABLE IF NOT EXISTS gmail_attachments (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, message_id TEXT NOT NULL,
            part_id TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL, mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0, content BLOB, skipped_reason TEXT NOT NULL DEFAULT '')''')
        db.execute('CREATE INDEX IF NOT EXISTS gmail_attachments_message ON gmail_attachments(user_id,message_id)')
        db.execute('CREATE INDEX IF NOT EXISTS gmail_messages_inbox ON gmail_messages(user_id,in_inbox,internal_date DESC)')
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
    if zone != 'Pacific/Saipan':
        raise ValueError('Only Pacific/Saipan is supported')
    try:
        if len(date) != 10 or len(time) != 5:
            raise ValueError()
        local = datetime.fromisoformat(f'{date}T{time}').replace(tzinfo=SAIPAN)
        if local.strftime('%Y-%m-%d') != date or local.strftime('%H:%M') != time:
            raise ValueError()
        return local
    except ValueError as exc:
        raise ValueError('Invalid due date or time') from exc


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def decorate(row, now=None):
    item = dict(row)
    item['deadline_state'] = 'none'
    if item['status'] != 'done' and item['due_date']:
        due_time = item['due_time'] or '23:59'  # Legacy date-only rows expire at end of day.
        due = _deadline(item['due_date'], due_time, item['due_timezone'])
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError('now must be timezone-aware')
        delta = due - current.astimezone(SAIPAN)
        item['deadline_state'] = 'overdue' if delta.total_seconds() < 0 else ('due_soon' if delta <= timedelta(hours=48) else 'upcoming')
    return item


def list_tasks(db, now=None, user_id=1):
    rows = db.execute("SELECT * FROM tasks WHERE user_id=? ORDER BY status='done', due_date IS NULL, due_date, due_time, id", (user_id,))
    return [decorate(r, now) for r in rows]



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
    row = db.execute('SELECT * FROM tasks WHERE id=? AND user_id=?', (task_id,user_id)).fetchone()
    return decorate(row, now) if row else None


def task_for_source(db, provider, message_id, user_id=1):
    row = db.execute('SELECT * FROM tasks WHERE source_provider=? AND message_id=? AND user_id=?', (provider, message_id,user_id)).fetchone()
    return decorate(row) if row else None


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
                       (task_id,'created',json.dumps({'source_provider':provider,'message_id':message_id}),now))
    except sqlite3.IntegrityError as exc:
        raise ValueError('A task already exists for this message') from exc
    return get_task(db, task_id, user_id=user_id)


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
            return decorate(row)
        now = _now()
        assignments = ', '.join(f'{key}=?' for key in changes)
        cursor = db.execute(f'UPDATE tasks SET {assignments}, updated_at=?, revision=revision+1 WHERE id=? AND revision=?',
                            (*changes.values(),now,task_id,row['revision']))
        if cursor.rowcount != 1:
            raise ConflictError('Task changed; reload before saving')
        db.execute('INSERT INTO task_events(task_id,action,changes,created_at) VALUES (?,?,?,?)',
                   (task_id,'updated',json.dumps(changes),now))
    return get_task(db,task_id,user_id=user_id)


def events(db, task_id, user_id=1):
    return [dict(r) for r in db.execute('SELECT e.* FROM task_events e JOIN tasks t ON t.id=e.task_id WHERE e.task_id=? AND t.user_id=? ORDER BY e.id',(task_id,user_id))]


def delete_task(db, task_id, user_id=1):
    with db:
        return db.execute('DELETE FROM tasks WHERE id=? AND user_id=?',(task_id,user_id)).rowcount == 1


class ConflictError(Exception):
    pass
