# ChatOps Command Gateway（聊天运维命令网关）

外部协作平台通过回调把**命令消息、按钮交互、消息操作**推送到本服务；审批型
命令会触发需要审批的运维动作。面对平台的重复投递、乱序到达以及服务任意时刻
重启，系统保证命令从接收到执行端到端**可控、可追溯、副作用仅一次**。

## 能力对照

1. **来源与签名校验** — HMAC-SHA256 + 时间戳容差（防重放），失败按
   `request_id` 写入安全日志并返回可区分原因，非法请求不影响合法请求。
2. **投递幂等** — `event_id` 原子去重，首次处理与重复命中响应明确区分；
   命令副作用只发生一次。
3. **审批状态机** — `PENDING_APPROVAL → APPROVED/REJECTED → EXECUTING →
   SUCCEEDED/FAILED`，非法/越级迁移一律拒绝。
4. **并发一致** — 进程写锁 + 每命令锁 + 条件 UPDATE + 执行 attempt 租约；
   多人同时审批只生效一次；动作在线程池异步执行，慢操作不阻塞其他命令。
5. **权限与限流** — 工作区/角色的命令权限、审批人白名单（可禁止自我审批）；
   用户级与工作区级两级固定窗口限流，原子计数不误伤并发；拒绝原因可区分。
6. **崩溃恢复** — 启动对账：`EXECUTING→APPROVED` 回退重试，`APPROVED`
   继续执行，待审批保持，终态绝不重复触发；超时不乐观判失败。
7. **全链路审计** — 每一步的毫秒时间、操作者、结果、关联 ID 都可按命令查询，
   审计只追加，不提供篡改接口；非法请求另有安全日志。
8. **自动化测试与文档** — 覆盖重复投递、乱序、并发审批、越权、超频、重启
   恢复；文档见 `docs/`。

## 快速开始

```bash
uv sync
uv run uvicorn main:app --host 127.0.0.1 --port 8080
```

发起一个带签名的回调（Python 示例）：

```python
import json, time, hmac, hashlib, requests

secret = "dev-secret"
ts = str(int(time.time()))
body = json.dumps({
    "event_id": "evt-1", "type": "command",
    "workspace_id": "w1", "user_id": "alice", "role": "admin",
    "timestamp": int(ts), "command_id": "cmd-1",
    "command_name": "restart_service", "args": {"host": "h1"},
}).encode()
sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
requests.post("http://127.0.0.1:8080/callback", data=body,
              headers={"X-Timestamp": ts, "X-Signature": "sha256=" + sig})
```

注册自定义运维动作（必须以 `command_id` 幂等）：

```python
from gateway.actions import register_action, ActionResult

def restart(command_name, args, command_id):
    # 以 command_id 为下游幂等键，重复调用安全
    return ActionResult(success=True, output={"host": args["host"]})

register_action("restart_service", restart)
```

## 测试

```bash
uv run pytest
```

覆盖场景：签名/过期/重放（`tests/test_signature.py`）、重复投递
（`test_idempotency.py`）、状态迁移（`test_states.py`）、并发审批与慢操作
（`test_concurrency.py`）、越权（`test_permissions.py`）、超频
（`test_ratelimit.py`）、乱序（`test_ordering.py`）、重启恢复
（`test_recovery.py`）、审计（`test_audit.py`）。

## 文档

* [状态迁移规则](docs/state-machine.md)
* [幂等与投递语义](docs/idempotency.md)
* [配置项](docs/configuration.md)
* [HTTP 接口与审计事件](docs/api.md)

## 模块结构

```
gateway/
  config.py       配置（环境变量）
  errors.py       可区分错误码的异常体系
  models.py       状态枚举、回调报文模型
  security.py     时间戳 + HMAC 签名校验
  permissions.py  工作区/角色权限与审批人策略
  ratelimit.py    原子固定窗口两级限流
  states.py       状态机迁移规则
  db.py           SQLite(WAL) 建表与访问
  idempotency.py  事件去重与乱序缓冲
  actions.py      幂等运维动作注册表
  recovery.py     启动崩溃对账
  audit.py        只追加审计 + 安全日志
  service.py      生命周期编排（事务、状态机、异步执行、attempt 租约）
  app.py          FastAPI HTTP 层
```
