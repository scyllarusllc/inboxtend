"""Safe plain-text reading view for cached email bodies."""
from __future__ import annotations

import re

from connectors.gmail_readonly import html_to_text

_HTML_START = re.compile(r'^\s*(?:<!doctype\s+html|<html\b|<(?:head|body|div|p|table|section)\b)', re.I)
_HTML_DOCUMENT = re.compile(r'<(?:!doctype\s+html|html\b|head\b|body\b|style\b)', re.I)


def looks_like_html(text: str | None) -> bool:
    source = text or ''
    return bool(_HTML_START.match(source) or _HTML_DOCUMENT.search(source[:500]))


def readable_body(body_text: str | None, body_html: str | None = None) -> str:
    """Return visible text without changing the raw message in storage."""
    plain = body_text or ''
    if looks_like_html(plain):
        return html_to_text(body_html or plain) or plain
    if not plain.strip() and body_html:
        return html_to_text(body_html)
    return plain
