# Gmail Read-Only Connector — first implementation slice

**Status (2026-11-16):** REST transport and mocked unit tests added; **NOT a connected Gmail client**. This does not authorize use of an account, fetch live email, or implement OAuth login.

## Scope
- Module: `connectors/gmail_readonly.py`
- Read-only scope: `https://www.googleapis.com/auth/gmail.readonly`
- Supported: list IDs with pagination, fetch a message's selected headers and snippet.
- Unsupported: OAuth login, refresh-token management, persistent mailbox sync, body retrieval, send, delete, modify, draft creation.
- Access token is supplied in memory by a future OAuth layer. **Never embed access tokens in a URL, repo, log, screenshot or frontend source.**
- No real Gmail account is connected by this code.

## Tests
```sh
python3 -m unittest discover -s tests -v
```

## Next safe milestone
1. Add an installed-app or local loopback OAuth authorization-code + PKCE flow with Google Cloud OAuth consent configuration.
2. Require the minimal Gmail read-only scope and clear user consent; store secrets only in local OS credential storage with permission control.
3. Provide `/api/connectors/gmail/status` and explicit connect/disconnect controls (no credentials in responses).
4. Import only ID, thread, from, subject, date and a preview initially; introduce encrypted-at-rest persistence and retention/deletion policy before full bodies and attachments.
5. Validate input, enforce localhost origin/CSRF protection for any mutating local endpoints, and make Gmail mail content untrusted when building agent task proposals.
6. Preserve human approval for outbound mail or remote agent actions; never auto-send based on message text.

## API semantics
`list_message_ids(access_token, max_results=20, query="", page_token=None)` returns `message_ids` and `next_page_token`.

`get_message_metadata(access_token, message_id)` returns selected headers, snippet and IDs.

Both are transport utilities only; an OAuth token acquisition flow is required before use.
