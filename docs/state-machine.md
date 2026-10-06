# 命令状态机

审批型命令在其生命周期内严格按以下状态迁移，任何越级、回退或对终态的重复操作
都会被拒绝（返回 `ignored` 或 `403`）。

## 状态

| 状态 | 含义 |
| --- | --- |
| `SUBMITTED` | （保留）命令已接收，尚未进入审批队列。当前实现接收后直接进入待审批。 |
| `PENDING_APPROVAL` | 已提交，等待审批人操作。 |
| `APPROVED` | 已批准，等待/即将执行。 |
| `REJECTED` | 已拒绝（终态）。 |
| `EXECUTING` | 运维动作执行中。 |
| `SUCCEEDED` | 执行成功（终态）。 |
| `FAILED` | 执行明确失败（终态）。 |

## 合法迁移

```
SUBMITTED        ──▶ PENDING_APPROVAL
PENDING_APPROVAL ──▶ APPROVED
PENDING_APPROVAL ──▶ REJECTED                 (终态)
APPROVED         ──▶ EXECUTING
EXECUTING        ──▶ SUCCEEDED                (终态)
EXECUTING        ──▶ FAILED                   (终态)
```

其余一切迁移（例如 `SUBMITTED → APPROVED`、`APPROVED → SUCCEEDED`、
`REJECTED → PENDING_APPROVAL`、对 `SUCCEEDED` 再次审批）均非法。

## 并发与执行租约（attempt）

* 每次进入 `EXECUTING` 都会把命令的 `attempt` 加 1。
* 只有携带**当前 attempt** 的执行结果才允许把命令推进到终态；旧执行迟到的
  结果会被丢弃，不会覆盖较新的状态。
* 动作**超时**时结果未知，命令从 `EXECUTING` 回退到 `APPROVED` 并重新调度
  （记录 `execution_timeout_retry` 审计事件），而不是乐观地置为 `FAILED`。
  因此动作处理器必须以 `command_id` 为幂等键，允许被重复调用。
* 审批通过后执行器异步调度；对同一命令的并发审批由「进程写锁 + 每命令锁 +
  条件 UPDATE（`WHERE status=预期值`）」三重保证，只有一个请求能完成迁移，
  其余得到 `ignored`。

## 重启恢复时的状态对账

| 崩溃时状态 | 启动恢复动作 |
| --- | --- |
| `PENDING_APPROVAL` | 保持不变，继续等待审批 |
| `APPROVED` | 立即重新调度执行（`recover_resume_execution`） |
| `EXECUTING` | 回退为 `APPROVED`（`recovered_executing`）后重新执行 |
| `REJECTED` / `SUCCEEDED` / `FAILED` | 终态，永不触碰、永不重复触发 |
