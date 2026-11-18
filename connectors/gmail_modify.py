"""Gmail write access, kept deliberately tiny and apart from gmail_readonly.py.

The only operation here sets or clears the UNREAD label on ONE message. There is no code anywhere in Inbox
Tend that sends mail, creates drafts, trashes or deletes messages, or changes any other label.

The OAuth permission this needs (`gmail.modify`) is broader than what the code does: Google's wording for it is
"read, compose and send emails ... except permanent deletion". Request it only when the user opts in.
"""
from __future__ import annotations
import json
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API = "https://gmail.googleapis.com/gmail/v1/users/me"
MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


class GmailWriteError(RuntimeError):
    """Safe error without response bodies or credentials."""


def set_unread(token: str, message_id: str, unread: bool, *, opener=urlopen) -> None:
    """Add (unread=True) or remove (unread=False) the UNREAD label of one message."""
    if not isinstance(token, str) or not token.strip():
        raise ValueError("A runtime OAuth access token is required")
    if (not isinstance(message_id, str) or not message_id or
            not all(ch.isalnum() or ch in "-_" for ch in message_id)):
        raise ValueError("Invalid message ID")
    body = {"addLabelIds": ["UNREAD"]} if unread else {"removeLabelIds": ["UNREAD"]}
    req = Request(API + "/messages/" + quote(message_id, safe="") + "/modify", method="POST",
                  data=json.dumps(body).encode(),
                  headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with opener(req, timeout=15) as response:
            response.read(100_001)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise GmailWriteError("Gmail refused the change: write access was not granted. Allow it again in Settings → Gmail access.") from None
        if exc.code == 404:
            raise GmailWriteError("Gmail no longer has this message (it may have been deleted).") from None
        raise GmailWriteError(f"Gmail rejected the change (HTTP {exc.code}).") from None
    except (URLError, TimeoutError, OSError):
        raise GmailWriteError("Gmail could not be reached.") from None
