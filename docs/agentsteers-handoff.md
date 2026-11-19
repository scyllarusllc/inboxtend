# AgentSteers mock handoff (opt-in)

The **Tasks** screen exposes the same AgentSteers controls in two places: in the task-list card and inside the expanded **Edit task** details. Both use the same authenticated server routes and explicit confirmation dialog.

This is a **mock-only integration**. It never runs shell commands, Codex, sends mail, or modifies GitHub. No actual job completion is simulated beyond the remote mock server's status/result. The UI will show a `queued_mock` job; this is **not** a completed code task.

## User flow

1. Use an existing *reviewed* task saved from a Gmail message (or a task already in the database).
2. In the task card or expanded details, choose **Send to AgentSteers (mock only)**.
3. Confirm the exact action in the browser. The server requires signed-in session, CSRF protection, matching task revision, a task owner, and the explicit `DISPATCH MOCK` confirmation.
4. Inbox Tend sends **only task title and edited description** to the mock MCP endpoint. Email bodies, raw headers, attachments, Gmail OAuth tokens, credentials, and source identifiers are never sent directly.
5. A returned mock task ID and status are stored on the local task and shown in both views. Use **Refresh AgentSteers mock status** to read status and the simulated result.
6. The task history records dispatch and status updates. Uncertain failures cannot automatically be retried to avoid accidental duplicate submissions.

## Private server configuration

Bridge is **disabled by default**. The server operator must explicitly configure, outside version control:

- `INBOX_TEND_AGENTSTEERS_BRIDGE_ENABLED=1`
- `INBOX_TEND_AGENTSTEERS_MCP_URL=https://<trusted-mock-service-host>/mcp`
- `INBOX_TEND_AGENTSTEERS_MCP_TOKEN=<long-private-mock-token>`
- `INBOX_TEND_AGENTSTEERS_REPOSITORY=scyllarusllc/agentsteers` (or another explicitly allowed repository)

AgentSteers **must already run** its separate authenticated, HTTPS, mock-only MCP service with a matching token, exact Host, and matching repository allowlist. Do not configure a production executor endpoint or place the token in Git, browser JS, logs, screenshots, or an email. The integration does not provision, deploy, or verify that service.

The AgentSteers mock endpoint may not be deployed, so the button can return a configuration or connection error until a separately authorized operator prepares it. The `queued_mock` state is intentionally not changed into `done` unless the underlying service later implements that behavior.

## Sample demonstration

Use a fictional message (do **not** send a real email):

> Subject: Student registration page needs attention  
> Please investigate the website registration form and draft a list of changes. Do not publish or modify the live site.

Create and review the local task, dispatch it in mock mode, then refresh to see the job ID and simulated outcome. This is a demonstration scenario, not proof that a real email was fetched or a real agent ran.

**No test suite execution is part of this documentation change.** Existing authentication, CSRF, no-send Gmail boundary, and remote mock-only scope remain mandatory.
