# Gmail Web OAuth, read-only

Inbox Tend supports an explicit Gmail connection from the signed-in workspace. The requested scope is exactly `https://www.googleapis.com/auth/gmail.readonly`. The Inbox page lists messages from the local SQLite copy. **Refresh inbox** (and the first visit after connecting) starts a background sync (`POST /api/connectors/gmail/sync`, progress in `GET /api/connectors/gmail/status`) that lists up to `INBOX_TEND_SYNC_MAX_MESSAGES` messages (overridable per user on the Settings page, as are the attachment limit and whether sent mail is synced) for each of `INBOX_TEND_SYNC_QUERY` (inbox) and `INBOX_TEND_SYNC_SENT_QUERY` (sent), flags them per folder (`in_inbox`, `in_sent`; `GET /api/connectors/gmail/messages?folder=inbox|sent`), shows a message's whole stored conversation thread alongside it, and can group the list three ways (`GET /api/connectors/gmail/messages?folder=…&view=threads|messages|people`, remembered per user as the `inbox_view` setting): by Gmail conversation, one row per message, or one row per person (the sender of mail you received, the first recipient of mail you sent, matched on the lower-cased address). A group is listed in a folder when any of its messages is in it and holds all its stored messages from both folders. Grouping is a local query: no Gmail request, no AI, and downloads each one not stored yet with `format=full`: all headers, text and HTML bodies, labels, and attachments no larger than `INBOX_TEND_ATTACHMENT_MAX_BYTES` (larger ones are recorded as metadata only). Messages are committed one by one, so an interrupted sync resumes where it stopped, and stored messages that left the inbox are flagged and kept. Data is scoped by app user in `gmail_messages` and `gmail_attachments`; disconnecting Gmail deletes both. The scope stays `gmail.readonly`: nothing is sent, modified or deleted in Gmail. Bodies and attachments are untrusted content: the UI renders plain text only, HTML is stored but never rendered, and attachments are served as `application/octet-stream` downloads with `nosniff` and `Content-Disposition: attachment` (text/CSV can be previewed as text). Gmail content is not sent to agents or logged.

## Required Google Cloud setup

Create an OAuth **Web application** client in a Google Cloud project with Gmail API enabled and a configured consent screen. For the currently documented internal host, set the Authorized redirect URI to exactly:

`https://inboxtend.scyllar.us/api/connectors/gmail/callback`

The browser begins authorization by navigating to Google's server; no JavaScript OAuth client or Authorized JavaScript origin is required for this server-side flow. If Google Cloud asks for an origin, use `https://inboxtend.scyllar.us`. Add intended test users if the consent screen is in testing mode. Gmail read-only is a restricted scope; public use may require Google's verification and security review.

Install the dependency with `python3 -m pip install -r requirements.txt`. Set these keys in the private, Git-ignored `.env` or protected service environment:

- `INBOX_TEND_GOOGLE_CLIENT_ID`: Web application client ID (ends in `.apps.googleusercontent.com`).
- `INBOX_TEND_GOOGLE_CLIENT_SECRET`: matching Web application client secret.
- `INBOX_TEND_GOOGLE_REDIRECT_URI`: exactly `https://inboxtend.scyllar.us/api/connectors/gmail/callback` for the internal host, byte-for-byte matching Google Cloud.
- `INBOX_TEND_TOKEN_KEY`: a stable 32-byte random key encoded with URL-safe base64. Generate locally with `python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'`. Keep it separate from database backups; losing it makes stored Gmail connections unreadable.

Never commit these values. A local HTTP test is allowed only with a `localhost` or `127.0.0.1` redirect URI registered separately in Google Cloud, including the exact port. The web client cannot use the Desktop client JSON from the older one-shot prototype.

## Flow and storage

1. The signed-in user presses Connect or Reconnect. A JSON POST requires same origin and the session CSRF token. The server creates a random state and PKCE verifier for that user and the hash of that exact app session, expiring in five minutes. A second attempt invalidates the first.
2. Google redirects to the fixed callback. The callback requires the same signed-in session, matching state, and an unused pending request. The server consumes state before exchanging the code. It sends the Web client secret and PKCE verifier directly to Google's token endpoint.
3. The server accepts only the exact Gmail read-only scope and a refresh token. It encrypts the refresh token with AES-256-GCM and binds the ciphertext to the app user ID. The access token is never persisted. Database and directory permissions are restricted to the process owner.
4. Inbox reads refresh access server-side, list only inbox message IDs, then request selected headers and snippet. Disconnect deletes the local encrypted token and pending state. To revoke the Google grant itself, also remove Inbox Tend access in the Google Account permissions page.

The app currently has one bootstrapped administrator. Task queries and Gmail connections are filtered by app user ID. Legacy tasks are assigned to user ID 1 during migration; review ownership before introducing additional users. The legacy task table still has a global unique message ID constraint, so adding a second user requires a schema migration to permit the same demo message ID for multiple users.

## Security and operational limits

The current server trusts a loopback proxy that rewrites `Host` to the loopback application address while preserving the browser's `Origin`. The app accepts the configured HTTPS origin and rejects other origins for writes. Validate that configuration and HTTPS before enabling real Gmail. The reverse proxy must not log OAuth callback query strings, because Google places the authorization code in the callback URL. The Python server suppresses request logging. Session cookies are `HttpOnly`, `SameSite=Lax`, and marked `Secure` when the proxy sends `X-Forwarded-Proto: https`; only a trusted local proxy may set that header. Back up encrypted refresh tokens and the encryption key separately, with access restricted. There is no background sync, pagination UI, token-rotation handling, or provider-side revocation on disconnect. Use a test mailbox first and establish retention/backup procedures before using client correspondence.
