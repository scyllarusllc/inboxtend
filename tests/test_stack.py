import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlmodel import select

from asgi import app, RequestAdapter
from db_models import Task, session
from tasks import connection, create_task, list_tasks


class StackTests(unittest.TestCase):
    def test_fastapi_preserves_public_and_protected_routes(self):
        client = TestClient(app)
        self.assertEqual(client.get('/api/health', headers={'Host': 'localhost:8765'}).json(), {'status': 'ok'})
        self.assertEqual(client.get('/static/tasks.js', headers={'Host': 'localhost:8765'}).status_code, 200)
        result = client.get('/user/tasks', headers={'Host': 'localhost:8765'}, follow_redirects=False)
        self.assertEqual((result.status_code, result.headers['location']), (303, '/login'))

    def test_redirect_keeps_both_google_and_session_cookies(self):
        adapter = object.__new__(RequestAdapter)
        adapter._redirect('/user/inbox', (('Set-Cookie', 'it_glogin=; Max-Age=0'),
                                          ('Set-Cookie', 'it_session=example; HttpOnly')))
        self.assertEqual(len(adapter.response.headers.getlist('set-cookie')), 2)

    def test_sqlmodel_reads_existing_task_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/tasks.sqlite3'
            db = connection(path)
            created = create_task(db, {'id': 'mail-1', 'provider': 'gmail'},
                                  {'title': 'Review images', 'description': '', 'priority': 'medium'})
            db.close()
            with session(path) as db_session:
                task = db_session.exec(select(Task).where(Task.id == created['id'])).one()
                self.assertEqual((task.title, task.source_provider), ('Review images', 'gmail'))

    def test_native_task_routes_keep_existing_response_and_user_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/tasks.sqlite3'
            db = connection(path)
            created = create_task(db, {'id': 'mail-2', 'provider': 'gmail'},
                {'title': 'Inspect images', 'description': 'Translate them', 'priority': 'high'}, user_id=1)
            db.execute("INSERT INTO task_attachments(task_id,filename,mime_type,size_bytes,content) VALUES(?, 'one.jpg','image/jpeg',4,?)",
                       (created['id'], b'\xff\xd8\xff\x00'))
            db.commit()
            expected = list_tasks(db, user_id=1)
            db.close()
            old_manager = getattr(app.state, 'auth_manager', None)
            old_path = getattr(app.state, 'db_path', None)
            token = 't' * 40
            app.state.auth_manager = type('Manager', (), {'session': lambda self, value: {'id': 1} if value == token else None})()
            app.state.db_path = path
            try:
                client = TestClient(app)
                headers = {'Host': 'localhost:8765', 'Cookie': 'it_session=' + token}
                self.assertEqual(client.get('/api/tasks', headers=headers).json()['tasks'], expected)
                self.assertEqual(client.get('/api/tasks/summary', headers=headers).json()['summary']['total'], 1)
                self.assertEqual(client.get(f"/api/tasks/{created['id']}/images", headers=headers).json()['images'][0]['name'], 'one.jpg')
                self.assertEqual(client.get(f"/api/tasks/{created['id']}/images/1/thumbnail", headers=headers).content, b'\xff\xd8\xff\x00')
                self.assertEqual(client.get(f"/api/tasks/{created['id']}/correspondence", headers=headers).json()['total'], 0)
                self.assertEqual(client.get('/api/tasks', headers={'Host': 'localhost:8765'}).status_code, 401)
            finally:
                app.state.auth_manager = old_manager
                app.state.db_path = old_path

    def test_task_correspondence_includes_sent_and_received_from_all_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/tasks.sqlite3'
            with connection(path) as db:
                created = create_task(db, {'id': 'source', 'provider': 'gmail'}, {'title': 'Follow up', 'description': '', 'priority': 'medium'}, user_id=1)
                for mid, peer, owner, labels, stamp in [
                    ('source', 'person@example.com', 1, '[]', 100),
                    ('reply', 'person@example.com', 1, '["SENT"]', 200),
                    ('other-account', 'person@example.com', 1, '[]', 300),
                    ('different', 'other@example.com', 1, '[]', 400),
                    ('another-user', 'person@example.com', 2, '[]', 500),
                ]:
                    db.execute('INSERT INTO gmail_messages(user_id,message_id,peer_email,label_ids,internal_date,fetched_at,account_id) '
                               'VALUES(?,?,?,?,?,?,?)', (owner, mid, peer, labels, stamp, stamp, 2 if mid == 'other-account' else 1))
                db.commit()
            old_manager = getattr(app.state, 'auth_manager', None)
            old_path = getattr(app.state, 'db_path', None)
            token = 't' * 40
            app.state.auth_manager = type('Manager', (), {'session': lambda self, value: {'id': 1} if value == token else None})()
            app.state.db_path = path
            try:
                client = TestClient(app)
                headers = {'Host': 'localhost:8765', 'Cookie': 'it_session=' + token}
                result = client.get(f"/api/tasks/{created['id']}/correspondence", headers=headers)
                self.assertEqual(result.status_code, 200)
                data = result.json()
                self.assertEqual([m['id'] for m in data['messages']], ['other-account', 'reply', 'source'])
                self.assertEqual(data['total'], 3)
                self.assertEqual(data['messages'][1]['direction'], 'sent')
                self.assertTrue(data['messages'][2]['is_source'])
            finally:
                app.state.auth_manager = old_manager
                app.state.db_path = old_path


if __name__ == '__main__':
    unittest.main()
