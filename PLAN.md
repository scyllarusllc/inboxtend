# Inbox Tend — Open Source First Plan

_Last updated: 2026-11-18 (Saipan, UTC+10). Planning targets, not promises or current achievements._

## Product thesis

Start as a useful **Scyllarus internal tool** and public open-source project. Transform actionable email into **reviewable tasks** with owners, deadlines, status transitions, reminders and audit history. Expand into agent-assisted execution only after permission controls and reliable tracking work.

**Domain decision:** use a Scyllarus subdomain (proposed: `inboxtend.scyllar.us`, subject to DNS setup) during validation. Do not buy `inboxtend.email` solely for appearance or a speculative future product. If the name becomes strategically important, check availability and renewal cost again; it may no longer be available.

## Stage gates (illustrative, not industry standards)

| Stage | Stars (reference only) | What matters more | Decision |
| --- | ---: | --- | --- |
| 1. Internal MVP | 0–100 | Weekly use, successful end-to-end tasks, zero credential exposure | Improve workflow and documentation |
| 2. Community validation | 100–500 | Real installations, useful issues, repeat contributors and evidence of retention | Improve onboarding, integrations and reliability |
| 3. Brand assessment | 500+ | Active recurring users, maintainers, demand for hosted service, sustainable support | Evaluate standalone domain, website, trademark checks and optional SaaS |

Star counts are *signals*, not required milestones; do not purchase a domain automatically when a threshold is hit. Evaluate genuine use and demand alongside domain availability and cost.

## Phase 1 — Local MVP

- [ ] Ingest *fictional / sanitized* email examples (do not publish actual customer email).
- [ ] Create a proposed task with source message ID, project, concise title, description, owner, priority, due date, status and timestamps.
- [ ] Persist tasks locally with SQLite; ensure a message does not create duplicate tasks.
- [ ] List and update tasks: `new` → `in_progress` → `waiting` → `done` (with backward transitions allowed).
- [ ] Display due-soon and overdue tasks; timezone-aware date handling.
- [ ] Prepare editable response drafts, **never send automatically**.
- [ ] Add unit and API tests, basic local authentication before exposing outside localhost, and clear demo-versus-real labels.

**First scenario:** fictional website-project follow-up based on the generic issue of a client requesting an update and disputing when a one-year service period starts. Keep the actual client name, private correspondence and any contract data out of this public repo.

## Phase 2 — Secure real-mail integration

- [ ] Gmail OAuth with minimum scopes and encrypted credentials; read-only first.
- [ ] Sync with paging, deduplication, incremental cursor/history, retries and audit logs.
- [ ] AI-assisted extraction with citations to message text, uncertainty marking and user corrections.
- [ ] Store only necessary email-derived information; support local retention/deletion rules.
- [ ] Explicit user approval before sending, deleting, deploying or executing remote actions.

## Two-week internal validation (start when a real secure build is usable)

Record daily: number of actionable messages, number converted into correct tasks, duplicate tasks, missed deadlines, manual correction rate, time spent processing email, drafts accepted, and unexpected/unauthorized actions. Compare to a baseline collected before deployment.

Proposed acceptance checks (revise after baseline):
- ≥90% of reviewed actionable emails become correct tasks after human confirmation.
- Zero unintended sends, zero exposed credentials, zero duplicate task records for the same message.
- 100% of tracked tasks have an explicit owner or are marked unassigned.
- Measure (do not assume) time saved and missed follow-ups; decide whether the system is genuinely useful to the maintainer.

## Phase 3 — Open-source community

- [ ] Concise README with honest implementation status and local demo.
- [ ] Public roadmap, architecture, threat model, CONTRIBUTING, issue/PR templates.
- [ ] Reproducible tests and one-command local startup.
- [ ] Privacy-safe screenshots and demo data.
- [ ] Solicit feedback without overstating adoption or working features.

## Current engineering state

The main branch currently describes a local sample-data demo, not a production email tool. An experimental branch `feat/email-task-tracking-mvp` contains an initial SQLite task module. **The full end-to-end workflow, Gmail sync, AI extraction and production security have not yet been completed or verified.** No real customer email is added to this file.

## Decision policy

Continue while internal utility improves and security risks remain manageable. Assess branding when meaningful community adoption emerges; compare real users and contributors against infrastructure/support cost. A commercial SaaS launch would need a separate security, privacy, uptime and support review.
