import base64
import tempfile
import unittest
from unittest.mock import patch

import gmail_web
import imap_web
from tasks import connection

RAW = b'From: Alice <alice@example.org>\r\nTo: You <you@icloud.com>\r\nSubject: Hello\r\nDate: Thu, 19 Nov 2026 10:00:00 +0000\r\nMessage-ID: <hello@example.org>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nPlease review this.\r\n'

class FakeIMAP:
    stores = []
    def __init__(self,*args,**kwargs): self.selected = None
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def login(self,*args): return 'OK',[b'']
    def list(self): return 'OK',[b'(\\HasNoChildren) "/" "INBOX"']
    def select(self,folder,readonly=False): self.selected=folder; return 'OK',[b'1']
    def response(self,name): return name,[b'123']
    def uid(self,command,*args):
        if command=='search': return 'OK',[b'7']
        if command=='store':
            self.stores.append(args)
            return 'OK',[b'']
        return 'OK',[(b'7 (UID 7 BODY[] {1}',RAW),b' FLAGS (\\Seen))']

class RejectFlags(FakeIMAP):
    def uid(self,command,*args):
        if command=='store': return 'NO',[b'not allowed']
        return super().uid(command,*args)

class IMAPTests(unittest.TestCase):
    def test_existing_database_opens_during_another_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory+'/mail.sqlite3'
            writer = connection(path)
            writer.execute('BEGIN IMMEDIATE')
            try:
                with connection(path) as reader:
                    self.assertEqual(reader.execute('SELECT 1').fetchone()[0],1)
            finally:
                writer.rollback()
                writer.close()

    def test_connect_sync_and_remote_read(self):
        with tempfile.TemporaryDirectory() as directory:
            db=connection(directory+'/mail.sqlite3')
            config={'INBOX_TEND_TOKEN_KEY':base64.urlsafe_b64encode(b'x'*32).decode()}
            with patch.object(imap_web.imaplib,'IMAP4_SSL',FakeIMAP):
                account_id=imap_web.add(db,config,1,'you@icloud.com','imap.mail.me.com','you','secret')
                self.assertLess(account_id,0)
                self.assertEqual(imap_web.add(db,config,1,'you@icloud.com','imap.mail.me.com','you','new-secret'),account_id)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM imap_connections WHERE user_id=1').fetchone()[0],1)
                self.assertNotIn(b'secret',bytes(imap_web.account(db,1,account_id)['password']))
                imap_web.sync(db,config,1,account_id)
            messages=gmail_web.cached_inbox(db,1)
            self.assertEqual(len(messages),1)
            self.assertEqual(messages[0]['subject'],'Hello')
            self.assertFalse(messages[0]['unread'])
            self.assertEqual(gmail_web.message(db,config,1,messages[0]['id'])['body_text'].strip(),'Please review this.')
            with patch.object(imap_web.imaplib,'IMAP4_SSL',FakeIMAP):
                result = gmail_web.set_read(db,config,1,messages[0]['id'],False)
                self.assertTrue(result['imap_changed'])
                self.assertEqual(FakeIMAP.stores[-1][1],'-FLAGS.SILENT')
                result = gmail_web.mark_all_read(db,config,1,[messages[0]['id']])
                self.assertEqual(result['imap_changed'],1)
                self.assertEqual(FakeIMAP.stores[-1][1],'+FLAGS.SILENT')
            with patch.object(imap_web.imaplib,'IMAP4_SSL',RejectFlags):
                failure = gmail_web.set_read(db,config,1,messages[0]['id'],False)
            self.assertFalse(failure['imap_changed'])
            self.assertIn('rejected',failure['gmail_error'])
            self.assertTrue(gmail_web.cached_inbox(db,1)[0]['unread'])
            imap_web.remove(db,1,account_id)
            self.assertEqual(gmail_web.stored_count(db,1),0)
            db.close()

    def test_bulk_seen_uses_bounded_uid_sets(self):
        with tempfile.TemporaryDirectory() as directory:
            db=connection(directory+'/mail.sqlite3')
            config={'INBOX_TEND_TOKEN_KEY':base64.urlsafe_b64encode(b'x'*32).decode()}
            with patch.object(imap_web.imaplib,'IMAP4_SSL',FakeIMAP):
                account_id=imap_web.add(db,config,1,'you@icloud.com','imap.mail.me.com','you','secret')
                FakeIMAP.stores.clear()
                ids=[f'imap-{account_id}~123~inbox~{uid}' for uid in range(1,206)]
                imap_web.set_seen(db,config,1,account_id,ids,True)
            self.assertEqual(len(FakeIMAP.stores),3)
            self.assertEqual([len(args[0].split(',')) for args in FakeIMAP.stores],[100,100,5])
            db.close()

if __name__=='__main__': unittest.main()
