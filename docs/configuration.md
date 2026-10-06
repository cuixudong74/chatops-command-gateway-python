# 配置项

配置由 `gateway.config.Settings` 提供，可在代码中直接构造，也可通过环境变量
（统一前缀 `GATEWAY_`）加载，见 `gateway.config.load_settings()`。
复杂结构（`workspace_roles`、`approvers`）使用 JSON 字符串。

| 配置项 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `database_path` | `GATEWAY_DATABASE_PATH` | `gateway.db` | SQLite 数据库文件路径（自动启用 WAL）。 |
| `signing_secret` | `GATEWAY_SIGNING_SECRET` | `dev-secret` | 与平台共享的 HMAC-SHA256 签名密钥。 |
| `signature_tolerance_seconds` | `GATEWAY_SIGNATURE_TOLERANCE_SECONDS` | `300` | 时间戳容差，超出即视为过期/重放并拒绝。 |
| `default_rate_per_minute` | `GATEWAY_DEFAULT_RATE_PER_MINUTE` | `60` | 每个「工作区+用户」每分钟允许的回调数。 |
| `workspace_rate_per_minute` | `GATEWAY_WORKSPACE_RATE_PER_MINUTE` | `600` | 每个工作区每分钟允许的回调总数。 |
| `workspace_roles` | `GATEWAY_WORKSPACE_ROLES` | `{}` | `工作区 -> 角色 -> 允许的命令名列表`，`"*"` 表示允许任意命令。 |
| `approvers` | `GATEWAY_APPROVERS` | `{}` | `工作区 -> 审批人 user_id 列表`。 |
| `require_different_approver` | `GATEWAY_REQUIRE_DIFFERENT_APPROVER` | `true` | 是否禁止提交人审批自己的命令。 |
| `action_timeout_seconds` | `GATEWAY_ACTION_TIMEOUT_SECONDS` | `30` | 单个运维动作的超时时间；超时后回退 `APPROVED` 重试。 |
| `recovery_poll_seconds` | `GATEWAY_RECOVERY_POLL_SECONDS` | `2` | 预留的恢复轮询间隔（启动恢复外的后台对账用）。 |

## 签名约定

```
X-Timestamp: <unix 秒>
X-Signature: sha256=<hex(HMAC_SHA256(secret, "<timestamp>." + raw_body))>
```

服务在解析 JSON 之前先校验时间戳与签名；缺失、格式错误、过期或不匹配一律
`401`，并在 `security_log` 中按返回的 `request_id` 记录失败原因：

* `invalid_signature`（缺失/格式错误/签名不匹配）
* `timestamp_expired`（超出容差窗口，防重放）
* `malformed_payload`（签名通过但报文不是合法回调 JSON）

## 权限与限流拒绝原因

* `403 permission_denied`：角色无权提交该命令，或用户不是审批人/自我审批。
* `403 approval_not_allowed`：交互动作不是 `approve`/`reject`。
* `429 rate_limited`：触发限流，消息中含具体超限的 scope，例如
  `user:w1:alice: 61/60` 或 `workspace:w1: 601/600`。

限流计数是单条原子 upsert，合法并发请求不会被误伤；两级 scope 各自独立计数。
