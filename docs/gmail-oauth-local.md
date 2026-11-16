# Local Gmail OAuth test (read-only, no token persistence)

This is a **manual one-shot prototype**, not Gmail OAuth integrated into the Inbox Tend web dashboard. It does not store a refresh token or retain a signed-in session. No real Gmail account was accessed during development.

## Setup

1. In Google Cloud, enable Gmail API for your project and configure an OAuth consent screen. In testing mode, add your own Google account as a test user, if required. Gmail read-only is a restricted Gmail scope and public production use may need Google's verification and related security review.
2. Create an **OAuth client ID → Desktop app**, then download its JSON locally as \`client_secret.json\`. Do not commit the file or paste it into issues, chats, logs or screenshots. The filename is only a convention; the JSON itself contains a Desktop client configuration.
3. Use Python 3.10+ at the repository root and run:
   \`\`\`sh
   python3 gmail_oauth_local.py --client-config ./client_secret.json --limit 5
   \`\`\`
4. In the browser, approve **read-only Gmail access** only if you recognize the Google Cloud project and scope. A randomized localhost callback checks state and PKCE. It expires after three minutes.
5. The console lists at most five message header summaries. The process then exits; access token is not stored. The script neither sends nor changes email.

## Privacy and scope

- The flow requests \`gmail.readonly\`, uses PKCE S256 + random state and a localhost callback.
- Authorization code and access token are kept in process memory; do not capture or share a terminal log.
- Metadata retrieval may display personal subject lines. Use test mail while validating.
- This prototype does not add web-app login, refresh-token storage, persistent syncing, message bodies, agent calls or Gmail sending.
- The script uses Google API credentials belonging to the person running it. **Do not add a private OAuth client JSON to this public repository.**
- Add explicit CSRF checks, secure credential storage, session expiry and safe disconnect before integrating into the local dashboard.

## Tests
\`\`\`sh
python3 -m unittest discover -s tests -v
\`\`\`

## Future development
Add a local account-connection UI and explicit connect/disconnect, and keep OAuth session data server-side; do not expose tokens to frontend JavaScript. Before permitting any sending or destructive actions, implement scoped approvals, audit logs and intentional opt-in.
