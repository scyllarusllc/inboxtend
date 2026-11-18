import base64
import io
import json
import unittest
from urllib.error import HTTPError
from connectors.gmail_readonly import (
    SCOPES, GmailReadError, list_message_ids, get_message_metadata, get_message_full, get_attachment, html_to_text
)


def b64(value):
    return base64.urlsafe_b64encode(value if isinstance(value, bytes) else value.encode()).decode().rstrip('=')


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
            # Each header name is its own parameter; a stringified list makes Gmail return no headers.
            for name in ("From", "To", "Subject", "Date"):
                self.assertIn("metadataHeaders=" + name, req.full_url)
            self.assertNotIn("%5B", req.full_url)
            return FakeResponse({"id": "123", "threadId": "thread", "snippet": "We&#39;re here &amp; ready",
                                 "payload": {"headers": [
                                     {"name": "From", "value": "test@example.org"},
                                     {"name": "Subject", "value": "Hello"}]}})
        got = get_message_metadata("test-token", "123", opener=mock)
        self.assertEqual(got["headers"]["subject"], "Hello")
        self.assertEqual(got["snippet"], "We're here & ready")
        self.assertNotIn("body", got)

    def test_full_message_parses_nested_parts_headers_and_attachments(self):
        payload = {"mimeType": "multipart/mixed", "headers": [
            {"name": "Subject", "value": "Hi"}, {"name": "From", "value": "a@example.org"},
            {"name": "To", "value": "me@example.org"}, {"name": "X-Custom", "value": "kept"}], "parts": [
            {"mimeType": "multipart/alternative", "filename": "", "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": b64("Plain body")}},
                {"mimeType": "text/html", "filename": "", "body": {"data": b64("<p>Html body</p>")}}]},
            {"mimeType": "text/csv", "filename": "data.csv", "partId": "1", "body": {"attachmentId": "ATT-1_x", "size": 7}},
            {"mimeType": "text/plain", "filename": "inline.txt", "partId": "2", "body": {"data": b64("tiny"), "size": 4}}]}
        got = get_message_full("tok", "m1", opener=lambda req, timeout: FakeResponse(
            {"id": "m1", "threadId": "t", "internalDate": "42", "labelIds": ["INBOX", "UNREAD"], "sizeEstimate": 900,
             "snippet": "it&#39;s", "payload": payload}))
        self.assertEqual((got["body_text"], got["body_html"]), ("Plain body", "<p>Html body</p>"))
        self.assertEqual((got["headers"]["subject"], got["headers"]["to"]), ("Hi", "me@example.org"))
        self.assertIn(["X-Custom", "kept"], got["all_headers"])
        self.assertEqual((got["internal_date"], got["label_ids"], got["snippet"]), (42, ["INBOX", "UNREAD"], "it's"))
        names = {a["filename"]: a for a in got["attachments"]}
        self.assertEqual(names["data.csv"]["attachment_id"], "ATT-1_x")
        self.assertIsNone(names["data.csv"]["data"])
        self.assertEqual(names["inline.txt"]["data"], b"tiny")

    def test_full_message_html_only_and_charset(self):
        latin = "caf\u00e9".encode("latin-1")
        payload = {"mimeType": "text/html", "headers": [{"name": "Content-Type", "value": "text/html; charset=iso-8859-1"}],
                   "body": {"data": b64(b"<html><style>x{}</style><body><p>" + latin + b"</p><script>bad()</script></body></html>")}}
        got = get_message_full("tok", "m2", opener=lambda req, timeout: FakeResponse({"id": "m2", "payload": payload}))
        self.assertEqual(got["body_text"], "caf\u00e9")
        self.assertEqual(html_to_text("<p>a</p><p>b<br>c</p>"), "a\n\nb\nc")

    def test_attachment_download_is_size_limited_and_validated(self):
        urls = []
        def mock(req, timeout):
            urls.append(req.full_url)
            return FakeResponse({"size": 5, "data": b64(b"hello")})
        self.assertEqual(get_attachment("tok", "m1", "A/B", max_bytes=100, opener=mock), b"hello")
        self.assertTrue(urls[0].endswith("/messages/m1/attachments/A%2FB"))
        with self.assertRaises(GmailReadError):
            get_attachment("tok", "m1", "A", max_bytes=3, opener=mock)
        with self.assertRaises(ValueError):
            get_attachment("tok", "../x", "A", max_bytes=3, opener=mock)
        with self.assertRaises(ValueError):
            get_attachment("tok", "m1", "", max_bytes=3, opener=mock)

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
