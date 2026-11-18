"""Gmail read-only REST connector (transport only; OAuth UI not yet implemented).

The caller supplies a short-lived OAuth access token at runtime. Never save or log it.
Require an access token with https://www.googleapis.com/auth/gmail.readonly.
No sending, modifying, deleting, or draft-writing methods exist here.
"""
from __future__ import annotations
import json
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)


class GmailReadError(RuntimeError):
    """Safe error without response bodies or credentials."""


def _get_json(token: str, path: str, params: dict | None = None, *,
              opener=urlopen) -> dict:
    if not isinstance(token, str) or not token.strip():
        raise ValueError("A runtime OAuth access token is required")
    if not path.startswith("/") or "://" in path or "?" in path:
        raise ValueError("Invalid Gmail API path")
    url = API + path + (("?" + urlencode(params)) if params else "")
    req = Request(url, method="GET",
                  headers={"Authorization": "Bearer " + token,
                           "Accept": "application/json"})
    try:
        with opener(req, timeout=15) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise GmailReadError("Gmail response exceeds size limit")
        obj = json.loads(data)
        if not isinstance(obj, dict):
            raise ValueError("Unexpected Gmail response type")
        return obj
    except HTTPError as exc:
        # Do not print the response body; it may include account information.
        hint = " Check that the Gmail API is enabled for the Google Cloud project." if exc.code == 403 else ""
        raise GmailReadError(f"Gmail API rejected the read request (HTTP {exc.code}).{hint}") from None
    except (URLError, TimeoutError, OSError):
        raise GmailReadError("Gmail API connection failed") from None
    except (ValueError, UnicodeError):
        raise GmailReadError("Invalid Gmail API JSON response") from None


def list_message_ids(token: str, *, max_results: int = 20, query: str = "",
                     page_token: str | None = None, opener=urlopen) -> dict:
    """List Gmail message IDs only, with Gmail-native pagination metadata."""
    if not isinstance(max_results, int) or not 1 <= max_results <= 100:
        raise ValueError("max_results must be an integer from 1 through 100")
    if not isinstance(query, str) or len(query) > 1024:
        raise ValueError("Invalid query")
    params = {"maxResults": max_results}
    if query:
        params["q"] = query
    if page_token:
        params["pageToken"] = page_token
    result = _get_json(token, "/messages", params, opener=opener)
    ids = [x["id"] for x in result.get("messages", [])
           if isinstance(x, dict) and isinstance(x.get("id"), str)]
    return {"message_ids": ids, "next_page_token": result.get("nextPageToken")}


def get_message_metadata(token: str, message_id: str, *, opener=urlopen) -> dict:
    """Get header-only metadata; does not download email bodies or attachments."""
    if (not isinstance(message_id, str) or not message_id or
            not all(ch.isalnum() or ch in "-_" for ch in message_id)):
        raise ValueError("Invalid message ID")
    raw = _get_json(token, "/messages/" + quote(message_id, safe=""),
                    {"format": "metadata", "metadataHeaders":
                     ["From", "To", "Subject", "Date"]}, opener=opener)
    headers = raw.get("payload", {}).get("headers", [])
    safe_headers = {}
    for h in headers if isinstance(headers, list) else []:
        if isinstance(h, dict) and h.get("name", "").lower() in ("from", "to", "subject", "date"):
            safe_headers[h["name"].lower()] = h.get("value", "")
    return {"id": raw.get("id"), "thread_id": raw.get("threadId"),
            "snippet": raw.get("snippet", ""), "headers": safe_headers}
