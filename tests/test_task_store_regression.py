"""Regression tests for the local-only task store; no Gmail credentials or real messages."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tasks import (
    ConflictError, connection, create_task, events, get_task, list_tasks,
    task_for_source, update_task,
)


class TaskStoreRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connection(Path(self.temp.name) / "tasks.sqlite3")
        self.message = {
            "id": "gmail-001",
            "thread_id": "gmail-thread",
            "provider": "gmail",
        }
        self.fields = {
            "title": "Review request",
            "description": "Reply after checking the record",
            "owner": "",
            "priority": "high",
            "due_date": "2026-11-20",
            "due_time": "17:00",
            "due_timezone": "Pacific/Saipan",
        }

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_create_and_lookup_without_duplicate(self):
        task = create_task(self.db, self.message, self.fields)
        self.assertEqual(task["status"], "new")
        self.assertEqual(task["source_provider"], "gmail")
        self.assertEqual(task_for_source(self.db, "gmail", "gmail-001")["id"], task["id"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            create_task(self.db, self.message, self.fields)
        self.assertEqual(len(list_tasks(self.db)), 1)
        self.assertEqual(events(self.db, task["id"])[0]["action"], "created")

    def test_update_requires_current_revision_and_writes_audit_event(self):
        task = create_task(self.db, self.message, self.fields)
        changed = update_task(self.db, task["id"], {
            "revision": task["revision"],
            "status": "in_progress",
            "owner": "Demo owner",
        })
        self.assertEqual(changed["revision"], task["revision"] + 1)
        self.assertEqual(changed["status"], "in_progress")
        with self.assertRaises(ConflictError):
            update_task(self.db, task["id"], {
                "revision": task["revision"], "status": "done",
            })
        self.assertEqual(get_task(self.db, task["id"])["status"], "in_progress")
        history = events(self.db, task["id"])
        self.assertEqual([e["action"] for e in history], ["created", "updated"])

    def test_rejects_incomplete_deadline_and_unknown_status(self):
        bad = dict(self.fields, due_time=None)
        with self.assertRaises(ValueError):
            create_task(self.db, self.message, bad)
        task = create_task(self.db, self.message, self.fields)
        with self.assertRaisesRegex(ValueError, "Invalid status"):
            update_task(self.db, task["id"], {
                "revision": task["revision"], "status": "sent",
            })
        self.assertEqual(get_task(self.db, task["id"])["revision"], task["revision"])

    def test_deadline_state_is_timezone_aware(self):
        task = create_task(self.db, self.message, self.fields)
        early = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)
        overdue = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
        from tasks import decorate
        self.assertEqual(decorate(task, early)["deadline_state"], "due_soon")
        self.assertEqual(decorate(task, overdue)["deadline_state"], "overdue")
        done = update_task(self.db, task["id"], {
            "revision": task["revision"], "status": "done",
        })
        self.assertEqual(decorate(done, overdue)["deadline_state"], "none")


if __name__ == "__main__":
    unittest.main()
