import json
import os
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch
from fastapi.testclient import TestClient

from tasks import connection, create_task, scope_source, task_for_scope
from task_project import export_task
from asgi import app, RequestAdapter


class TaskScopeTests(unittest.TestCase):
    def test_inbox_create_route_uses_selected_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'api.sqlite3')
            with connection(path) as db:
                for mid in ('m1','m2'):
                    db.execute('INSERT INTO gmail_messages(user_id,message_id,peer_email,thread_id,account_id,fetched_at) '
                               'VALUES(1,?,?,?,1,0)', (mid,'same@example.org','thread-1'))
                db.commit()
            token = 't' * 40
            manager = type('Manager', (), {'session': lambda self, value: {'id': 1, 'csrf_token': 'csrf'} if value == token else None})()
            with patch.object(RequestAdapter, 'auth_manager', manager), \
                 patch.object(RequestAdapter, 'db_path', path), \
                 patch.object(RequestAdapter, 'gmail_config', {}), \
                 patch.object(RequestAdapter, '_gmail_message', return_value={'id':'m1'}):
                client = TestClient(app)
                response = client.post('/api/tasks', headers={'Host':'localhost:8765','Cookie':'it_session='+token,'X-CSRF-Token':'csrf'},
                                       json={'message_id':'m1','scope':'people','title':'Group follow-up','description':'', 'priority':'medium'})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()['task']['source_scope'], 'people')
            with connection(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM task_source_messages').fetchone()[0],2)

    def test_existing_task_migrates_to_message_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'old.sqlite3')
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, message_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, description TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', due_date TEXT, priority TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            old.execute("INSERT INTO tasks(id,message_id,title,description,priority,status,created_at,updated_at) VALUES(1,'legacy-mail','Legacy','','medium','new','2026-01-01','2026-01-01')")
            old.commit(); old.close()
            with connection(path) as db:
                row = db.execute('SELECT source_scope,scope_key,anchor_message_id FROM tasks WHERE id=1').fetchone()
                self.assertEqual(tuple(row), ('messages','legacy-mail','legacy-mail'))

    def test_people_conversation_and_message_copy_their_own_content_and_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'data.sqlite3')
            with connection(path) as db:
                for mid, peer, thread, account, date in [
                    ('m1', 'person@example.org', 'thread-a', 1, 1),
                    ('m2', 'person@example.org', 'thread-a', 1, 2),
                    ('m3', 'person@example.org', 'thread-b', 2, 3),
                    ('m4', 'other@example.org', 'thread-c', 1, 4),
                ]:
                    db.execute('INSERT INTO gmail_messages(user_id,message_id,peer_email,thread_id,account_id,body_text,subject,internal_date,fetched_at) '
                               'VALUES(?,?,?,?,?,?,?,?,?)', (1, mid, peer, thread, account, 'Body of ' + mid, mid, date, date))
                    db.execute('INSERT INTO gmail_attachments(user_id,message_id,filename,mime_type,size_bytes,content) '
                               'VALUES(?,?,?,?,?,?)', (1, mid, mid + '.txt', 'text/plain', 2, mid.encode()))
                db.commit()
                created = {}
                for scope, expected in [('people', 3), ('threads', 2), ('messages', 1)]:
                    source = scope_source(db, 1, 'm1', scope)
                    task = create_task(db, source, {'title': scope, 'description': '', 'priority': 'medium'}, 1)
                    created[scope] = task['id']
                    self.assertEqual(task['source_scope'], scope)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM task_source_messages WHERE task_id=?', (task['id'],)).fetchone()[0], expected)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM task_source_attachments WHERE task_id=?', (task['id'],)).fetchone()[0], expected)
                    self.assertEqual(task_for_scope(db, source, 1)['id'], task['id'])
                self.assertEqual(len(set(created.values())), 3)
                self.assertEqual(scope_source(db, 1, 'm3', 'people')['scope_key'], 'all:person@example.org')
                self.assertEqual(scope_source(db, 1, 'm1', 'people', 1)['source_ids'], ['m1', 'm2'])
                with self.assertRaises(ValueError): scope_source(db, 1, 'm1', 'people', 2)
                with self.assertRaises(ValueError): scope_source(db, 2, 'm1', 'people')
                db.execute('DELETE FROM gmail_attachments WHERE user_id=1')
                db.execute('DELETE FROM gmail_messages WHERE user_id=1')
                db.commit()
            with patch.dict(os.environ, {'INBOX_TEND_TASKS_DIR': str(Path(directory) / 'projects')}):
                for scope, expected in [('people', 3), ('threads', 2), ('messages', 1)]:
                    export_task(path, 1, created[scope])
                    folder = Path(directory) / 'projects' / str(created[scope])
                    emails = json.loads((folder / 'emails.json').read_text())
                    files = json.loads((folder / 'manifest.json').read_text())
                    self.assertEqual(len(emails), expected)
                    self.assertEqual(len(files), expected)
                    self.assertEqual(emails[0]['body_text'], 'Body of m1')


if __name__ == '__main__': unittest.main()
