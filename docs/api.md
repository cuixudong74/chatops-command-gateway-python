# HTTP 接口

| 方法 路径 | 说明 |
| --- | --- |
| `POST /callback` | 平台回调入口（需签名头），见下。 |
| `GET  /commands/{command_id}` | 查询命令当前状态、版本、attempt 与完整审计链。 |
| `GET  /audit?limit=100` | 最近的审计事件（倒序）。 |
| `GET  /security-log?limit=100` | 最近被拒绝的非法请求与原因。 |
| `GET  /healthz` | 存活检查。 |

## 回调报文

```json
{
  "event_id": "平台唯一事件ID",
  "type": "command | button | message_action",
  "workspace_id": "w1",
  "user_id": "alice",
  "role": "admin",
  "timestamp": 1700000000,
  "command_id": "稳定命令ID",
  "command_name": "restart_service",
  "args": {"host": "h1"},
  "action": "approve",
  "text": null
}
```

* `type=command`：需 `command_id` / `command_name`，提交命令。
* `type=button|message_action`：需 `command_id` 与 `action`（`approve`/`reject`）。

## 典型响应

首次提交：

```json
{"status": "accepted", "command_id": "c1",
 "command_status": "PENDING_APPROVAL", "buffered_interactions": 0}
```

重复投递（与首次明确区分）：

```json
{"status": "duplicate", "command_id": "c1",
 "message": "callback already processed; side effect not repeated"}
```

审批：`{"status":"ok","command_status":"APPROVED"}`；
对已离开待审批状态的命令操作：`{"status":"ignored",...}`；
早到交互：`{"status":"buffered",...}`；
同一 `command_id` 以新 `event_id` 再次建单：`{"status":"already_exists",...}`
（返回当前真实状态，不重建、不重复执行）。

## 审计事件链（示例）

`command_submitted` → `command_approved` → `execution_started`
→ `execution_succeeded`（失败为 `execution_failed`，拒绝为 `command_rejected`）。
另外还包括 `duplicate_callback`、`approve_ignored`、`reject_ignored`、
`command_denied`、`command_rate_limited`、`interaction_buffered`、
`recovered_executing`、`recover_resume_execution`、`execution_timeout_retry`。
每条记录含毫秒时间戳、操作者、结果、关联的 `event_id`/`request_id` 与详情，
且审计接口只追加、不提供修改/删除。
