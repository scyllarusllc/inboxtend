import io
import json
import unittest
from urllib.error import HTTPError
from connectors.gmail_readonly import (
    SCOPES, GmailReadError, list_message_ids, get_message_metadata
)


class FakeResponse:
    def __init__(self, obj):
        self.body = io.BytesIO(json.dumps(obj).encode())
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, n):
        return self.body.read(n)


class GmailReadOnlyTests(unittest.TestCase):
    def test_minimal_scope(self):
        self.assertEqual(SCOPES, ("https://www.googleapis.com/auth/gmail.readonly",))

    def test_lists_ids_without_body(self):
        def mock(req, timeout):
            self.assertEqual(req.get_method(), "GET")
            self.assertIn("maxResults=5", req.full_url)
            return FakeResponse({"messages": [{"id": "123"}, {"id": "abc"}],
                                 "nextPageToken": "next"})
        self.assertEqual(list_message_ids("test-token", max_results=5, opener=mock),
                         {"message_ids": ["123", "abc"], "next_page_token": "next"})

    def test_metadata_only(self):
        def mock(req, timeout):
            self.assertIn("format=metadata", req.full_url)
            return FakeResponse({"id": "123", "threadId": "thread",
                                 "payload": {"headers": [
                                     {"name": "From", "value": "test@example.org"},
                                     {"name": "Subject", "value": "Hello"}]}})
        got = get_message_metadata("test-token", "123", opener=mock)
        self.assertEqual(got["headers"]["subject"], "Hello")
        self.assertNotIn("body", got)

    def test_rejects_invalid_id(self):
        with self.assertRaises(ValueError):
            get_message_metadata("test-token", "../secret")

    def test_error_suppresses_provider_body_and_token(self):
        def mock(req, timeout):
            raise HTTPError(req.full_url, 403, "Denied", None, io.BytesIO(b"secret"))
        with self.assertRaises(GmailReadError) as caught:
            list_message_ids("private-token", opener=mock)
        self.assertNotIn("private-token", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
