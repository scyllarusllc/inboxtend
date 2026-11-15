import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from app import Handler, MESSAGES, draft_reply, summarize

class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url=f"http://127.0.0.1:{cls.server.server_port}"
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
    def test_samples(self):
        self.assertEqual(len(MESSAGES),3)
        self.assertTrue(summarize(MESSAGES[0]))
        self.assertNotIn("fixed",draft_reply(MESSAGES[0]))
    def test_list(self):
        with urlopen(self.url+"/api/messages") as r: data=json.load(r)
        self.assertEqual(data["mode"],"demo")
        self.assertEqual(len(data["messages"]),3)
    def test_draft_unsent(self):
        req=Request(self.url+"/api/draft",data=b'{"id":"sample-1"}',headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req) as r: data=json.load(r)
        self.assertFalse(data["sent"])
        self.assertIn("review",data["draft"])
    def test_missing_message(self):
        req=Request(self.url+"/api/draft",data=b'{"id":"unknown"}',headers={"Content-Type":"application/json"},method="POST")
        with self.assertRaises(HTTPError) as err:urlopen(req)
        self.assertEqual(err.exception.code,404)
    def test_page(self):
        with urlopen(self.url) as r:self.assertIn(b"Inbox Tend",r.read())

if __name__=="__main__":unittest.main()
