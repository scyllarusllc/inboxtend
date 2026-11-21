import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlmodel import select

from asgi import app, RequestAdapter
from db_models import Task, session
from tasks import connection, create_task


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


if __name__ == '__main__':
    unittest.main()
