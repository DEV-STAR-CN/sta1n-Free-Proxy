# sta1n Free Model Proxy

> **免责声明**：本项目仅用于技术研究与个人自用，与 sta1n.ai / RP-Hub / New API 及其运营方无任何关联，仓库内不含任何 API key。上游可能将「非官方源站调用」视为违规，使用风险自负。完整条款见文末。

把一个 OpenAI 兼容的远端接口，包装成**局域网内任何设备都能用的本地服务**：手机、电脑、NAS 上的脚本、App、后端都能连。

纯 Python 标准库实现，零 pip 依赖，不需要 root。

```
GET  /v1/models           → 上游模型列表原样返回
POST /v1/chat/completions → 转发（含流式 SSE），自动补齐上游要求的请求头 + 退避重试
其它路径                    → 404，不暴露任何无关接口
```

---

## 目录

- [这是什么](#这是什么)
- [特性](#特性)
- [快速开始](#快速开始)
- [客户端接入](#客户端接入)
- [配置项](#配置项)
- [Python 环境探测](#python-环境探测)
- [工作原理](#工作原理)
- [错误排查](#错误排查)
- [已知限制](#已知限制)
- [FAQ](#faq)
- [项目结构](#项目结构)
- [免责声明](#免责声明--disclaimer)

---

## 这是什么

很多第三方站点会校验请求来源，只允许自家页面调用自家 API。本项目在本地起一个转发服务：

```
你的客户端 ──► http://127.0.0.1:17392/v1 ──► 上游 API
              （标准 OpenAI 协议）          （补齐来源头、UA、重试）
```

客户端只需要支持「自定义 Base URL」，不用改任何代码。**key 由客户端自带并原样透传，服务端不保存任何凭据。**

## 特性

- **零依赖**：只用 `http.server` / `http.client` / `ssl`，不需要 pip install
- **免 root**：监听高端口，普通用户即可运行
- **key 透传**：客户端 `Authorization` 里的 key 直接转发上游，服务端不落盘
- **退避重试**：429/500/502/503/504 按 2/4/8 秒重试（共 3 次），`model_not_found` 立即失败不浪费次数
- **重试可中断**：退避等待期间一旦检测到客户端断开就立即放弃，不空转
- **异常兜底**：上游返回不规范响应（`BadStatusLine` 等）也会转成 502 JSON，不会把连接直接掐掉
- **流式支持**：`stream:true` 的 SSE 逐块透传（chunked），不等整包
- **路径白名单**：只转发必要接口，其它一律 404
- **响应头清洗**：上游的 `Set-Cookie`、`Server`、`X-New-Api-*` 等不留痕
- **错误归一化**：统一成 `{"error":{message,type,code}}`
- **自动环境探测**：`start.sh` 自己找 Python 并缓存路径
- **局域网可用**：默认监听 `0.0.0.0`，手机、平板、电脑同网即用

---

## 快速开始

### 1. 环境要求

| 项 | 要求 |
|---|---|
| Python | **3.8 及以上**，必须能 `import ssl` |
| 依赖包 | 无（纯标准库） |
| 系统 | Android（Termux / MT管理器）、Linux、macOS 均可 |
| root | 不需要 |

装 Python（Termux）：

```sh
pkg update && pkg install python
python3 -V          # 确认 >= 3.8
```

### 2. 获取代码

方式一，git：

```sh
git clone https://github.com/DEV-STAR-CN/sta1n-Free-Proxy.git
cd sta1n-Free-Proxy
```

方式二，手机浏览器打开仓库页 → `Code` → `Download ZIP` → 解压到任意目录，例如 `/sdcard/DSH-GO/sta1n-Free-Proxy/`

> `/sdcard` 是 exFAT，改不了可执行位，所以下面一律用 `sh start.sh` 调用，不要用 `./start.sh`

### 3. 启动

```sh
cd /sdcard/DSH-GO/sta1n-Free-Proxy

sh start.sh              # 前台运行，Ctrl+C 停止
sh start.sh --bg         # 后台运行，日志写 proxy.log
sh start.sh --stop       # 停止后台实例
sh start.sh --status     # 查看是否在跑
```

启动成功会打印：

```
✓ Python 3.12.15  /usr/local/bin/python3  [自动探测]
  已写入 python_path.txt
[20:12:37] sta1n-free-proxy v1.0.0 监听 0.0.0.0:17392
[20:12:37] 本地:   http://127.0.0.1:17392/v1
[20:12:37] 局域网: http://192.168.1.236:17392/v1
```

记住那个 `局域网` 地址，其它设备连它。

### 4. 自检

```sh
# 服务活着吗
curl http://127.0.0.1:17392/healthz

# 上游模型列表（换成你自己的 key）
curl http://127.0.0.1:17392/v1/models \
  -H 'Authorization: Bearer 你的key'

# 发一条对话
curl http://127.0.0.1:17392/v1/chat/completions \
  -H 'Authorization: Bearer 你的key' \
  -H 'Content-Type: application/json' \
  -d '{"model":"[公益]Gemini","max_tokens":512,
       "messages":[{"role":"user","content":"你好"}]}'
```

没有 curl 的手机可以用 Termux 的 `pkg install curl`，或者直接用任意客户端 App 测。

---

## 客户端接入

| 配置项 | 填什么 |
|---|---|
| Base URL | `http://<手机局域网IP>:17392/v1` |
| API Key | **你自己的上游 key**（会原样转发，服务端不存） |
| Model | 先 `GET /v1/models` 看列表，名字要完全一致（方括号也算） |

常见客户端对应字段：

- **OpenAI SDK**：`OpenAI(base_url="http://IP:17392/v1", api_key="你的key")`
- **ChatBox / LobeChat / NextChat**：设置里找「自定义 API 地址」，末尾要带 `/v1`
- **OpenWebUI**：`Settings → Connections → OpenAI API Base URL`
- **Cherry Studio**：`设置 → 模型服务 → 添加 → OpenAI 兼容`

如果开了本地口令 `PROXY_KEY`，还得加请求头 `X-Proxy-Key: <口令>`；客户端不支持自定义头时，把口令填进 API Key 框也能过校验（此时上游 key 必须由服务端 `KEY` 提供）。

---

## 配置项

全部通过环境变量，或写在同目录 `key.txt`：

```sh
PORT=18000 PROXY_KEY=abc123 sh start.sh
```

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `17392` | 监听端口，>1024 免 root |
| `HOST` | `0.0.0.0` | 改 `127.0.0.1` 则只有本机能连 |
| `KEY_SOURCE` | `auto` | `auto` 客户端优先、没带回落服务端；`client` 只认客户端；`env` 只用服务端 |
| `KEY` | 空 | 服务端兜底 key，仅 `auto`/`env` 用 |
| `PROXY_KEY` | 空 | 本地口令，靠 `X-Proxy-Key` 头校验 |
| `RETRY_DELAYS` | `2,4,8` | 重试间隔秒数，逗号分隔（默认共 3 次重试） |
| `TIMEOUT` | `600` | 单次上游请求超时 |
| `UPSTREAM_BASE` | `https://sta1n.ai/v1` | 换上游用 |
| `FAKE_ORIGIN` | `https://sta1n156.github.io` | 伪装来源，一般不用改 |
| `FAKE_REFERER` | `https://sta1n156.github.io/RP-Hub/` | 伪装 Referer |
| `FAKE_UA` | Android Chrome 142 | 想换 UA 改这个 |
| `INSECURE_TLS` | `0` | 系统 CA 缺失报 SSL 错时设 `1` |
| `FULL_ERROR` | `0` | `1` = 上游错误体原样透传，不归一化 |
| `ALLOW_ANY_PATH` | `0` | `1` = 放开路径白名单 |

`key.txt` 一行一个 key，支持 `KEY=sk-xxx` 写法，`#` 开头是注释。

---

## Python 环境探测

`start.sh` 每次启动按顺序找 Python，找到就缓存进 `python_path.txt`，下次直接用：

1. `PYTHON=/path/to/python3` 环境变量
2. `python_path.txt` 缓存的路径（会校验能否执行、版本是否 ≥3.8）
3. 自动探测：PATH 里的 `python3` / `python`，再扫 Termux、MT管理器、Pydroid 等常见安装位置，最后是 `/data/local/tmp`、`/usr/bin`、`/usr/local/bin` 和本目录自带的便携版 `./python3`

相关命令：

```sh
sh start.sh --detect    # 只探测环境，不启动
sh start.sh --which     # 看用的哪个 Python、SSL 版本
sh start.sh --reset     # 清掉缓存重新找
```

缓存失效（重装 Termux、升级 Python、路径被删）会自动重新探测并覆盖，不会卡在坏路径上。

---

## 工作原理

```
客户端请求
   │  Authorization: Bearer <key>
   ▼
sta1n_proxy.py
   │  ┌─ 丢弃客户端的 Cookie / UA / X-Forwarded-For / Origin
   │  ├─ 补 Origin / Referer / Android Chrome UA / Sec-Fetch-*
   │  ├─ key 原样带上（或回落服务端）
   │  └─ 仅在白名单路径上转发
   ▼
上游 API
   │  429 / 503 → 3、6、9、12 秒退避重试
   │  model_not_found / 已下架 → 立即返回，不重试
   ▼
客户端
   └─ 状态码 + Content-Type 透传，chunked 流式回写
```

安全边界：

- 客户端的 `Authorization` 永远只作为上游 key，不会用于本地鉴权
- 上游 `Set-Cookie`、`Server`、`X-New-Api-*` 等响应头不回给客户端
- 日志只记录 `key=客户端透传` / `key=服务端 KEY`，**从不打印 key 本身**
- 默认只放行 `/models`、`/chat/completions`、`/completions`、`/embeddings`、`/responses`

---

## 错误排查

### 启动阶段

| 现象 | 原因 | 处理 |
|---|---|---|
| `✕ 没找到可用的 Python 3.8+` | 没装 Python 或不在默认位置 | `pkg install python`；或 `PYTHON=/path/to/python3 sh start.sh`；或把路径写进 `python_path.txt` |
| `⚠ 这个 Python 缺 ssl 模块` | 精简版 Python | 换 Termux 官方 `python` 包，或 `PYTHON=/usr/bin/python3 sh start.sh` |
| `✕ KEY_SOURCE=env 但没给服务端 key` | 选了服务端 key 模式却没配 | `export KEY=sk-xxx`，或改回 `KEY_SOURCE=auto` |
| 启动后立刻退出、日志末尾有 `Traceback` | 看日志最后一行异常 | 最常见是端口占用（见下条），其次是 key 配置冲突 |
| `OSError: [Errno 98] Address already in use` | 端口被别的程序占了 | `PORT=18000 sh start.sh`，或 `sh start.sh --status` 看是不是已经跑着一份 |
| `Permission denied` 写在 `/sdcard` | exFAT 改了权限位 | 只用 `sh start.sh` 调用，别依赖可执行位 |

### 请求阶段（按状态码）

| 状态码 / 提示 | 含义 | 处理 |
|---|---|---|
| `401 缺少上游 key` | 客户端没带 `Authorization` | 在客户端 API Key 里填上游 key |
| `401 invalid local proxy key` | `X-Proxy-Key` 不对 | 检查口令，或把口令填进 API Key 框 |
| `401 无效的令牌` | key 错 / 过期 / 已吊销 | 换 key，先在 `/v1/models` 上验证 |
| `404 path not proxied` | 请求路径不在白名单 | 确认 Base URL 带 `/v1`；确实需要别的接口就设 `ALLOW_ANY_PATH=1` |
| `400 permission_error` | 上游来源校验没过 | 一般不会出现（请求头已补齐）；检查是否被中间代理改写 |
| `400 invalid_request` | 参数或模型名错误 | 核对 `model` 名字、JSON 格式、`max_tokens` |
| `429` | 限流 | 代理会自动退避重试；持续 429 说明配额或频率问题 |
| `503 当前公益模型并发数较高` | 免费池满了 | 代理自动重试 4 次；仍失败就隔一会儿再试 |
| `503 model_not_found` / `该模型已下架` | 模型没了，重试无用 | `GET /v1/models` 看现存的，换模型 |
| `502 upstream unreachable` | 连不上上游（DNS / 网络 / 代理） | 手机能不能直接打开上游站点；必要时配系统代理 |
| `SSLCertVerificationError` | 系统根证书缺失 | `INSECURE_TLS=1 sh start.sh`（临时），或装 `ca-certificates` |

### 局域网连不上

1. 手机和客户端**必须在同一个 WiFi**（访客网络 / AP 隔离会挡）
2. 用启动日志里那个 `局域网:` 地址，别用 `127.0.0.1`
3. 确认没改过 `HOST=127.0.0.1`
4. 手机上浏览器开 `http://127.0.0.1:17392/healthz` 能通，才说明服务本身没问题
5. 部分国产 ROM 有「后台省电限制」，会把后台进程杀掉——把 Termux 加到电池白名单，或用 `--bg` 后锁屏前确认进程还在（`sh start.sh --status`）

### 客户端报 `Connection closed before full header was received`

这条是**客户端**抛的（典型于 Dart/Flutter 系 App），含义是「还没收到响应头连接就断了」。常见两种来源：

1. **上游给了不规范响应**（状态行乱码、半截响应、连接被中间设备掐断）——v1.0.1 之前这种异常没被捕获，handler 直接崩溃导致断连。现已转成 `502` + `{"error":{"message":"upstream error: ..."}}`
2. **客户端自己超时/主动断开**——比如 App 设了 20 秒超时，而代理正在退避重试。v1.0.1 起退避期间会探测断开并提前停止，日志会写 `客户端在退避等待中断开，放弃重试`

排查顺序：先看 `proxy.log` 最后几行有没有 `上游异常 xxx` 或 `未捕获异常` 的 traceback；再把 `RETRY_DELAYS` 调短（如 `1,2`）试试能否绕过客户端超时。

### 流式没反应 / 一次性吐完

- 客户端要自己开 `stream: true`，服务端只是透传
- 中间有反向代理（nginx 等）时可能被缓冲，需要关掉 `proxy_buffering`
- 某些客户端把 SSE 当普通 JSON 解析，看着像"卡住"，其实是客户端不支持

---

## 已知限制

- **不支持图片输入**：上游网关收不了图，会返回 `Unable to process input image`，纯文本正常
- **推理型模型**：`max_tokens` 建议 ≥256，太低时正文可能为空、思考内容跑到 `reasoning_content`
- **模型随时变动**：调用前先查 `GET /v1/models`，别把模型名写死在业务里
- **响应里的 `model` / `provider_model` 会暴露真实上游模型名**，对外展示时注意
- **上游随时可能改校验**（加签名、改服务端会话），届时本项目会直接失效
- **单机无连接复用**：每次请求新建上游 TLS 连接，首字延迟多几百毫秒

---

## FAQ

**Q：会不会偷我的 key？**
A：代码全部公开，服务端只在内存里转发，不写文件、不写日志。可以自己验：

```sh
grep -n "open(" sta1n_proxy.py     # 只有 load_key 读 key.txt/.env 那一处
grep -n "print\|log(" sta1n_proxy.py | grep -i key   # 日志里没有 key 变量
ls -la                              # 跑完不会多出任何文件
```

**Q：能不能放到公网 VPS 上？**
A：能跑，但**别裸奔**。至少设 `PROXY_KEY`，前面套 HTTPS 反向代理。否则等于把你的 key 池公开给别人用。

**Q：多个设备能不能共用一把 key？**
A：可以。`KEY_SOURCE=env` + `KEY=sk-xxx`，客户端 key 随便填；想限制别人用就再设 `PROXY_KEY`。

**Q：为什么端口是 17392？**
A：高端口免 root，且不容易和其它服务撞。

**Q：支持 `/v1/embeddings`、`/v1/responses` 吗？**
A：路径白名单里有，能转发；但上游是否实现取决于它自己，返回 404 就是上游没有。

**Q：日志里为什么看不到 key？**
A：设计如此，只打印 `key=客户端透传` 这类来源标记。

**Q：改了代码怎么生效？**
A：`sh start.sh --stop` 再 `sh start.sh --bg`。前台模式直接 Ctrl+C 重启。

---

## 项目结构

```
sta1n-Free-Proxy/
├── sta1n_proxy.py     # 代理本体，纯标准库
├── start.sh           # 启动脚本：探测 Python / 读 key / 打印局域网地址
├── README.md          # 本文档
├── .gitignore         # 排除 key.txt / .env / 日志 / python_path.txt 等
├── key.txt            # 可选，服务端兜底 key（不提交）
└── python_path.txt    # 自动生成，Python 路径缓存（不提交）
```

---

## 免责声明 / Disclaimer

本项目是一个本地技术验证工具，仅用于个人学习、技术研究与自用场景。

1. **无关联**：与 sta1n.ai、RP-Hub、New API、one-api 及上述服务的任何运营方、开发者、关联方均无关系，未获其授权、认可或背书。
2. **不含凭据**：仓库不包含任何 API key、账号、令牌或密钥。使用者须自行准备，并自行承担凭据保管、轮换与合规责任。
3. **第三方条款**：上游服务可能将「非官方源站调用」列为违规乃至封禁情形。是否使用、如何使用，由使用者自行判断并自行承担后果（包括但不限于账号受限、服务中断、数据丢失）。
4. **无担保**：代码按「现状」（AS IS）提供，不提供任何明示或暗示担保，不保证可用性、稳定性、安全性或适用性。上游接口、模型列表、鉴权方式随时可能变更并导致本项目失效。
5. **合规义务**：使用者应自行确保其行为符合所在地法律法规及上游服务条款。
6. **禁止商用分发**：请勿将本项目用于对外提供付费代理、转售或任何形式的商业分发。

若不同意上述任一条款，请停止使用并删除本项目副本。

*Provided for technical research and personal use only. Not affiliated with, endorsed by, or sponsored by sta1n.ai, RP-Hub, New API or their operators. No API keys included. Use entirely at your own risk.*
