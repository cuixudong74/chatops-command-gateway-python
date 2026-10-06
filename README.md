# ChatOps 命令网关

外部协作平台通过回调把**命令消息**、**按钮交互**、**消息操作**推送到本服务，
命令触发需要审批的运维动作。服务保证：重复投递/乱序/并发/重启场景下，
每条命令的副作用**恰好执行一次**，全链路可审计。

## 架构

```
平台回调 ──> app/api.py        签名校验 + 时间戳新鲜度 + 请求解析
                │
                v
           app/service.py      编排：去重 → 限流 → 鉴权 → 状态机 → 执行
                │
     ┌──────────┼─────────────┐
     v          v             v
app/storage.py  app/ratelimit  app/executor.py
SQLite 持久化    .py 滑动窗口    副作用执行（幂等约定）
(命令/投递/审计)                app/authz.py 权限
```

## 回调签名校验（需求 1）

平台对每个回调计算：

```
X-Signature = hex(HMAC_SHA256(workspace_secret, f"{X-Timestamp}.{body}"))
```

请求头：`X-Workspace-Id`、`X-Timestamp`（epoch 秒）、`X-Signature`。
校验失败即拒绝，错误码可区分，且失败请求会写入审计（`request_rejected`，
含 delivery_id/command_id 便于定位）：

| 错误码 | 含义 | HTTP |
|---|---|---|
| `invalid_signature` | 签名伪造 / 时间戳畸形 / 未知工作区 | 401 |
| `stale_request` | 时间戳偏移超过 `max_clock_skew_seconds` | 401 |
| `permission_denied` | 越权提交或越权审批 | 403 |
| `rate_limited` | 超过频率限制 | 429 |
| `illegal_transition` | 非法状态迁移 | 409 |

## 状态机（需求 3）

```
received ──> submitted ──> pending_approval ──> approved ──> executing ──> succeeded
                                │                                  └──> failed
                                └──> rejected
```

- 迁移规则唯一定义在 `app/statemachine.py: TRANSITIONS`。
- `rejected` / `succeeded` / `failed` 为终态，任何出边都会被拒绝。
- 越级（如 `submitted → approved`）或回退迁移一律抛 `IllegalTransition`。
- 存储层用 `UPDATE ... WHERE status = 期望值` 的**比较并交换**实现迁移，
  并发下只有一个请求能赢得迁移；落败者收到当前状态而非错误（需求 4）。

## 幂等语义（需求 2）

- **delivery_id**（平台每次投递唯一）：`deliveries` 表原子认领
  （`INSERT OR IGNORE`），同一 delivery 重复到达返回
  `{"duplicate": true, "status": <当前状态>}`，不产生任何副作用，
  也**不消耗限流配额**。
- **command_id**（逻辑命令唯一）：平台换 delivery_id 重发同一命令时，
  命中已有命令记录，同样按重复处理。
- 乱序：审批先于命令到达时返回 `status: "unknown"`，不污染状态；
  平台重试后正常推进。
- 响应中 `duplicate` 字段区分**首次处理**与**重复命中**。

## 审批与执行（需求 3、4、6）

- 提交人需在 `submitters` 白名单，审批人需在 `approvers` 白名单，二者独立。
- 批准后立即进入执行：`approved → executing` 先**持久化**，再调用执行器。
  崩溃恢复时 `recover()` 会对 `approved`/`executing` 状态的命令重新驱动执行，
  因此**执行器必须按 command_id 幂等**（`app/executor.py` 的协议约定）。
- 执行器抛异常 → 命令进入 `failed` 终态并记录错误，不会被盲目重试。
- 存储锁只覆盖短事务，执行器在锁外运行：慢命令不阻塞无关命令。

## 限流（需求 5）

按 `(workspace_id, user_id)` 滑动窗口计数，键相互独立：
单个用户超频不影响其他用户/工作区；重复投递不计入配额。

## 审计（需求 7）

- 每个事件（投递接收、重复命中、命令创建、状态迁移、审批冲突、请求拒绝）
  写入 `audit` 表：`seq`（单调）、时间戳、command_id、delivery_id、
  actor、事件名、明细。
- 数据库触发器禁止对 `audit` 表 UPDATE/DELETE，保证不可篡改。
- 查询：`GET /commands/{id}/audit`、`GET /audit`、`GET /commands/{id}`。

## 配置项（`app/config.py: Settings`）

| 字段 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `workspace_secrets` | `GATEWAY_WORKSPACE_SECRETS`（`ws1:secret1,ws2:secret2`） | `{}` | 每工作区 HMAC 密钥 |
| `max_clock_skew_seconds` | — | 300 | 回调时间戳最大偏移 |
| `submitters` / `approvers` | — | `{}` | 工作区 → 允许提交/审批的用户集合 |
| `rate_limit_max` | — | 20 | 窗口内每（工作区，用户）最大请求数 |
| `rate_limit_window_seconds` | — | 60 | 限流窗口长度（秒） |
| `db_path` | `GATEWAY_DB_PATH` | `gateway.db` | SQLite 路径，`:memory:` 用于测试 |

## 运行与测试

```bash
uv sync
uv run pytest                 # 32 个测试：重复投递/乱序/并发审批/越权/超频/重启恢复
GATEWAY_WORKSPACE_SECRETS=ws1:s3cret uv run python main.py
```

### 回调示例

```bash
BODY='{"delivery_id":"d1","event_type":"command_message","actor_id":"alice",
       "command_id":"c1","action":"deploy","args":{"env":"prod"}}'
TS=$(date +%s)
SIG=$(python3 -c "import hmac,hashlib;print(hmac.new(b's3cret',f'$TS.'.encode()+b'''$BODY''',hashlib.sha256).hexdigest())")
curl -X POST localhost:8000/callbacks -H "X-Workspace-Id: ws1" \
     -H "X-Timestamp: $TS" -H "X-Signature: $SIG" -d "$BODY"
```
