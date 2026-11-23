#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sta1n Free Model Proxy  v1.0.1
纯 Python 标准库实现的 OpenAI 兼容转发服务（Termux / MT管理器 / 任意 Android 环境可跑，不需要 root）

作用：
  把 /v1/models、/v1/chat/completions 等请求转发到上游 sta1n.ai，
  自动补齐 RP-Hub 源站校验所需的 Origin / Referer / 浏览器 UA / Sec-Fetch-* 请求头，
  并对 429 / 503 做 3-6-9-12 秒退避重试。
  局域网其它设备把 base_url 指到 http://<本机IP>:PORT/v1 即可使用。

环境变量（全部可选）：
  KEY_SOURCE      上游 key 来源，默认 auto
                  client = 只用客户端请求头里的 key（服务端不存 key，推荐）
                  env    = 只用服务端的 KEY
                  auto   = 客户端带就用客户端的，没带回落服务端 KEY
  KEY / STA1N_KEY / RPHUB_KEY   仅当 KEY_SOURCE=env/auto 时作为回落 key
  HOST            监听地址，默认 0.0.0.0（局域网可访问）
  PORT            端口，默认 17392
  PROXY_KEY       本地访问口令；设置后客户端须带 X-Proxy-Key: <PROXY_KEY>
  UPSTREAM_BASE   上游地址，默认 https://sta1n.ai/v1
  FAKE_ORIGIN     默认 https://sta1n156.github.io
  FAKE_REFERER    默认 https://sta1n156.github.io/RP-Hub/
  FAKE_UA         默认现代 Android Chrome UA
  RETRY_DELAYS    退避秒数，默认 2,4,8
  TIMEOUT         单次上游超时秒数，默认 600
  FULL_ERROR      1 = 上游错误体原样透传；默认归一化成 OpenAI error 结构
  INSECURE_TLS    1 = 跳过证书校验（系统 CA 缺失时用）
  ALLOW_ANY_PATH  1 = 不限制路径白名单
