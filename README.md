# sta1n Free Model Proxy

> **免责声明**：本项目仅用于技术研究与个人自用，与 sta1n.ai / RP-Hub / New API 及其运营方无任何关联，仓库内不含任何 API key。上游可能将「非官方源站调用」视为违规，使用风险自负。完整条款见文末。

把 sta1n.ai 的公益模型转成一个**局域网可用的 OpenAI 兼容服务**：任何客户端（脚本 / 后端 / ChatBox / OpenWebUI / LobeChat）把 base_url 指过来，就能在 RP-Hub 之外直接调用。
纯 Python 标准库，无 pip 依赖，不需要 root。

```
/v1/models              -> 原样透传上游列表
/v1/chat/completions    -> 转发并自动补 RP-Hub 源站头 + 503/429 退避重试
其它路径                 -> 404（不外泄任何无关接口）
```

## 文件

| 文件 | 作用 |
|---|---|
| `sta1n_proxy.py` | 代理本体（http.server + http.client，标准库） |
| `start.sh` | 启动脚本，自动找 python3 / 读 key / 打印局域网地址 |
| `key.txt` | **可选**：只有当你想让服务端兜底持有一个 key 时才需要 |
| `python_path.txt` | 自动生成：缓存探测到的 Python 路径，下次启动直接读 |

## 启动

Termux：

```sh
pkg install python          # 已装可跳过
cd /sdcard/DSH-GO/sta1n-Free-Proxy
sh start.sh                 # 前台，Ctrl+C 停
sh start.sh --bg            # 后台，日志 proxy.log
sh start.sh --status        # 看状态
sh start.sh --stop          # 停
sh start.sh --detect        # 只探测 Python 环境，不启动
sh start.sh --which         # 看当前用的哪个 Python、SSL 版本
sh start.sh --reset         # 清掉 Python 缓存，下次重新找
```

MT 管理器：进入目录 → 长按 `start.sh` → 执行 / 或用它自带的终端 `sh start.sh`。
`/sdcard` 是 exFAT，改不了可执行位，所以一律用 `sh start.sh` 调用（不要指望 `./start.sh`）。

**默认不需要在手机里存任何 key**：客户端请求头里带的 `Authorization: Bearer <你的 sta1n key>` 会被原样转发给上游。
只有想让服务端替客户端兜底（多个客户端共用一把 key）时，才 `export KEY=sk-xxx` 或写 `key.txt`。

## Python 环境探测（python_path.txt）

`start.sh` 每次启动按这个顺序找 Python，找到就缓存，下次直接用：

1. `PYTHON=/path/to/python3` 环境变量
2. `python_path.txt` 里缓存的路径（顺手校验能否执行、版本是否 ≥3.8）
3. 自动探测：PATH 里的 `python3`/`python`，再扫 Termux、MT管理器、Pydroid 等常见安装位置，最后是 `/data/local/tmp`、`/usr/bin`、`/usr/local/bin` 和本目录自带的便携版

要求 Python ≥ 3.8 且能 `import json, http.client`；缺 `ssl` 会告警（访问 https 上游需要它）。

- 探测成功 → 路径写进 `python_path.txt`，并打印来源（`缓存` / `自动探测` / `PYTHON 环境变量`）
- 缓存失效（Termux 重装、Python 升级）→ 自动重新探测并覆盖，不会卡住
- 全部探测不到 → 打印安装与手动指定方法后退出，不会静默失败
- 目录只读写不进缓存 → 只警告，本次照样能跑
- 想手写也行：`python_path.txt` 支持单行路径、`python=/path/to/python3` 和 `#` 注释

启动后会打印：

```
局域网: http://192.168.x.x:17392/v1
```

## 客户端怎么填

| 项 | 值 |
|---|---|
| Base URL | `http://<手机局域网IP>:17392/v1` |
| API Key | **填你自己的 sta1n key**，会原样转发给上游（服务端不存 key） |
| Model | `[公益]Gemini`（先 `GET /v1/models` 看当前在架的） |

如果开了本地口令 `PROXY_KEY`，客户端还得带 `X-Proxy-Key: <口令>` 头；客户端不支持自定义头时，把口令填进 API Key 框也行（此时上游 key 必须由服务端 `KEY` / `key.txt` 提供）。

curl 示例：

```sh
curl http://192.168.1.236:17392/v1/chat/completions \
  -H 'Authorization: Bearer sk-你的key' \
  -H 'Content-Type: application/json' \
  -d '{"model":"[公益]Gemini","max_tokens":512,
       "messages":[{"role":"user","content":"你好"}]}'
```

## 变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `KEY_SOURCE` | `auto` | `auto` 客户端优先、没带回落服务端；`client` 只认客户端；`env` 只用服务端 |
| `KEY` | 空 | 服务端兜底 key，仅 `auto`/`env` 用；`client` 模式下不读 |
| `HOST` | `0.0.0.0` | `127.0.0.1` 则只本机可访问 |
| `PORT` | `17392` | 高端口，免 root |
| `PROXY_KEY` | 空 | 本地口令，靠 `X-Proxy-Key` 头校验，防局域网旁人白蹭 |
| `RETRY_DELAYS` | `3,6,9,12` | 上游 503/429 退避秒数 |
| `TIMEOUT` | `600` | 单次上游超时 |
| `UPSTREAM_BASE` | `https://sta1n.ai/v1` | 换源用 |
| `FAKE_UA` | Android Chrome 142 | 想换 UA 改这个 |
| `INSECURE_TLS` | 0 | 系统 CA 缺失报 SSL 错时设 1 |
| `FULL_ERROR` | 0 | 1 = 上游错误体原样透传 |
| `ALLOW_ANY_PATH` | 0 | 1 = 放开路径白名单 |

## 行为说明

- **只透传必需内容**：客户端自带的 Cookie / X-Forwarded-For / UA / Origin 一律丢弃重写；上游的 Set-Cookie、Server、X-New-Api-* 等响应头不回给客户端；错误体默认归一化成 `{"error":{message,type,code}}`。
- **重试**：429/500/502/503/504 按 3/6/9/12 秒退避，最多 4 次；命中 `model_not_found` / 已下架 不重试，直接返回。
- **流式**：`stream:true` 的 SSE 逐块透传（chunked），不等整包。
- **key 走向**：`Authorization` / `X-Upstream-Key` 里的 key 就是上游 key，直接透传；本地口令走 `X-Proxy-Key`，两者不混用。日志只记录 key 来源，不打印 key 本身。

## 注意

1. 上游把「脱离官方源站调用」列为封号情形，这套头随时可能失效；key 已经在聊天里明文出现过，先轮换。
2. 别把这套转发放到公网或前端 JS 里，等于公开你的 key。
3. 图片输入走不通（上游返回 `Unable to process input image`），纯文本正常。
4. 推理型模型 `max_tokens` 给 ≥256，否则正文可能为空。

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
