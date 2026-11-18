"""Gmail read-only REST connector (transport and parsing only; OAuth lives in gmail_web).

The caller supplies a short-lived OAuth access token at runtime. Never save or log it.
Require an access token with https://www.googleapis.com/auth/gmail.readonly.
No sending, modifying, deleting, or draft-writing methods exist here.
"""
from __future__ import annotations
import base64
import codecs
import html
import json
import random
import re
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)


class GmailReadError(RuntimeError):
    """Safe error without response bodies or credentials."""


class GmailRateLimitError(GmailReadError):
    """Gmail kept refusing with a rate limit even after waiting and retrying."""


# Gmail answers "too many requests" with HTTP 429, but also with HTTP 403 and reason rateLimitExceeded.
_RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
_RETRY_DELAYS = (3, 6, 12, 24, 48)  # Seconds before each retry; the last wait is followed by one final attempt.
_cooldown = {"until": 0.0}          # Shared by all worker threads: when one is limited, every request waits.
_cooldown_lock = threading.Lock()
_sleep = time.sleep                 # Replaceable in tests.


def cooldown_remaining() -> float:
    """Seconds until requests resume after a rate limit (0 when not limited)."""
    return max(0.0, _cooldown["until"] - time.monotonic())


def _error_reasons(exc: HTTPError) -> set:
    """Google's machine-readable reason codes (e.g. rateLimitExceeded), never free text from the response."""
    try:
        err = json.loads(exc.read(20_000)).get("error", {})
        found = {x.get("reason") for x in err.get("errors", []) if isinstance(x, dict)}
        found |= {d.get("reason") for d in err.get("details", []) if isinstance(d, dict)}
    except Exception:
        return set()
    return {r for r in found if isinstance(r, str) and re.fullmatch(r"[A-Za-z_]{3,40}", r)}


def _describe(code: int, reasons: set) -> str:
    if reasons & _RATE_REASONS or code == 429:
        return (f"Gmail is rate-limiting requests (HTTP {code}); it still refused after waiting. "
                "Wait a few minutes, then press Refresh: what is already saved is kept.")
    if code == 403 and ({"accessNotConfigured"} & reasons):
        return f"Gmail API rejected the read request (HTTP 403). Check that the Gmail API is enabled for the Google Cloud project."
    return f"Gmail API rejected the read request (HTTP {code})." + (f" Reason: {', '.join(sorted(reasons))}." if reasons else "")


def _get_json(token: str, path: str, params: dict | None = None, *,
              opener=urlopen, max_bytes: int = 2_000_000) -> dict:
    if not isinstance(token, str) or not token.strip():
        raise ValueError("A runtime OAuth access token is required")
    if not path.startswith("/") or "://" in path or "?" in path:
        raise ValueError("Invalid Gmail API path")
    url = API + path + (("?" + urlencode(params, doseq=True)) if params else "")
    req = Request(url, method="GET",
                  headers={"Authorization": "Bearer " + token,
                           "Accept": "application/json"})
    for attempt in range(len(_RETRY_DELAYS) + 1):
        wait = cooldown_remaining()
        if wait:
            _sleep(wait)
        try:
            with opener(req, timeout=15) as response:
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise GmailReadError("Gmail response exceeds size limit")
            obj = json.loads(data)
            if not isinstance(obj, dict):
                raise ValueError("Unexpected Gmail response type")
            return obj
        except HTTPError as exc:
            # Do not print the response body; it may include account information.
            reasons = _error_reasons(exc)
            limited = exc.code == 429 or (exc.code == 403 and bool(reasons & _RATE_REASONS))
            if (limited or exc.code in (500, 502, 503, 504)) and attempt < len(_RETRY_DELAYS):
                try:
                    delay = min(120.0, float(exc.headers.get("Retry-After")))
                except (TypeError, ValueError, AttributeError):
                    delay = _RETRY_DELAYS[attempt] + random.random()
                with _cooldown_lock:
                    _cooldown["until"] = max(_cooldown["until"], time.monotonic() + delay)
                continue
            error = GmailRateLimitError if limited else GmailReadError
            raise error(_describe(exc.code, reasons)) from None
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
    # Gmail returns the snippet HTML-escaped (e.g. &#39;); it is shown as text, so decode it.
    try:
        internal_date = int(raw.get("internalDate", 0))
    except (TypeError, ValueError):
        internal_date = 0
    return {"id": raw.get("id"), "thread_id": raw.get("threadId"), "internal_date": internal_date,
            "snippet": html.unescape(raw.get("snippet", "")), "headers": safe_headers}


# ---- Full messages and attachments (still read-only: format=full, attachments.get) ----

def _b64decode(data) -> bytes:
    if not isinstance(data, str) or not data:
        return b""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _decode_text(raw: bytes, headers) -> str:
    charset = "utf-8"
    for h in headers if isinstance(headers, list) else []:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-type":
            found = re.search(r'charset="?([\w.:-]+)"?', str(h.get("value", "")), re.I)
            if found:
                charset = found.group(1)
    try:
        codecs.lookup(charset)
    except LookupError:
        charset = "utf-8"
    return raw.decode(charset, "replace")


