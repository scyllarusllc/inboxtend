"""Authentication tests use only disposable SQLite files and fictional credentials."""
import tempfile
import unittest
from pathlib import Path
from auth import AuthManager, cookie_token, hash_password, verify_password
from tasks import connection

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'tasks.sqlite3'
        self.password='fictional-test-password-123'
        self.auth=AuthManager(self.path,username='admin@example.test',password_hash=hash_password(self.password))
    def tearDown(self):
        self.temp.cleanup()
    def test_bootstrap_only_once(self):
        with connection(self.path) as db:
            row=db.execute('SELECT username,role,password_hash FROM users').fetchone()
            self.assertEqual((row['username'],row['role']),('admin@example.test','admin'))
            self.assertNotEqual(row['password_hash'],self.password)
        AuthManager(self.path,username='other@example.org',password_hash=hash_password('other-fictional-password'))
        with connection(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
        self.assertEqual(self.auth.authenticate('admin@example.test','wrong'),'invalid')
        self.assertEqual(self.auth.authenticate('other@example.org','other-fictional-password'),'invalid')
        self.assertEqual(self.auth.authenticate('admin@example.test',self.password)['role'],'admin')
    def test_session_and_revocation(self):
        user=self.auth.authenticate('admin@example.test',self.password)
        token,csrf=self.auth.new_session(user['id'])
        self.assertEqual(self.auth.session(token)['csrf_token'],csrf)
        self.assertEqual(cookie_token('it_session='+token),token)
        with connection(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_sessions').fetchone()[0],1)
            self.assertNotEqual(db.execute('SELECT token_hash FROM user_sessions').fetchone()[0],token)
        restarted=AuthManager(self.path)
        self.assertEqual(restarted.session(token)['username'],'admin@example.test')
        with connection(self.path) as db:
            db.execute('UPDATE user_sessions SET expires_at=0')
            db.commit()
        self.assertIsNone(restarted.session(token))
        self.auth.revoke(token)
        self.assertIsNone(self.auth.session(token))
    def test_invalid_hash_and_throttling(self):
        self.assertFalse(verify_password(self.password,'invalid'))
        self.assertFalse(verify_password('wrong',hash_password(self.password)))
        self.assertIsNone(self.auth.session('bad-token'))
        for _ in range(10):
            self.assertEqual(self.auth.authenticate('admin@example.test','wrong'),'invalid')
        self.assertEqual(self.auth.authenticate('admin@example.test',self.password),'throttled')

if __name__=='__main__':unittest.main()
