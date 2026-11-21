"""Owner-scoped, read-only task feed for the OrbitMinder integration.

Disabled unless both integration environment variables are configured. Never
returns message bodies, attachments, credentials, or task descriptions.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from tasks import _deadline


class FeedError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def _cursor_encode(snapshot_at, max_id, last_id):
    value = json.dumps([snapshot_at, max_id, last_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _cursor_decode(value):
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        snapshot_at, max_id, last_id = json.loads(raw)
        datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
        if not all(type(n) is int and n >= 0 for n in (max_id, last_id)) or last_id > max_id:
            raise ValueError()
        return snapshot_at, max_id, last_id
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise FeedError(400, "Invalid pagination cursor") from None


def _task(row):
    value = dict(row)
    due_at = None
    due_date = value["due_date"] or None
    if due_date and value["due_time"]:
        due_at = _deadline(due_date, value["due_time"], value["due_timezone"]).isoformat()
    source_url = value["source_url"] or None
    if source_url and urlsplit(source_url).scheme not in ("https", "http"):
        source_url = None
    assigned = value["assigned_agent"] or None
    job_id = value["agentsteers_job_id"] or None
    # This ID is not proof of a live running AgentSteers execution.
    executor = "agentsteers" if job_id else ("inboxtend" if assigned else None)
    return {
        "id": str(value["id"]), "title": value["title"][:500],
        "project": value["project_label"][:200] or None,
        "status": value["status"], "priority": value["priority"],
        "due_at": due_at, "due_date": due_date,
        "due_timezone": value["due_timezone"] or None,
        "source_url": source_url, "review_state": value["review_state"],
        "assigned_agent": assigned, "executor": executor,
        "agent_job_id": job_id, "agent_status": value["agent_status"] or None,
        "updated_at": value["updated_at"], "revision": value["revision"],
    }


def read_page(db, authorization, path):
    expected = os.environ.get("INBOX_TEND_INTEGRATION_TOKEN", "")
    owner_string = os.environ.get("INBOX_TEND_INTEGRATION_USER_ID", "")
    if not expected or len(expected) < 32 or not owner_string.isdecimal() or int(owner_string) < 1:
        raise FeedError(503, "Integration not configured")
    if not authorization.startswith("Bearer ") or not hmac.compare_digest(
        authorization.removeprefix("Bearer "), expected
    ):
        raise FeedError(401, "Unauthorized")

    params = parse_qs(urlsplit(path).query, keep_blank_values=True)
    if set(params) - {"limit", "cursor"} or any(len(v) != 1 for v in params.values()):
        raise FeedError(400, "Invalid query")
    try:
        limit = int(params.get("limit", ["50"])[0])
    except ValueError:
        raise FeedError(400, "Invalid limit") from None
    if not 1 <= limit <= 100:
        raise FeedError(400, "Limit must be 1–100")

    owner_id = int(owner_string)
    if "cursor" in params:
        cursor = params["cursor"][0]
        if len(cursor) > 2048:
            raise FeedError(400, "Cursor too long")
        snapshot_at, max_id, last_id = _cursor_decode(cursor)
    else:
        snapshot_at = datetime.now(timezone.utc).isoformat()
        max_id = db.execute("SELECT COALESCE(MAX(id),0) FROM tasks WHERE user_id=?", (owner_id,)).fetchone()[0]
        last_id = 0

    rows = db.execute(
        """SELECT id,title,project_label,status,priority,due_date,due_time,due_timezone,
                  source_url,review_state,assigned_agent,agentsteers_job_id,agent_status,
                  updated_at,revision
           FROM tasks WHERE user_id=? AND id>? AND id<=?
           ORDER BY id LIMIT ?""", (owner_id, last_id, max_id, limit + 1),
    ).fetchall()
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = _cursor_encode(snapshot_at, max_id, visible[-1]["id"]) if has_more else None
    return {
        "source": "inboxtend", "schema_version": 1,
        "snapshot_at": snapshot_at, "items": [_task(row) for row in visible],
        "next_cursor": next_cursor, "has_more": has_more,
    }