class _HTMLText(HTMLParser):
    """Crude HTML-to-text for messages that only have an HTML part. Never used to render HTML."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(markup: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # Malformed markup: fall back to the raw text without tags.
        return re.sub(r"<[^>]+>", " ", markup).strip()
    text = "".join(parser.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _walk(payload, out) -> None:
    if not isinstance(payload, dict):
        return
    mime = str(payload.get("mimeType", ""))
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    filename = str(payload.get("filename") or "")
    parts = payload.get("parts")
    if isinstance(parts, list) and not filename:
        for part in parts:
            _walk(part, out)
        return
    if filename or body.get("attachmentId"):
        try:
            size = int(body.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        out["attachments"].append({
            "part_id": str(payload.get("partId", "")), "filename": filename or "attachment",
            "mime_type": mime or "application/octet-stream", "size": size,
            "attachment_id": body.get("attachmentId") if isinstance(body.get("attachmentId"), str) else None,
            "data": _b64decode(body.get("data")) if body.get("data") else None})
        return
    if mime in ("text/plain", "text/html"):
        text = _decode_text(_b64decode(body.get("data")), payload.get("headers"))
        out["text" if mime == "text/plain" else "html"].append(text)


def get_message_full(token: str, message_id: str, *, opener=urlopen) -> dict:
    """Get one complete message: all headers, text/HTML bodies and attachment descriptors.

    Small inline attachments carry their bytes in ``data``; larger ones carry an
    ``attachment_id`` to pass to :func:`get_attachment`.
    """
    if (not isinstance(message_id, str) or not message_id or
            not all(ch.isalnum() or ch in "-_" for ch in message_id)):
        raise ValueError("Invalid message ID")
    raw = _get_json(token, "/messages/" + quote(message_id, safe=""), {"format": "full"},
                    opener=opener, max_bytes=12_000_000)
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    all_headers, picked = [], {}
    for h in payload.get("headers", []) if isinstance(payload.get("headers"), list) else []:
        if isinstance(h, dict) and isinstance(h.get("name"), str):
            value = str(h.get("value", ""))[:10_000]
            all_headers.append([h["name"], value])
            picked.setdefault(h["name"].lower(), value)
    out = {"text": [], "html": [], "attachments": []}
    _walk(payload, out)
    body_html = "\n".join(out["html"])
    body_text = "\n\n".join(out["text"]) if out["text"] else html_to_text(body_html)
    try:
        internal_date = int(raw.get("internalDate", 0))
    except (TypeError, ValueError):
        internal_date = 0
    try:
        size_estimate = int(raw.get("sizeEstimate", 0))
    except (TypeError, ValueError):
        size_estimate = 0
    labels = [x for x in raw.get("labelIds", []) if isinstance(x, str)] if isinstance(raw.get("labelIds"), list) else []
    return {"id": raw.get("id"), "thread_id": raw.get("threadId"), "internal_date": internal_date,
            "snippet": html.unescape(raw.get("snippet", "")), "label_ids": labels,
            "size_estimate": size_estimate, "all_headers": all_headers,
            "headers": {k: picked[k] for k in ("from", "to", "cc", "subject", "date", "message-id") if k in picked},
            "body_text": body_text, "body_html": body_html, "attachments": out["attachments"]}


def get_attachment(token: str, message_id: str, attachment_id: str, *,
                   max_bytes: int, opener=urlopen) -> bytes:
    """Download one attachment (decoded). Refuses anything larger than ``max_bytes``."""
    if (not isinstance(message_id, str) or not message_id or
            not all(ch.isalnum() or ch in "-_" for ch in message_id)):
        raise ValueError("Invalid message ID")
    if not isinstance(attachment_id, str) or not attachment_id or len(attachment_id) > 4096:
        raise ValueError("Invalid attachment ID")
    raw = _get_json(token, "/messages/" + quote(message_id, safe="") + "/attachments/" + quote(attachment_id, safe=""),
                    opener=opener, max_bytes=max_bytes * 4 // 3 + 4096)
    data = _b64decode(raw.get("data"))
    if len(data) > max_bytes:
        raise GmailReadError("Attachment exceeds size limit")
    return data


# ---- Mailbox totals (counts only; the profile also holds the address, which is deliberately not returned) ----

def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def get_totals(token: str, *, opener=urlopen) -> dict:
    """How many messages Gmail holds: whole mailbox, inbox and sent, plus unread in the inbox."""
    profile = _get_json(token, "/profile", opener=opener)
    inbox = _get_json(token, "/labels/INBOX", opener=opener)
    sent = _get_json(token, "/labels/SENT", opener=opener)
    return {"all": _count(profile.get("messagesTotal")), "threads": _count(profile.get("threadsTotal")),
            "inbox": _count(inbox.get("messagesTotal")), "inbox_unread": _count(inbox.get("messagesUnread")),
            "sent": _count(sent.get("messagesTotal"))}
