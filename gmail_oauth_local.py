"""One-shot local Gmail OAuth: installed-app PKCE, memory-only tokens, read-only.

Run with a Google Cloud OAuth Desktop client JSON:
    python3 gmail_oauth_local.py --client-config ./client_secret.json
This utility is deliberately separate from the sample-data web dashboard.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from connectors.gmail_readonly import (
    SCOPES, GmailReadError, list_message_ids, get_message_metadata
)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_OAUTH_WAIT_SECONDS = 180


def make_pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def load_desktop_client(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        client = data["installed"]
        client_id = client["client_id"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Provide a valid Google OAuth Desktop app JSON file") from None
    if not isinstance(client_id, str) or not client_id.endswith(".apps.googleusercontent.com"):
        raise ValueError("Expected a Google OAuth Desktop client ID")
    # Desktop client secret is not a confidential secret; keep the JSON file local.
    return {"client_id": client_id, "client_secret": client.get("client_secret", "")}


def authorization_url(client_id, redirect_uri, state, challenge):
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "online",
        "include_granted_scopes": "false",
    })


def exchange_code(client, code, verifier, redirect_uri, *, opener=urlopen):
    if not code or not verifier:
        raise ValueError("Missing authorization code or PKCE verifier")
    payload = {
        "client_id": client["client_id"],
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client.get("client_secret"):
        payload["client_secret"] = client["client_secret"]
    request = Request(
        TOKEN_URL, data=urlencode(payload).encode("ascii"), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener(request, timeout=15) as response:
            raw = response.read(100_001)
        if len(raw) > 100_000:
            raise ValueError("Oversized response")
        result = json.loads(raw)
    except HTTPError as exc:
        raise RuntimeError(f"OAuth exchange failed (HTTP {exc.code})") from None
    except (URLError, TimeoutError, OSError, ValueError, TypeError):
        raise RuntimeError("OAuth token exchange failed") from None
    token = result.get("access_token") if isinstance(result, dict) else None
    granted = set((result.get("scope") or "").split()) if isinstance(result, dict) else set()
    if not isinstance(token, str) or not token:
        raise RuntimeError("No access token returned")
    # A missing scope field is not positive proof of the permission: reject it.
    if not granted or not set(SCOPES).issubset(granted):
        raise RuntimeError("Required read-only Gmail permission was not granted")
    return token


def capture_callback(client_id, *, opener=webbrowser.open, timeout=MAX_OAUTH_WAIT_SECONDS):
    """Return (code, verifier, redirect_uri) without writing tokens to disk."""
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(32)
    outcome = {"code": None, "error": None, "finished": False}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] != "/oauth/callback":
                return self.reply(404, "Not found")
            # Avoid accepting an unsolicited localhost request as an OAuth callback.
            expected_host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected_host:
                return self.reply(400, "Invalid callback host")
            if outcome["finished"]:
                return self.reply(409, "Authorization already processed")
            parsed = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if parsed.get("state") != [state]:
                return self.reply(400, "Authorization state mismatch")
            outcome["finished"] = True
            if "error" in parsed:
                outcome["error"] = "Google authorization was declined or failed"
                status, message = 400, "Authorization not granted. You may close this tab."
            elif len(parsed.get("code", [])) == 1 and parsed["code"][0]:
                outcome["code"] = parsed["code"][0]
                status, message = 200, "Read-only authorization received. You may close this tab."
            else:
                outcome["error"] = "Missing authorization code"
                status, message = 400, "Authorization code was missing."
            done.set()
            self.reply(status, message)

        def reply(self, code, message):
            body = message.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            # Never log the OAuth callback URL: it contains the authorization code.
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth/callback"
    url = authorization_url(client_id, redirect_uri, state, challenge)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        print("Opening Google consent screen (Gmail read-only).")
        print("If your browser did not open, copy the authorization URL below:")
        print(url)
        opener(url)
        if not done.wait(timeout):
            raise TimeoutError("Authorization timed out. Please retry.")
        if outcome["error"]:
            raise RuntimeError(outcome["error"])
        return outcome["code"], verifier, redirect_uri
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description="Inbox Tend temporary Gmail read-only OAuth demo")
    parser.add_argument("--client-config", required=True, help="Local OAuth Desktop client JSON")
    parser.add_argument("--limit", type=int, default=5, help="Maximum email metadata entries (1–20)")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    client = load_desktop_client(args.client_config)
    code, verifier, redirect_uri = capture_callback(client["client_id"])
    access_token = exchange_code(client, code, verifier, redirect_uri)
    # A real access token exists only in process memory and dies with this process.
    ids = list_message_ids(access_token, max_results=args.limit)
    print(f"Read-only Gmail connection succeeded. Retrieved {len(ids['message_ids'])} message IDs.")
    for message_id in ids["message_ids"]:
        metadata = get_message_metadata(access_token, message_id)
        headers = metadata.get("headers", {})
        print(f"- {headers.get('date', '')} | {headers.get('from', '')} | {headers.get('subject', '')}")
    print("No email was sent or modified. No token was saved.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, TimeoutError, GmailReadError) as exc:
        raise SystemExit(f"Error: {exc}") from None
