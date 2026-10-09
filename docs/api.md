# API v1

浏览器和功能客户端使用同一接口。服务 URL 必须是协议、主机和端口，不含密码、路径、查询串。远程 URL 使用 HTTPS；本机回环支持 HTTP，供本地或 SSH 隧道使用。

```python
import os
from guanlan_app.client import Client

client = Client('http://127.0.0.1:18738', os.environ['GUANLAN_ACCESS_TOKEN'])
print(client.health())
print(client.query('etf', view='summary'))
print(client.query('movers', view='dates', limit=30))
```

`GET /api/v1/health` 返回 `application=tingfeng-guanlan`、`api_version=1`、程序版本和能力状态，不暴露本机数据根。`GET /api/v1/calendar` 返回现有日历覆盖。

`POST /api/v1/{module}/invoke` 接收：

```json
{
  "schema_version": "operation_request_v1",
  "module": "etf",
  "operation": "observer.query",
  "params": {"view": "summary"},
  "request_id": "a-stable-unique-request-id"
}
```

响应为 `{"ok": true, "data": ..., "request_id": "..."}`，业务失败为 `ok=false` 和明确错误。请求 JSON 最大 1 MiB。接口不接受任意 SQL、文件路径或命令执行。

| 模块 | 操作族 |
|---|---|
| home | observer.query：view=summary，block=stock / industry / etf / theme |
| etf | observer.query：health / summary / detail / coverage |
| industry30 / theme | observer.query：health / summary / detail / member_signals |
| movers | observer.query：dates / day / stock / leader |
| training | training.list / start / state / act / finish / review / note / replay / abandon / history |
| theme | themes.list / get / preview / save / archive / restore / apply |
| global | updates.list / submit / retry / cancel |

具体允许参数和操作以各功能校验器为准，不能用操作族前缀任意调用。训练请求编号为 8–100 字符；默认客户端生成 UUID。行情和训练规则保持原语义，未知日期、缺因子或来源变化不会填造结果。

需要保存或更新时先生成并保存 request_id。同一操作重试复用该编号与完全相同的参数；改变参数必须新建请求。网络中断、格式不符或回执编号不符抛出 `SubmissionUnknown`，客户端不会自动重试。先查询原记录/任务，再决定恢复。数据库用户状态、请求和任务编号始终由服务宿主管理。

访问令牌用 `Authorization: Bearer ...` 请求头传递，不能放 URL。浏览器使用登录会话和独立 CSRF 令牌；跨来源请求被拒绝。API 当前为一个受信工作区，不提供账户隔离和细分权限。
