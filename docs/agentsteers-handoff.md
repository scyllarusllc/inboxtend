# AgentSteers mock handoff (opt-in)

The **Tasks** screen exposes the same AgentSteers controls in two places: in the task-list card and inside the expanded **Edit task** details. Both use the same authenticated server routes and explicit confirmation dialog.

This is a **mock-only integration**. It never runs shell commands, Codex, sends mail, or modifies GitHub. No actual job completion is simulated beyond the remote mock server's status/result. The UI will show a `queued_mock` job; this is **not** a completed code task.

## Readiness feedback and sample data

The Tasks page calls the authenticated `GET /api/agentsteers/status` endpoint. It reports **disabled**, **needs_configuration**, **invalid_configuration**, or **configured_unverified** without exposing the token or endpoint. **Configured does not mean connected**: this endpoint intentionally makes no network call.

When the bridge is not ready, both dispatch controls are hidden. When configured, both the task-list control and expanded Edit task control can dispatch the same reviewed task; a shared browser in-flight guard and an atomic server reservation reject duplicate submissions.

`make demo` now seeds a completely **fictional** "Student registration page needs attention" email and an associated reviewed task. The sample is created in the demo database only. It is not fetched from Gmail and does not dispatch anything automatically.

A failed or uncertain dispatch is intentionally **not retried automatically**. If a mock remote accepted the task but the network response was lost, resubmitting could create a duplicate. Resolve and reconcile that state deliberately.

## Tasks page: configuration and demo entry

The Tasks list shows a **mock bridge status** message and a link to this guide. States are **disabled**, **needs_configuration**, **invalid_configuration**, and **configured_unverified**. The status only reflects local configuration; it does not claim remote connectivity.

When locally configured, select **Check AgentSteers connection** to make an authenticated **read-only MCP ping**. Only a successful ping produces **connected_mock** in the current browser page; this does not prove worker or Codex execution. On connection failure, the page disables dispatch until the operator resolves the problem and the connection check succeeds.

The existing **make demo** command rebuilds a *separate, disposable* local SQLite demo database. In **Tasks**, look for **Review student registration form issue (mock handoff)** with the **Fictional demo task** label. The demo task is deliberately tagged `source_provider=demo`; no genuine Gmail or production task is created. The example does not automatically dispatch or invoke any remote service. **Never run the destructive demo seed against a real user database.**

If connectivity has been checked, the page may poll one `queued_mock` job per minute while the page is visible. A restart of the AgentSteers in-memory mock server can invalidate the job ID; a missing job is *not* evidence of task completion.

## User flow

1. Use an existing *reviewed* task saved from a Gmail message (or a task already in the database).
2. In the task card or expanded details, choose **Send to AgentSteers (mock only)**.
3. Confirm the exact action in the browser. The server requires signed-in session, CSRF protection, matching task revision, a task owner, and the explicit `DISPATCH MOCK` confirmation.
4. Inbox Tend sends **only task title and edited description** to the mock MCP endpoint. Email bodies, raw headers, attachments, Gmail OAuth tokens, credentials, and source identifiers are never sent directly.
5. A returned mock task ID and status are stored on the local task and shown in both views. Use **Refresh AgentSteers mock status** to read status and the simulated result.
6. The task history records dispatch and status updates. Uncertain failures cannot automatically be retried to avoid accidental duplicate submissions.

**AgentSteers server operator guide:** [mock MCP server preparation](https://github.com/scyllarusllc/agentsteers/blob/main/docs/inboxtend-mock-server-preparation.md). It covers private persistent SQLite, an uninstalled non-root service template, HTTPS proxy review and rollback. No server deployment is implied.

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
