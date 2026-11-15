# Inbox Tend

**Your inbox, thoughtfully handled.**

**Open source · Local-first · Agent-ready**

Inbox Tend is a proposed open-source, local-first AI email assistant and email-driven agent orchestration platform. It is designed to turn incoming messages into organized, reviewable tasks—and, when authorized, delegate those tasks to AI agents running locally or on other servers.

Start with a private web dashboard for triage, summaries, search, and reply drafts. Extend it with reusable Agent Skills and remote agent integrations to help carry out work requested by email.

> **Project status: Planning / pre-release.** The capabilities below describe the intended design and roadmap, not features that are already implemented. There is not yet a public release or installation procedure.

**Website (planned):** https://inboxtend.email  
**Maintainer:** Scyllarus LLC

## Why Inbox Tend?

Email is often where work begins, but carrying that work through requires switching between applications, tools, and people. Inbox Tend aims to provide a single place to understand a request, decide what should happen next, authorize any meaningful action, and track its outcome.

Inbox Tend is **not just another email client**. Its long-term goal is to connect email workflows to an extensible network of specialized agents while preserving human control.

## Core principles

- **Local-first:** Keep the primary application, workflow state, and locally stored email data on infrastructure you control by default. Remote integrations are optional and explicit.
- **Human control:** An email does not grant permission to execute its instructions. Sensitive actions require policy checks and, by default, explicit user approval.
- **Open and extensible:** Favor documented interfaces, reusable skills, and provider-independent architecture.
- **Auditable execution:** Record what was requested, approved, delegated, attempted, and confirmed—without falsely reporting a task as complete.
- **Privacy by design:** Minimize collection and transmission of message content. Clearly disclose when an external email provider, model API, or remote agent receives data.

## Planned capabilities

### Phase 1: Local email assistant

- Connect a Gmail account through an appropriate OAuth flow; support IMAP and additional providers in later iterations.
- Show a locally hosted web dashboard for message triage.
- Summarize conversations, identify action items, and rank messages by relevance.
- Search messages and prepare context-aware reply **drafts**.
- Keep sending, deleting, and moving messages behind user-controlled permissions.

### Phase 2: Agent Skills

- Expose well-defined, reusable skills for reading selected messages, creating task proposals, drafting replies, and checking task state.
- Define structured inputs/outputs, scoped permissions, and predictable error handling.
- Support integrations with compatible AI agent runtimes without tying the core to a single model vendor.

### Phase 3: Distributed agent orchestration

- Register approved local and remote agents with explicit capabilities.
- Route authorized tasks to suitable agents, including agents on other servers.
- Track queued, running, blocked, failed, and completed tasks.
- Collect execution evidence and prepare a follow-up email draft for review.
- Support retries, timeouts, cancellation, and safe handling of partially completed work.

### Phase 4: Workflow automation

- Add user-configurable rules and approval policies.
- Coordinate multiple agents on multi-step tasks with dependency tracking.
- Provide execution history, notifications, and operational controls.

## Example: From email to completed work

**Incoming email:** “Could you fix the login issue on our website and let me know when it is resolved?”

1. Inbox Tend ingests the email and identifies a possible software maintenance request.
2. It creates a **proposed task** with relevant context, target system, and the actions it expects to take.
3. The user reviews and authorizes the request under a scoped permission policy.
4. The orchestrator sends an appropriately limited task to an approved coding agent on a remote server.
5. The agent returns its actions, test results, and any remaining uncertainties.
6. Inbox Tend displays the outcome with evidence and prepares a reply draft.
7. The user reviews and approves the reply before it is sent.

**Important:** Neither an email request nor an AI-generated plan is, by itself, authorization to change a server or send mail. Inbox Tend must distinguish a successful tool invocation from a verified outcome.

## Proposed architecture

