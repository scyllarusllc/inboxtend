# Architecture decision: Website authorization + local Agent

**Decision date:** 2026-11-16  
**Status:** Selected direction; design only, not implemented or deployed.

## Product model

InboxTend users start Gmail authorization at **https://inboxtend.email**. A locally installed **InboxTend Agent** performs mailbox synchronization, private indexing, AI analysis, task proposals and integrations with other explicitly approved agents. The website is the authorization/pairing control plane and does **not** serve as a default cloud mailbox.

A local Agent installation is required to obtain the local-first behavior described here. The existing standalone desktop OAuth proof-of-concept (`gmail_oauth_local.py`) remains an isolated prototype, not the production authorization flow.

## Suggested user journey

1. Install and launch InboxTend Agent locally, then request a short-lived **pairing code** from the website or launch an authenticated pairing session.
2. Open `inboxtend.email`, sign in, and explicitly pair the currently running Agent using a code plus a cryptographic, device-bound challenge. Pairing must be one-time, time-limited and user-confirmed; do not trust a mere user-entered code by itself.
3. Click **Connect Gmail**. Google's OAuth **Web application** authorization code flow uses HTTPS redirect to an exact registered callback on the website, validates `state`, PKCE when supported, redirect URI, and requested scope (`gmail.readonly`). Implement robust CSRF and replay prevention.
4. The backend exchanges the authorization code securely. Bind resulting authorization to the previously paired local Agent; deliver a short-lived, one-time handoff grant to that Agent via authenticated outbound TLS request. **Never place access or refresh tokens in URLs, browser JavaScript, logs, analytics or Git.**
5. The local Agent redeems that grant over authenticated TLS. If persistent sync is enabled, securely store the refresh token **on the user device** (OS keychain/credential manager), rotate/revoke when appropriate, and do not persist Gmail bodies or token copies on the website.
6. The Agent performs Gmail read-only API calls, indexes data locally, performs AI analysis and creates **unsent** draft proposals. Any external model or remote agent receiving user content needs an explicit data-flow notice and scoped consent.
7. Users can disconnect locally and revoke Google access. Design offline behavior, token expiry and consent/reauthorization clearly.

## Security and deployment boundaries

- Google OAuth Web client credentials and code-exchange logic stay on the backend. A secure handoff must account for the backend's brief access to Google authorization material; do **not** promise that the cloud can never see an access token.
- The backend should avoid long-lived refresh token storage. If holding credentials temporarily is necessary, use strong encryption, limited retention, strict access controls, deletion after redemption and audited handling.
- Treat email data as **untrusted task input**, never as instructions or authorization. Remote actions, sending and deletion require deliberate per-action approval.
- Domain must have HTTPS, a configured Google Cloud project, Gmail API and OAuth consent screen. Google classifies Gmail restricted scopes as restricted; public deployment may require verification and possibly security assessment depending on data handling.
- Optional website UI may connect securely to the Agent, but a normal public HTTPS page cannot access arbitrary local files or a localhost service without additional authenticated, origin-restricted design. Local-first service must not silently become a general-purpose cloud inbox.
- Prefer the simplest first vertical slice: secure pairing, Gmail metadata-only sync, local UI and disconnect. Do not enable automatic email sending.

## Next implementation milestones

1. Document and threat-model device pairing, OAuth callback and one-time grant exchange (including replay, account swap and agent impersonation).
2. Implement secure website OAuth backend; keep existing Desktop OAuth only for local testing.
3. Implement Agent registration/pairing and secure local OS credential storage.
4. Demonstrate read-only metadata sync with a test account and local interface.
5. Add local AI summaries, task proposals and approval-gated remote Agent delegation.

## Non-goals of this commit

This decision does **not** implement website OAuth, pairing infrastructure, token handoff, a production Agent install or verified live Gmail access. It changes the intended product architecture only.
