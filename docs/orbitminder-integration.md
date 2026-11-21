# OrbitMinder task integration / OrbitMinder 任务集成

**Status: source implemented, not live-verified. / 状态：接口源代码已实现，尚未完成真实环境联调。**

Inboxtend exposes `GET /integration/v1/tasks?limit=50&cursor=...` in `app.py` using `integration_feed.py`. It is disabled until the operator configures a dedicated token of at least 32 characters and a valid existing numeric Inboxtend user ID. The configured user is selected on the server; callers cannot choose another account with query parameters. Run behind HTTPS and restrict service access to OrbitMinder. Do not commit secrets.

Inboxtend 在 `app.py` 注册只读任务接口，由 `integration_feed.py` 处理。需管理员配置至少 32 字符的专用令牌和真实用户 ID；用户范围在服务端固定，调用方不能通过 URL 选择其他用户。需通过 HTTPS 访问，并尽量限制到 OrbitMinder 服务。不要提交密钥。

Private environment variables / 私有环境变量：

```text
INBOX_TEND_INTEGRATION_TOKEN=<random 32+ character private token>
INBOX_TEND_INTEGRATION_USER_ID=<existing authorized user ID>
```

Run existing Inboxtend normally after updating and configuring these values; no new Python dependency or Docker is required. For a trusted operator on the private network, the read-only endpoint takes `Authorization: Bearer <INBOX_TEND_INTEGRATION_TOKEN>`. Valid responses contain task summaries with `source=inboxtend`, `schema_version=1`, `snapshot_at`, `items`, `next_cursor`, and `has_more`. No bodies, descriptions, attachments, message IDs, passwords, or OAuth tokens are intentionally returned. The opaque cursor is signed and scoped to the configured user.

**Integration wiring / 对接配置：** On OrbitMinder, set `INBOXTEND_READONLY_BASE_URL` to Inboxtend's HTTPS origin and `INBOXTEND_READONLY_TOKEN` to the matching integration token. The separate `ORBITMINDER_API_TOKEN` is for OrbitMinder's own trusted caller, **not** for Inboxtend. The OrbitMinder gateway route `/api/v1/inboxtend/tasks` forwards read-only requests and validates the schema.

**Limitations / 尚未完成：** This is an ID-bounded export, not a transactional repeatable-read snapshot. Changes to a task while paging can affect returned values; downstream must reconcile revisions on subsequent syncs. No production credentials have been entered; no live network call, deployment, or authenticated end-to-end response is confirmed. Review the exact deployed auth/TLS setup before enabling outside the host or private network. The existing Inboxtend task execution and mail flows remain unchanged.
