# 部署和跨电脑连接

## 数据宿主

在能本地只读访问数据库的电脑安装 Python 3.12+ 和本程序，执行 `guanlan init guanlan.json`，编辑路径，再执行 `guanlan doctor --config guanlan.json`。相对路径全部以配置文件目录为基准，不依赖当前工作目录或安装盘符。

默认服务只监听 `127.0.0.1:18738`。可在该电脑执行 `guanlan serve --config guanlan.json --open`。CLI 在前台运行，Ctrl+C 停止；长期使用应通过操作系统服务管理器运行同一命令，并让它持有自己的环境变量。关闭浏览器不影响独立运行的服务进程。

## SSH 隧道：少量可信电脑

数据宿主设置 `GUANLAN_ACCESS_TOKEN` 为自行生成的至少 32 字符随机令牌，再启动服务。可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成，保存在自己的凭据管理器中，不提交到 Git。

客户端执行：

```sh
ssh -N -L 18738:127.0.0.1:18738 user@data-host
```

打开 `http://127.0.0.1:18738/home/` 并输入令牌。访问令牌不写入浏览器本地存储，登录得到 HttpOnly、SameSite 会话。服务重启或会话过期需重新连接。

如果需要独立的本机入口，设置客户端同名令牌环境变量，然后执行：

```sh
guanlan connect --url http://127.0.0.1:18738 --port 18739 --open
```

该进程只提供本机界面并转发 API，不读数据库、不创建第二份训练记录。请保留隧道和服务进程。

## HTTPS 反向代理：浏览器直接访问

在数据宿主配置服务器：

```json
{
  "schema_version": "guanlan.config.v1",
  "paths": {"market_root": "data", "state_root": "state"},
  "server": {
    "host": "127.0.0.1",
    "port": 18738,
    "public_origin": "https://guanlan.example.com",
    "token_env": "GUANLAN_ACCESS_TOKEN"
  }
}
```

反向代理将该 HTTPS 域名转发到本机 18738，并保留浏览器访问的 `Host`；请求超时至少 90 秒。`public_origin` 必须与用户实际访问的协议、主机、端口完全一致。自签名或内网 CA 应正确安装信任；客户端不会关闭证书校验。

程序不自动开启防火墙、不获取公网地址或证书。不要把标准库 HTTP 端口直接暴露到公网。绑定非回环地址必须配置随机令牌及 HTTPS 来源。本版面向可信个人工作区；共享令牌的客户端共享训练/题材状态和操作权限。

## 更新提供者

股票行情、ETF 生产、日历归藏和重算继续由已有更新服务负责。便携端配置：

```json
"provider": {
  "enabled": true,
  "kind": "legacy-loopback",
  "url": "http://127.0.0.1:18736"
}
```

`legacy-loopback` 仅接受本机地址，并校验原听风服务身份；用于存量 1.2.0 更新宿主。`guanlan-api` 类型可连接使用同一 API v1 的服务，另设 `token_env` 指定提供者令牌环境变量。请避免提供者指回当前服务形成循环。

任务按原 request_id/job_id 提交和恢复，不新建中央队列。远程客户端、服务端配置和更新提供者必须指向同一套题材/结果数据；程序不复制或同步它们。新建的独立题材目录不能交给另一份数据配置的提供者重算。

## 故障处理

| 现象 | 操作 |
|---|---|
| doctor 显示 missing/incompatible | 检查配置路径、字段和版本；不要让程序创建空源库冒充修复 |
| 401 / 提示重新连接 | 核对令牌是否在服务进程环境中，重新登录 |
| 403 来源错误 | 核对 public_origin、代理 Host、访问 URL，避免混用 localhost 与 127.0.0.1 |
| 更新服务未连接 | 配置 provider；无需因此重建行情或训练库 |
| 提交结果待核对 | 保留原请求编号；读取原任务/训练记录后恢复，勿生成新请求重复提交 |
| 页面缓慢 | 先看服务宿主负载、源库规模及网络；不要共享 SQLite 文件或擅自给原库建索引 |
| 更换程序版本 | 原配置与状态保持原路径；先 doctor，再开服务；不覆盖用户状态 |

本项目验证包含独立进程、虚拟环境和客户端代理连接，未宣称已在第二台物理电脑或公网反向代理实测。
