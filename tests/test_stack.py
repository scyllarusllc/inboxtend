import tempfile
import unittest
import os
import zipfile
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlmodel import select

from asgi import app, RequestAdapter
from db_models import Task, session
from tasks import connection, create_task, list_tasks
from task_project import export_task, read_project


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
            old_config = getattr(app.state, 'gmail_config', None)
            token = 't' * 40
            app.state.auth_manager = type('Manager', (), {'session': lambda self, value: {'id': 1, 'csrf_token': 'csrf'} if value == token else None})()
            app.state.db_path = path
            app.state.gmail_config = {'INBOX_TEND_GOOGLE_REDIRECT_URI': 'https://mail.example.org/api/connectors/gmail/callback'}
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
                with connection(path) as db:
                    db.execute("INSERT INTO gmail_attachments(user_id,message_id,filename,mime_type,size_bytes,content) VALUES(1,'source','note.txt','text/plain',3,?)", (b'abc',))
                    db.execute("INSERT INTO gmail_attachments(user_id,message_id,filename,mime_type,size_bytes,content) VALUES(1,'source','image.jpg','image/jpeg',6,?)", (b'\xff\xd8\xffabc',))
                    db.commit()
                with tempfile.TemporaryDirectory() as projects, patch.dict(os.environ, {'INBOX_TEND_TASKS_DIR': projects}):
                    self.assertEqual(client.post(f"/api/tasks/{created['id']}/project", headers=headers).status_code, 403)
                    self.assertEqual(client.post(f"/api/tasks/{created['id']}/project", headers={**headers, 'X-CSRF-Token': 'csrf', 'Origin': 'https://evil.example.org'}).status_code, 403)
                    exported = client.post(f"/api/tasks/{created['id']}/project", headers={**headers, 'X-CSRF-Token': 'csrf', 'Origin': 'https://mail.example.org'})
                    self.assertEqual(exported.status_code, 200)
                    self.assertEqual(exported.json()['project']['attachments'], 2)
                    archive = client.get(f"/api/tasks/{created['id']}/attachments.zip", headers=headers)
                    self.assertEqual(archive.status_code, 200)
                    with zipfile.ZipFile(BytesIO(archive.content)) as bundle:
                        self.assertEqual(bundle.read(bundle.namelist()[0]), b'abc')
                    listing = client.get(f"/api/tasks/{created['id']}/project/attachments", headers=headers).json()['attachments']
                    self.assertEqual([item['kind'] for item in listing], ['text', 'image'])
                    self.assertEqual(client.get(f"/api/tasks/{created['id']}/project/attachments/0/preview", headers=headers).json()['content'], 'abc')
                    self.assertEqual(client.get(f"/api/tasks/{created['id']}/project/attachments/1/preview", headers=headers).content, b'\xff\xd8\xffabc')
                    self.assertEqual(client.get(f"/api/tasks/{created['id']}/project/attachments/1/download", headers=headers).content, b'\xff\xd8\xffabc')
                    self.assertEqual(client.get(f"/api/tasks/{created['id']}/project/attachments/2/preview", headers=headers).status_code, 404)
                    project = read_project(created['id'])
                    self.assertIn('3 saved emails', project['text'])
                    self.assertIn('image.jpg', project['text'])
                    folder = projects + '/' + str(created['id'])
                    with open(folder + '/notes.md', 'w', encoding='utf-8') as notes:
                        notes.write('Please review the updated context.')
                    export_task(path, 1, created['id'])
                    self.assertIn('Please review the updated context.', read_project(created['id'])['text'])
                    plan = {'steps': [], 'approach': {'en': 'Use the project notes.', 'zh': ''},
                            'summary': {'en': 'Review the task.', 'zh': ''}, 'reply_draft': '',
                            'reply_draft_zh': '', 'questions': [], 'risks': [], 'confidence': 'medium'}
                    with patch.object(RequestAdapter, 'auth_manager', app.state.auth_manager, create=True), \
                         patch.object(RequestAdapter, 'db_path', path, create=True), \
                         patch.object(RequestAdapter, 'gmail_config', app.state.gmail_config, create=True), \
                         patch('app.prefs.effective', return_value={'ai_enabled': True, 'ai_provider': 'codex', 'ai_model': ''}), \
                         patch('app.ai.work_plan', return_value=plan) as ai_plan:
                        response = client.post('/api/ai/work', headers={**headers, 'X-CSRF-Token': 'csrf'},
                                               json={'task_id': created['id'], 'refresh': True})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json()['saved']['project_used'])
                    self.assertIn('Please review the updated context.', ai_plan.call_args.kwargs['project_text'])
                    saved = client.get(f"/api/tasks/{created['id']}/plan", headers=headers).json()['saved']
                    self.assertEqual(saved['plan']['approach']['en'], 'Use the project notes.')
                    website_url = f"/api/tasks/{created['id']}/website-project"
                    with patch('website_jobs.start', return_value={'state': 'running', 'slug': 'site-test'}) as start_job:
                        self.assertEqual(client.post(website_url, headers=headers, json={'slug': 'site-test'}).status_code, 403)
                        started = client.post(website_url, headers={**headers, 'X-CSRF-Token': 'csrf'}, json={'slug': 'site-test'})
                        self.assertEqual(started.status_code, 200)
                        start_job.assert_called_once_with(path, 1, created['id'], 'site-test')
                    with patch('website_jobs.publish') as publish_job:
                        self.assertEqual(client.post(website_url + '/publish', headers={**headers, 'X-CSRF-Token': 'csrf'}, json={}).status_code, 400)
                        publish_job.assert_not_called()
                    with connection(path) as db:
                        image_id = db.execute("INSERT INTO task_attachments(task_id,filename,mime_type,size_bytes,content) VALUES(?, 'ocr.jpg','image/jpeg',6,?)",
                                              (created['id'], b'\xff\xd8\xffabc')).lastrowid
                        db.commit()
                    with patch('task_api.prefs.effective', return_value={'ai_enabled': True, 'ai_provider': 'codex', 'ai_model': ''}), \
                         patch('task_api.ai.extract_image_text', return_value=[{'number': 1, 'text': 'Original text'}]):
                        ocr = client.post(f"/api/tasks/{created['id']}/images/extract-text", headers={**headers, 'X-CSRF-Token': 'csrf'},
                                          json={'image_ids': [image_id]})
                    self.assertEqual(ocr.status_code, 200)
                    self.assertEqual(client.get(f"/api/tasks/{created['id']}/images", headers=headers).json()['texts'][0]['text_original'], 'Original text')
            finally:
                app.state.auth_manager = old_manager
                app.state.db_path = old_path
                app.state.gmail_config = old_config


if __name__ == '__main__':
    unittest.main()
