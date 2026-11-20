import base64
import tempfile
import unittest
from unittest.mock import patch

import gmail_web
import imap_web
from tasks import connection

RAW = b'From: Alice <alice@example.org>\r\nTo: You <you@icloud.com>\r\nSubject: Hello\r\nDate: Thu, 19 Nov 2026 10:00:00 +0000\r\nMessage-ID: <hello@example.org>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nPlease review this.\r\n'

class FakeIMAP:
    def __init__(self,*args,**kwargs): self.selected = None
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def login(self,*args): return 'OK',[b'']
    def list(self): return 'OK',[b'(\\HasNoChildren) "/" "INBOX"']
    def select(self,folder,readonly=False): self.selected=folder; return 'OK',[b'1']
    def response(self,name): return name,[b'123']
    def uid(self,command,*args):
        if command=='search': return 'OK',[b'7']
        return 'OK',[(b'7 (FLAGS (\\Seen) RFC822 {1}',RAW),b')']

class IMAPTests(unittest.TestCase):
    def test_connect_sync_and_local_read(self):
        with tempfile.TemporaryDirectory() as directory:
            db=connection(directory+'/mail.sqlite3')
            config={'INBOX_TEND_TOKEN_KEY':base64.urlsafe_b64encode(b'x'*32).decode()}
            with patch.object(imap_web.imaplib,'IMAP4_SSL',FakeIMAP):
                account_id=imap_web.add(db,config,1,'you@icloud.com','imap.mail.me.com','you','secret')
                self.assertLess(account_id,0)
                self.assertNotIn(b'secret',bytes(imap_web.account(db,1,account_id)['password']))
                imap_web.sync(db,config,1,account_id)
            messages=gmail_web.cached_inbox(db,1)
            self.assertEqual(len(messages),1)
            self.assertEqual(messages[0]['subject'],'Hello')
            self.assertEqual(gmail_web.message(db,config,1,messages[0]['id'])['body_text'].strip(),'Please review this.')
            self.assertFalse(gmail_web.set_read(db,config,1,messages[0]['id'],True,write=True)['gmail_changed'])
            imap_web.remove(db,1,account_id)
            self.assertEqual(gmail_web.stored_count(db,1),0)
            db.close()

if __name__=='__main__': unittest.main()
