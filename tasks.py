"""Local demo task tracking. No Gmail access and no email sending."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

STATES = ("new", "in_progress", "waiting", "done")
PRIORITIES = ("low", "medium", "high")
DEFAULT_DB = Path(__file__).resolve().parent / "data" / "tasks.sqlite3"

def connection(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        owner TEXT NOT NULL DEFAULT '',
        due_date TEXT,
        priority TEXT NOT NULL CHECK(priority IN ('low','medium','high')),
        status TEXT NOT NULL CHECK(status IN ('new','in_progress','waiting','done')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    db.commit()
    return db

def list_tasks(db):
    return [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY CASE WHEN status='done' THEN 1 ELSE 0 END, due_date IS NULL, due_date, id")]

def create_task(db, message, title, description, due_date=None, owner=""):
    if not isinstance(title, str) or not title.strip() or len(title)>200:
        raise ValueError("Title is required (max 200 characters)")
    if not isinstance(description,str) or len(description)>4000:
        raise ValueError("Invalid description")
    if not isinstance(owner,str) or len(owner)>120:
        raise ValueError("Invalid owner")
    if due_date is not None:
        if not isinstance(due_date,str) or len(due_date)!=10:
            raise ValueError("Invalid due date")
        try:
            datetime.strptime(due_date,"%Y-%m-%d")
        except ValueError as e:
            raise ValueError("Invalid due date") from e
    now=datetime.now(timezone.utc).isoformat()
    try:
        with db:
            cursor=db.execute("INSERT INTO tasks (message_id,title,description,owner,due_date,priority,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (message["id"],title.strip(),description.strip(),owner.strip(),due_date,message["priority"],"new",now,now))
    except sqlite3.IntegrityError as e:
        raise ValueError("A task already exists for this message") from e
    return dict(db.execute("SELECT * FROM tasks WHERE id=?",(cursor.lastrowid,)).fetchone())

def update_task(db,task_id, status,owner=None):
    if status not in STATES: raise ValueError("Invalid status")
    row=db.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
    if row is None: return None
    if owner is None: owner=row["owner"]
    if not isinstance(owner,str) or len(owner)>120: raise ValueError("Invalid owner")
    with db:
        db.execute("UPDATE tasks SET status=?,owner=?,updated_at=? WHERE id=?",
                   (status,owner.strip(),datetime.now(timezone.utc).isoformat(),task_id))
    return dict(db.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone())