```text
Gmail / IMAP / Future Providers
             |
             v
     Email Connector Layer
             |
             v
    Inbox Tend Core (local-first)
   - Message indexing and triage
   - AI analysis and task proposals
   - Permission and approval engine
   - Task state and audit history
       |                 |
       v                 v
 Local Web Dashboard   Agent Skill Interface
                         |
                         v
                  Task Orchestrator
                         |
                Approved, scoped calls
                         |
              +----------+----------+
              |          |          |
           Local      Remote     Remote
           Agent      Agent A    Agent B
              +----------+----------+
                         |
                         v
                Results + evidence
                         |
                         v
                Reviewable email draft
```

The core should function without a remote agent. Delegation requires a deliberate integration and authorization step.

## Security model (design requirements)

Email is **untrusted input**. Messages, attachments, and quoted text can include malicious instructions. Inbox Tend will be designed to:

- Treat message content as task data, never as privileged system instructions.
- Check sender identity, task provenance, and whether a request is authorized for the relevant system.
- Require explicit approval by default for sending email, deleting messages, transferring money, changing account settings, deploying code, and executing remote commands.
- Apply least-privilege credentials and isolate agents or tools where practical.
- Avoid forwarding full email threads or secrets to remote agents unless needed and approved.
- Protect stored tokens and credentials; never commit secrets to the repository.
- Maintain an audit trail and distinguish proposals, approvals, attempts, and verified results.

These are **design goals**, not a claim that a security audit has been completed.

## Local-first and AI processing

“Local-first” describes ownership of the application's primary data and workflow state; it does **not** guarantee that all processing stays offline. Gmail requires network access, and a user-enabled cloud model or remote agent may receive selected content. The eventual application should make data flow, consent, and provider configuration visible and controllable. An optional local-model path can be evaluated as the project develops.

## Repository layout (proposed)

```text
inboxtend/
├── README.md
├── LICENSE                 # To be chosen before public release
├── apps/
│   └── web/                # Local web dashboard
├── packages/
│   ├── core/               # Message and workflow models
│   ├── connectors/         # Gmail, then IMAP/providers
│   ├── ai/                 # Summaries and task extraction
│   └── orchestration/      # Permissions, agents, task states
├── skills/
│   └── inboxtend/
│       └── SKILL.md        # Proposed agent integration
├── docs/
│   ├── architecture.md
│   ├── security.md
│   └── roadmap.md
└── examples/
```

## Roadmap

| Stage | Focus | Initial milestone |
| --- | --- | --- |
| 0 — Foundation | Architecture, license, threat model | Document data flows and permission boundaries |
| 1 — Email MVP | Gmail, dashboard, summaries, drafts | Review messages and create a draft locally |
| 2 — Skills | Structured skills and scoped actions | Agent can propose an email-derived task |
| 3 — Remote agents | Registration, delegation, status, evidence | Approved task executes on a remote agent |
| 4 — Automation | Policies, multi-agent workflows, observability | Track a multi-step workflow end to end |

This roadmap is directional; implementation priorities may change as the project evolves.

## Getting started

Inbox Tend now has a **local sample-data demo**, not a production release or real email integration. With Python 3.10+, run `python3 app.py` from the repository root and open `http://127.0.0.1:8765`. The demo presents three fictional messages with deterministic excerpts and unsent reply templates. See [QUICKSTART.md](QUICKSTART.md) for instructions and tests. Gmail OAuth, AI processing, persistent storage, and remote-agent orchestration are not yet implemented.

## Contributing

Contributions and design discussions will be welcome when the public repository and contribution guidelines are published. Early areas of interest include email-provider integration, privacy-preserving AI, skill interface design, and agent authorization patterns.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). Copyright 2026 Scyllarus LLC. See [NOTICE](NOTICE) for project attribution.

## Brand and project links

- **Name:** Inbox Tend
- **Tagline:** Your inbox, thoughtfully handled.
- **Planned website:** https://inboxtend.email
- **GitHub repository:** `scyllarusllc/inboxtend`
- **Maintainer:** Scyllarus LLC

---

*Your inbox. Your tasks. Your control.*
