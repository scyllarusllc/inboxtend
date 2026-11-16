import io
import json
import unittest
from urllib.parse import parse_qs, urlsplit
from gmail_oauth_local import make_pkce, authorization_url, exchange_code, load_desktop_client


class FakeReply:
    def __init__(self, data):
        self.data = io.BytesIO(json.dumps(data).encode())
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, n):
        return self.data.read(n)


class OAuthTests(unittest.TestCase):
    def test_pkce_challenge(self):
        verifier, challenge = make_pkce()
        self.assertTrue(len(verifier) >= 43)
        self.assertEqual(len(challenge), 43)
        self.assertNotEqual(verifier, challenge)

    def test_consent_url_uses_only_readonly_scope_and_pkce(self):
        url = authorization_url(
            "demo.apps.googleusercontent.com", "http://127.0.0.1:9000/oauth/callback", "state", "challenge"
        )
        q = parse_qs(urlsplit(url).query)
        self.assertEqual(q["scope"], ["https://www.googleapis.com/auth/gmail.readonly"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["state"], ["state"])
        self.assertEqual(q["access_type"], ["online"])

    def test_exchange_verifier_posted_no_refresh(self):
        def fake(request, timeout):
            self.assertEqual(request.get_method(), "POST")
            payload = parse_qs(request.data.decode())
            self.assertEqual(payload["code_verifier"], ["verifier"])
            self.assertNotIn("refresh_token", payload)
            return FakeReply({"access_token": "temporary", "scope": "https://www.googleapis.com/auth/gmail.readonly"})
        token = exchange_code({"client_id": "demo.apps.googleusercontent.com"}, "code", "verifier",
                              "http://127.0.0.1:9000/oauth/callback", opener=fake)
        self.assertEqual(token, "temporary")

    def test_scope_must_be_confirmed(self):
        with self.assertRaises(RuntimeError):
            exchange_code({"client_id": "demo.apps.googleusercontent.com"}, "code", "verifier",
                          "http://127.0.0.1:9000/oauth/callback", opener=lambda *_args, **_kw: FakeReply({"access_token": "temporary"}))


if __name__ == "__main__":
    unittest.main()
