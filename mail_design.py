"""Isolated, non-interactive rendering of an email's original HTML layout."""
from __future__ import annotations

import bleach
import re
from bleach.css_sanitizer import CSSSanitizer

TAGS = frozenset('a b blockquote br center div em font h1 h2 h3 h4 h5 h6 hr i img li ol p pre small span strong style sub sup table tbody td th thead tr u ul'.split())
CSS = CSSSanitizer(allowed_css_properties=frozenset('background background-color border border-bottom border-collapse border-color border-radius border-style border-width color display font-family font-size font-style font-weight height line-height margin margin-bottom margin-left margin-right margin-top max-width min-width padding padding-bottom padding-left padding-right padding-top text-align text-decoration vertical-align white-space width'.split()))


def clean_email_html(markup: str, remote_images: bool = False) -> str:
    def attribute(tag, name, value):
        if name in ('class', 'id', 'style', 'align', 'valign', 'width', 'height', 'bgcolor', 'alt', 'title', 'cellpadding', 'cellspacing', 'border'):
            return True
        if tag == 'img' and name == 'src':
            return value.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/gif;base64,')) or (remote_images and value.startswith('https://'))
        return False  # Links remain visible but inert; open them from the reading view.

    source = re.sub(r'<(script|noscript|iframe|object|template)\b[^>]*>[\s\S]*?</\1\s*>', '', markup[:500_000], flags=re.I)
    return bleach.clean(source, tags=TAGS, attributes=attribute,
                        protocols=('https', 'data'), css_sanitizer=CSS, strip=True)


def preview_document(markup: str, remote_images: bool = False) -> str:
    clean = clean_email_html(markup, remote_images)
    policy = "default-src 'none'; style-src 'unsafe-inline'; img-src data:" + (' https:' if remote_images else '') + "; font-src data:; form-action 'none'; base-uri 'none'"
    # The route also sends this CSP as a response header; the iframe itself has sandbox="".
    return '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + policy + '"></head><body>' + clean + '</body></html>'