"""

from __future__ import annotations

import json
import os
import select
import socket
import ssl
import subprocess
import sys
import traceback
import threading
import time
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

__version__ = "1.0.1"
HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 配置
def env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_flag(name: str) -> bool:
    return env(name, "0").strip().lower() in ("1", "true", "yes", "on")


def load_key() -> str:
    for n in ("KEY", "STA1N_KEY", "RPHUB_KEY", "API_KEY", "STA1N_API_KEY"):
        v = os.environ.get(n, "").strip()
        if v:
            return v
    for fn in ("key.txt", ".env"):
        p = os.path.join(HERE, fn)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if fn == ".env":
                        if "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k.strip() not in ("KEY", "STA1N_KEY", "RPHUB_KEY", "API_KEY", "STA1N_API_KEY"):
                            continue
                    else:
                        return line.strip().strip('"').strip("'")
                    return v.strip().strip('"').strip("'")
        except OSError:
            pass
    return ""


KEY_SOURCE = env("KEY_SOURCE", "auto").strip().lower()
if KEY_SOURCE in ("forward", "passthrough", "client", "proxy", "pass"):
    KEY_SOURCE = "client"
elif KEY_SOURCE in ("server", "file", "key", "env", "fixed"):
    KEY_SOURCE = "env"
elif KEY_SOURCE not in ("client", "env", "auto"):
    KEY_SOURCE = "auto"

UPSTREAM_KEY = load_key()
UPSTREAM_BASE = env("UPSTREAM_BASE", "https://sta1n.ai/v1").rstrip("/")
FAKE_ORIGIN = env("FAKE_ORIGIN", "https://sta1n156.github.io")
FAKE_REFERER = env("FAKE_REFERER", "https://sta1n156.github.io/RP-Hub/")
FAKE_UA = env(
    "FAKE_UA",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Mobile Safari/537.36",
)
LISTEN_HOST = env("HOST", "0.0.0.0")
LISTEN_PORT = env_int("PORT", 17392)
PROXY_KEY = env("PROXY_KEY", "").strip()
TIMEOUT = env_int("TIMEOUT", 600)
FULL_ERROR = env_flag("FULL_ERROR")
INSECURE_TLS = env_flag("INSECURE_TLS")
ALLOW_ANY_PATH = env_flag("ALLOW_ANY_PATH")
MAX_BODY = 32 * 1024 * 1024

RETRY_DELAYS = [2, 4, 8]
_raw_delays = env("RETRY_DELAYS", "")
if _raw_delays.strip():
    try:
        RETRY_DELAYS = [float(x) for x in _raw_delays.replace(" ", "").split(",") if x]
    except ValueError:
        pass

RETRY_STATUS = {429, 500, 502, 503, 504}
NO_RETRY_MARKERS = ("model_not_found", "model not found", "已下架", "does not exist", "not supported")

# 允许透传的接口路径（相对 /v1）
ALLOWED_PATHS = {
    "/models",
    "/chat/completions",
    "/completions",
    "/embeddings",
    "/responses",
}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
        sys.stderr.flush()


# ---------------------------------------------------------------- 上游连接
_UP = urlsplit(UPSTREAM_BASE)
_UP_SCHEME = _UP.scheme or "https"
_UP_HOST = _UP.hostname or "sta1n.ai"
_UP_PORT = _UP.port or (443 if _UP_SCHEME == "https" else 80)
_UP_PATH = (_UP.path or "").rstrip("/")

_SSL_CTX = ssl.create_default_context()
if INSECURE_TLS:
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE


def upstream_headers(up_key: str, extra: dict | None = None) -> dict:
    """构造伪装成 RP-Hub 页面发起的浏览器请求头。客户端自带的无关头一律丢弃。"""
    h = {
        "Authorization": "Bearer " + up_key,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "identity",
        "Origin": FAKE_ORIGIN,
        "Referer": FAKE_REFERER,
        "User-Agent": FAKE_UA,
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
    }
    if extra:
        h.update(extra)
    return h


def upstream_open(method: str, subpath: str, body: bytes | None, up_key: str):
    """发起一次上游请求，返回 (conn, resp)。subpath 形如 /chat/completions。"""
    url_path = _UP_PATH + subpath
    if _UP_SCHEME == "https":
        conn = HTTPSConnection(_UP_HOST, _UP_PORT, timeout=TIMEOUT, context=_SSL_CTX)
    else:
        conn = HTTPConnection(_UP_HOST, _UP_PORT, timeout=TIMEOUT)
    conn.request(method, url_path, body=body, headers=upstream_headers(up_key))
    resp = conn.getresponse()
    return conn, resp


# ---------------------------------------------------------------- HTTP 服务
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "sta1n-proxy/" + __version__
    sys_version = ""

    # ---------------- 工具
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self._sent = True
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            log("客户端已断开，响应未送达")

    def _err(self, code: int, message: str, etype: str = "proxy_error", ecode: str | None = None) -> None:
        item = {"message": message, "type": etype}
        if ecode:
            item["code"] = ecode
        self._json(code, {"error": item})

    def log_message(self, fmt: str, *args) -> None:
        pass  # 自己管日志，屏蔽默认逐行噪音

    # ---------------- 鉴权 / key 选取
    def _auth_ok(self) -> bool:
        """本地口令只认 X-Proxy-Key / X-Local-Key，不占用 Authorization。"""
        if not PROXY_KEY:
            return True
        for name in ("X-Proxy-Key", "X-Local-Key", "X-Access-Key"):
            got = (self.headers.get(name) or "").strip()
            if got:
                return got == PROXY_KEY
        # 兼容只能填一个 API Key 的客户端：Authorization 里放口令也算通过，
        # 此时上游 key 走服务端配置（_client_upstream_key 会把它排除掉）
        raw = (self.headers.get("Authorization") or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        return raw == PROXY_KEY

    def _client_upstream_key(self) -> str:
        """取客户端自带的 key：X-Upstream-Key 优先，其次 Authorization。"""
        raw = (self.headers.get("X-Upstream-Key") or "").strip()
        if not raw:
            raw = (self.headers.get("Authorization") or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if raw and PROXY_KEY and raw == PROXY_KEY:
            return ""  # 这是本地口令，不能当上游 key 发出去
        return raw

    def _pick_upstream_key(self) -> tuple[str, str]:
        """返回 (key, 来源说明)。空 key 表示客户端需要自带。"""
        client_key = self._client_upstream_key()
        if KEY_SOURCE == "client":
            return client_key, "客户端透传"
        if KEY_SOURCE == "env":
            return UPSTREAM_KEY, "服务端 KEY"
        if client_key:
            return client_key, "客户端透传"
        return UPSTREAM_KEY, "服务端 KEY 回落"

    # ---------------- 请求体 / 连接状态
    def _read_body(self) -> bytes:
        """读取请求体，兼容 Content-Length 与 Transfer-Encoding: chunked。"""
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            out = []
            total = 0
            while True:
                line = self.rfile.readline(1024)
                if not line:
                    break
                try:
                    size = int(line.strip().split(b";")[0] or b"0", 16)
                except ValueError:
                    raise ValueError("invalid chunk size")
                if size == 0:
                    self.rfile.readline(1024)
                    break
                total += size
                if total > MAX_BODY:
                    raise ValueError("body too large")
                out.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(out)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        if length > MAX_BODY:
            raise ValueError("body too large")
        return self.rfile.read(length)

    def _client_gone(self) -> bool:
        """客户端是否已断开（非阻塞探测，不消费数据）。"""
        try:
            r, _, _ = select.select([self.connection], [], [], 0)
            if not r:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except Exception:
            return True

    def _sleep_watching(self, seconds: float) -> bool:
        """退避等待；期间客户端断开则提前返回 True。"""
        end = time.time() + seconds
        while True:
            if self._client_gone():
                return True
            left = end - time.time()
            if left <= 0:
                return False
            time.sleep(min(0.25, left))

    def _fail_safe(self) -> None:
        """兜底：把未预期异常变成 500 JSON，而不是直接断开连接。"""
        if getattr(self, "_sent", False):
            return
        try:
            self._err(500, "internal proxy error (see proxy log)", "proxy_error", "internal_error")
        except Exception:
            pass

    # ---------------- 路由
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._sent = False
        try:
            self._do_get()
        except Exception:
            log("unhandled error on GET %s:\n%s" % (self.path, traceback.format_exc()))
            self._fail_safe()

    def _do_get(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/healthz", "/health"):
            self._json(
                200,
                {
                    "status": "ok",
                    "service": "sta1n-free-proxy",
                    "version": __version__,
                    "upstream": UPSTREAM_BASE,
                    "key_source": KEY_SOURCE,
                    "server_key": bool(UPSTREAM_KEY),
                    "local_auth": bool(PROXY_KEY),
                },
            )
            return
        self._forward("GET", path, None)

    def do_POST(self) -> None:
        self._sent = False
        try:
            self._do_post()
        except Exception:
            log("unhandled error on POST %s:\n%s" % (self.path, traceback.format_exc()))
            self._fail_safe()

    def _do_post(self) -> None:
        path = urlsplit(self.path).path
        try:
            body = self._read_body()
        except ValueError as e:
            self._err(413 if "too large" in str(e) else 400,
                      "bad request body: %s" % e, "invalid_request_error")
            return
        if not body:
            self._err(400, "empty request body", "invalid_request_error")
            return
        self._forward("POST", path, body)

    # ---------------- 转发
    def _forward(self, method: str, path: str, body: bytes | None) -> None:
        if not self._auth_ok():
            self._err(401, "invalid local proxy key", "invalid_request_error", "invalid_api_key")
            return
        up_key, key_from = self._pick_upstream_key()
        if not up_key:
            self._err(
                401,
                "缺少上游 key：请在客户端把 API Key 填成你的 sta1n key"
                "（Authorization: Bearer <key> 会原样转发给上游）。",
                "invalid_request_error",
                "missing_upstream_key",
            )
            return

        norm = path[3:] if path.startswith("/v1/") else path
        if not norm.startswith("/"):
            norm = "/" + norm
        if not ALLOW_ANY_PATH and norm not in ALLOWED_PATHS and not norm.startswith("/models/"):
            self._err(
                404,
                "path not proxied: %s (allowed: %s)" % (norm, ", ".join(sorted(ALLOWED_PATHS))),
                "invalid_request_error",
            )
            return

        model = "-"
        if body:
            try:
                model = str(json.loads(body.decode("utf-8")).get("model") or "-")
            except Exception:
                model = "?"

        attempts = len(RETRY_DELAYS) + 1
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                delay = RETRY_DELAYS[min(attempt - 2, len(RETRY_DELAYS) - 1)]
                log("retry %d/%d after %ss [%s]" % (attempt - 1, attempts - 1, delay, model))
                if self._sleep_watching(delay):
                    log("%s %s -> 客户端在退避等待中断开，放弃重试 [%s]" % (method, norm, model))
                    return
            try:
                conn, resp = upstream_open(method, norm, body, up_key)
            except Exception as e:
                # 关键修复：BadStatusLine / IncompleteRead 属于 http.client.HTTPException，
                # 不继承 OSError。只捕 OSError 会漏掉，handler 崩溃后连接被直接关闭，
                # 客户端只会看到 "Connection closed before full header was received"。
                desc = "%s: %s" % (type(e).__name__, e)
                log("%s %s -> 上游异常 %s [%s]" % (method, norm, desc, model))
                if attempt < attempts:
                    if self._client_gone():
                        log("%s %s -> 客户端已断开，停止重试" % (method, norm))
                        return
                    continue
                self._err(502, "upstream error: %s" % desc, "proxy_error", "upstream_error")
                return

            status = resp.status
            if status in RETRY_STATUS:
                # 错误体读取加短超时，避免上游 keep-alive 迟迟不关连接时卡到总超时
                try:
                    if getattr(conn, "sock", None):
                        conn.sock.settimeout(min(15, TIMEOUT))
                except Exception:
                    pass
                try:
                    raw = resp.read()
                except Exception:
                    raw = b""
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
                text = raw.decode("utf-8", "replace")
                low = text.lower()
                if any(m in low for m in NO_RETRY_MARKERS):
                    log("%s %s -> %d (no-retry marker) [%s]" % (method, norm, status, model))
                    self._relay_error(status, raw)
                    return
                if attempt < attempts:
                    log("%s %s -> %d, %s" % (method, norm, status, _short(text)))
                    continue
                log("%s %s -> %d (gave up) [%s]" % (method, norm, status, model))
                self._relay_error(status, raw)
                return

            log("%s %s -> %d [%s] key=%s" % (method, norm, status, model, key_from))
            if status >= 400:
                try:
                    raw = resp.read()
                except Exception:
                    raw = b""
                try:
                    conn.close()
                except Exception:
                    pass
                self._relay_error(status, raw)
                return
            self._relay_stream(conn, resp)
            return

    # ---------------- 回写
    def _relay_error(self, status: int, raw: bytes) -> None:
        """错误体：默认归一化成标准 OpenAI error，避免把上游无关信息/内部细节原样外泄。"""
        if FULL_ERROR:
            self._raw_out(status, raw or b"{}", "application/json; charset=utf-8")
            return
        text = (raw or b"").decode("utf-8", "replace")
        msg, etype, ecode = None, None, None
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                e = obj.get("error")
                if isinstance(e, dict):
                    msg = e.get("message") or e.get("msg")
                    etype = e.get("type")
                    ecode = e.get("code") or e.get("status")
                elif isinstance(e, str):
                    msg = e
                if not msg:
                    msg = obj.get("message") or obj.get("msg") or obj.get("detail")
        except Exception:
            pass
        if not isinstance(msg, str) or not msg.strip():
            msg = _short(text) or "upstream error"
        self._err(status, msg.strip()[:2000], etype if isinstance(etype, str) else "upstream_error",
                  str(ecode) if ecode else None)

    def _raw_out(self, status: int, data: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self._sent = True
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            log("客户端已断开，响应未送达")

    def _relay_stream(self, conn, resp) -> None:
        """成功响应：流式透传原始字节（models 列表 / JSON / SSE 都一样处理）。"""
        ctype = resp.getheader("Content-Type") or "application/json; charset=utf-8"
        use_chunked = self.request_version != "HTTP/1.0"
        if resp.length == 0:
            self._raw_out(resp.status, b"", ctype)
            return
        self.send_response(resp.status)
        self.send_header("Content-Type", ctype)
        ce = resp.getheader("Content-Encoding")
        if ce:
            self.send_header("Content-Encoding", ce)
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        if use_chunked:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self._sent = True
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                if use_chunked:
                    self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()
            if use_chunked:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log("client disconnected mid-stream")
        except Exception as e:
            log("stream aborted: %s: %s" % (type(e).__name__, e))
            # 200 头已经发出去了，只能补一个 SSE 错误事件收尾，
            # 否则客户端只会看到"流突然结束"，误以为是网络抖动。
            try:
                ev = ("data: " + json.dumps(
                    {"error": {"message": "upstream stream aborted: %s" % e,
                               "type": "proxy_error", "code": "stream_aborted"}},
                    ensure_ascii=False) + "\n\n").encode("utf-8")
                if use_chunked:
                    self.wfile.write(b"%x\r\n" % len(ev) + ev + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(ev)
                self.wfile.flush()
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


def _short(text: str, n: int = 160) -> str:
    t = " ".join((text or "").split())
    return t[:n] + ("..." if len(t) > n else "")


def candidate_ips() -> list[str]:
    """列出本机 IPv4，内网地址排前面（VPN/tun 的 198.18.x 排后面）。"""
    ips: list[str] = []
    for cmd in (["ip", "-4", "-o", "addr", "show"],
                ["/system/bin/ip", "-4", "-o", "addr", "show"],
                ["/system/xbin/ip", "-4", "-o", "addr", "show"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except Exception:
            continue
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 4 and f[2] == "inet":
                ip = f[3].split("/")[0]
                if ip and ip not in ips:
                    ips.append(ip)
        if ips:
            break
    if not ips:
        for cmd in (["ifconfig"], ["/system/bin/ifconfig"], ["/system/xbin/ifconfig"]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
            except Exception:
                continue
            for line in out.splitlines():
                if "inet " not in line and "inet addr:" not in line:
                    continue
                for tok in line.replace("addr:", "").split():
                    if tok.count(".") == 3 and tok[0].isdigit() and tok not in ips:
                        ips.append(tok)
            if ips:
                break
    ips = [ip for ip in ips if not ip.startswith(("127.", "0."))]

    def rank(ip: str) -> int:
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith("172."):
            return 2
        if ip.startswith(("198.18.", "198.19.", "169.254.")):
            return 8
        return 5

    ips.sort(key=rank)
    if not ips:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))
            ips = [s.getsockname()[0]]
        except Exception:
            ips = []
        finally:
            s.close()
    return ips[:4]


def lan_ip() -> str:
    ips = candidate_ips()
    return ips[0] if ips else "127.0.0.1"


def _http_probe(port: int, path: str, timeout: float = 10.0):
    """用标准库自打一次，返回 (状态码, 前 120 字节, 耗时)。"""
    import urllib.error
    import urllib.request
    url = "http://127.0.0.1:%d%s" % (port, path)
    headers = {"Content-Length": "0"} if path.startswith("/v1/chat") else {}
    req = urllib.request.Request(url, data=(b"" if headers else None), headers=headers, method=("POST" if headers else "GET"))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:120], time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:120], time.time() - t0
    except Exception as e:
        return 0, ("%s: %s" % (type(e).__name__, e)).encode(), time.time() - t0


def doctor() -> int:
    """环境自检：不启动对外服务，只在本机回环上把整条链路跑一遍。"""
    print("=== sta1n-free-proxy doctor ===")
    print("版本      : %s" % __version__)
    print("Python    : %s" % sys.version.split()[0])
    print("解释器    : %s" % sys.executable)
    me = os.path.abspath(__file__)
    try:
        raw = open(me, "rb").read()
        nul = raw.count(b"\x00")
        state = "OK" if nul == 0 and len(raw) > 1000 else "损坏（重新拉取）"
        print("脚本文件  : %s  %d 字节  NUL=%d  %s" % (me, len(raw), nul, state))
    except OSError as e:
        print("脚本文件  : 读不到 -> %s" % e)
    print("上游      : %s" % UPSTREAM_BASE)
    print("key 来源  : %s%s" % (KEY_SOURCE, "（服务端已配 key）" if UPSTREAM_KEY else "（无服务端 key，需客户端自带）"))
    print("监听计划  : %s:%d" % (LISTEN_HOST, LISTEN_PORT))
    print("重试间隔  : %s" % (",".join(str(x) for x in RETRY_DELAYS)))
    print("")

    # 端口是否被占
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1)
    busy = probe.connect_ex(("127.0.0.1", LISTEN_PORT)) == 0
    probe.close()
    print("端口 %d : %s" % (LISTEN_PORT, "已有服务在监听（可能正在跑）" if busy else "空闲"))

    # 上游连通性
    print("")
    print("--- 上游连通性 ---")
    try:
        c = upstream_open("GET", "/models", None, UPSTREAM_KEY or "sk-doctor-probe")
        code = c[1].status
        try:
            c[1].read()
        except Exception:
            pass
        c[0].close()
        print("  可达      : 是（HTTP %d）%s" % (code, "，key 有效" if code == 200 else "，key 无效或未配（正常）"))
    except Exception as e:
        print("  可达      : 否 -> %s: %s" % (type(e).__name__, e))
        print("  提示      : 检查手机网络，或 INSECURE_TLS=1 重试")

    # 回环全链路
    print("")
    print("--- 本机回环全链路 ---")
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError as e:
        print("  起服务失败: %s" % e)
        return 1
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    try:
        code, body, cost = _http_probe(port, "/healthz")
        print("  /healthz  : %d  %.2fs  %s" % (code, cost, body[:80].decode("utf-8", "replace")))
        code, body, cost = _http_probe(port, "/v1/models", timeout=20)
        print("  /v1/models: %d  %.2fs  %s" % (code, cost, body[:110].decode("utf-8", "replace")))
        print("  结论      : %s" % ("回环链路正常，代理本身没问题" if code in (200, 401, 403, 429, 503) else "回环链路异常，请看上面的状态码"))
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


def main() -> int:
    if KEY_SOURCE == "env" and not UPSTREAM_KEY:
        log("ERROR: KEY_SOURCE=env 但服务端没有 key。export KEY=sk-xxx 或放 key.txt")
        return 2
    try:
        httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    except OSError as e:
        log("ERROR: 无法监听 %s:%s -> %s" % (LISTEN_HOST, LISTEN_PORT, e))
        return 3
    httpd.daemon_threads = True
    log("sta1n-free-proxy v%s 监听 %s:%s" % (__version__, LISTEN_HOST, LISTEN_PORT))
    log("上游: %s  伪装源: %s" % (UPSTREAM_BASE, FAKE_ORIGIN))
    log("本地:   http://127.0.0.1:%d/v1" % LISTEN_PORT)
    ips = candidate_ips()
    if ips:
        log("局域网: http://%s:%d/v1" % (ips[0], LISTEN_PORT))
        for extra in ips[1:]:
            log("        http://%s:%d/v1  (备用)" % (extra, LISTEN_PORT))
    else:
        log("局域网: 未识别到网卡地址，请用 ip addr 自查")
    if KEY_SOURCE == "client":
        log("上游 key: 由客户端透传（服务端不存 key）")
    elif KEY_SOURCE == "env":
        log("上游 key: 使用服务端 KEY")
    else:
        log("上游 key: 客户端优先，未带则回落服务端 KEY%s" % ("" if UPSTREAM_KEY else "（当前未配置回落 key）"))
    log("本地口令保护: %s" % ("开启（X-Proxy-Key）" if PROXY_KEY else "关闭"))
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        log("收到中断，退出")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    if "--doctor" in sys.argv[1:]:
        sys.exit(doctor())
    sys.exit(main())
