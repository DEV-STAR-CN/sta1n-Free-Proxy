#!/usr/bin/env sh
# sta1n Free Model Proxy 启动脚本（Termux / MT管理器 / Android sh 通用，无需 root）
#
# 用法:
#   sh start.sh            前台运行（Ctrl+C 停止）
#   sh start.sh --bg       后台运行，日志写 proxy.log
#   sh start.sh --stop     停止后台实例
#   sh start.sh --status   查看后台实例状态
#   sh start.sh --detect   只做环境探测（找 Python 并写入 python_path.txt）
#   sh start.sh --which    看当前用的是哪个 Python
#   sh start.sh --reset    删掉缓存的 Python 路径，下次重新探测
#
# 可调环境变量（都可省略）:
#   KEY_SOURCE=auto  上游 key 来源：auto(默认) / client(只认客户端) / env(只用服务端 KEY)
#   KEY=sk-xxx       服务端兜底 key，仅在 auto/env 模式下使用（也可放 key.txt 第一行）
#   PORT=17392        端口
#   HOST=0.0.0.0     监听地址（0.0.0.0 = 局域网可访问）
#   PROXY_KEY=abc    本地口令，客户端需带 X-Proxy-Key: abc（不占用 Authorization）
#   RETRY_DELAYS=3,6,9,12   上游 503/429 退避秒数
#   INSECURE_TLS=1   系统证书缺失、报 SSL 错误时打开
#   PYTHON=/path     直接指定 Python，跳过探测

set -u

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) || SELF_DIR=$(pwd)
APP="$SELF_DIR/sta1n_proxy.py"
PIDFILE="$SELF_DIR/proxy.pid"
LOGFILE="$SELF_DIR/proxy.log"
PYFILE="$SELF_DIR/python_path.txt"
MODE=${1:-}

# ---------------------------------------------------------------- Python 探测
# 校验：能执行、版本 >= 3.8、有 json + http.client
py_ok() {
    [ -n "${1:-}" ] || return 1
    "$1" -c 'import sys, json, http.client; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

# 把名字解析成绝对路径；本来就是路径的原样返回
py_resolve() {
    case "${1:-}" in
        "") return 1 ;;
        */*) printf '%s' "$1" ;;
        *) command -v "$1" 2>/dev/null ;;
    esac
}

# 读缓存：单个路径一行，允许 # 注释和 python=/PATH 写法
py_saved() {
    [ -f "$PYFILE" ] || return 1
    line=$(sed 's/\r$//' "$PYFILE" 2>/dev/null \
        | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' \
        | grep -v '^#' \
        | grep -v '^$' \
        | tail -n 1)
    [ -n "${line:-}" ] || return 1
    case "$line" in
        python=*|PYTHON=*) line=${line#*=} ;;
    esac
    [ -n "${line:-}" ] || return 1
    printf '%s' "$line"
}

# 候选清单（Termux / MT管理器 / 便携 Python / 通用 Linux 全覆盖）
py_candidates() {
    [ -n "${PREFIX:-}" ] && printf '%s\n' "$PREFIX/bin/python3" "$PREFIX/bin/python"
    printf '%s\n' \
        python3 python python3.14 python3.13 python3.12 python3.11 \
        python3.10 python3.9 python3.8 \
        /data/data/com.termux/files/usr/bin/python3 \
        /data/data/com.termux/files/usr/bin/python \
        /data/user/0/com.termux/files/usr/bin/python3 \
        /data/data/bin.mt.plus/files/usr/bin/python3 \
        /data/data/bin.mt.plus/files/python/bin/python3 \
        /data/user/0/bin.mt.plus/files/usr/bin/python3 \
        /data/data/ru.iiec.pydroid3/files/bin/python3 \
        /data/user/0/ru.iiec.pydroid3/files/bin/python3 \
        /data/local/tmp/python3 \
        /data/local/tmp/python/bin/python3 \
        /system/bin/python3 \
        /vendor/bin/python3 \
        /bin/python3 \
        /usr/bin/python3 \
        /usr/local/bin/python3 \
        /opt/homebrew/bin/python3 \
        "$SELF_DIR/python3" \
        "$SELF_DIR/python" \
        "$SELF_DIR/bin/python3"
}

# 探测并缓存，结果放进 PY
init_python() {
    PY=""
    SAVED=""
    if [ -n "${PYTHON:-}" ]; then
        P=$(py_resolve "$PYTHON")
        if py_ok "${P:-}"; then
            PY="$P"
            PY_SRC="PYTHON 环境变量"
        else
            echo "· PYTHON=$PYTHON 不可用，改为自动探测" >&2
        fi
    fi

    if [ -z "$PY" ]; then
        SAVED=$(py_saved 2>/dev/null) || SAVED=""
        if [ -n "$SAVED" ] && py_ok "$SAVED"; then
            PY="$SAVED"
            PY_SRC="python_path.txt 缓存"
        elif [ -n "$SAVED" ]; then
            echo "· 缓存的 Python 已失效: $SAVED  → 重新探测" >&2
        fi
    fi

    if [ -z "$PY" ]; then
        OLDIFS=$IFS
        IFS='
'
        for cand in $(py_candidates); do
            [ -n "$cand" ] || continue
            [ -n "$SAVED" ] && [ "$cand" = "$SAVED" ] && continue
            r=$(py_resolve "$cand") || r=""
            [ -n "$r" ] || continue
            if py_ok "$r"; then
                PY="$r"
                PY_SRC="自动探测"
                break
            fi
        done
        IFS=$OLDIFS
    fi

    if [ -z "$PY" ]; then
        echo "✕ 没找到可用的 Python 3.8+" >&2
        echo "" >&2
        echo "  安装或指定其一即可：" >&2
        echo "    Termux:      pkg install python" >&2
        echo "    手动指定:    PYTHON=/path/to/python3 sh start.sh --detect" >&2
        echo "    写进文件:    echo /path/to/python3 > \"$PYFILE\"" >&2
        echo "    便携版:      把 python3 可执行文件放到 $SELF_DIR 并命名为 python3" >&2
        echo "" >&2
        echo "  探测成功的路径会缓存进 python_path.txt，下次直接读取。" >&2
        return 1
    fi

    # 首次探测 / 缓存变化时落盘
    if [ "$PY" != "${SAVED:-}" ]; then
        if printf '%s\n' "$PY" 2>/dev/null > "$PYFILE"; then
            PY_SAVED_MSG="已写入 python_path.txt"
        else
            PY_SAVED_MSG="写不进 python_path.txt（只读目录？），本次仍可使用"
        fi
    else
        PY_SAVED_MSG=""
    fi

    PY_VER=$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)
    echo "✓ Python ${PY_VER:-?}  $PY  [$PY_SRC]" >&2
    [ -n "$PY_SAVED_MSG" ] && echo "  $PY_SAVED_MSG" >&2
    if ! "$PY" -c 'import ssl' >/dev/null 2>&1; then
        echo "  ⚠ 这个 Python 缺 ssl 模块，访问 https 上游会失败；建议换成 Termux 的 python3" >&2
    fi
    return 0
}

# ---------------------------------------------------------------- 不需要 Python 的子命令
case "$MODE" in
    --stop|stop)
        if [ -f "$PIDFILE" ]; then
            P=$(cat "$PIDFILE" 2>/dev/null)
            if [ -n "${P:-}" ] && kill -0 "$P" 2>/dev/null; then
                kill "$P" 2>/dev/null
                sleep 1
                kill -9 "$P" 2>/dev/null
                echo "✓ 已停止 pid=$P"
            else
                echo "· 进程不存在，清理 pidfile"
            fi
            rm -f "$PIDFILE"
        else
            echo "· 没有 pidfile，服务应该没在后台跑"
        fi
        exit 0
        ;;
    --status|status)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
            echo "✓ 运行中 pid=$(cat "$PIDFILE")"
        else
            echo "· 未运行"
        fi
        exit 0
        ;;
    --reset)
        rm -f "$PYFILE"
        echo "✓ 已删除 python_path.txt，下次启动重新探测"
        exit 0
        ;;
esac

# ---------------------------------------------------------------- 初始化 Python
init_python || exit 1

case "$MODE" in
    --detect|detect)
        echo "环境探测完成，Python 可用：$PY"
        echo "缓存文件：$PYFILE"
        exit 0
        ;;
    --which|which)
        echo "当前使用：$PY"
        echo "来源：${PY_SRC:-未知}"
        "$PY" -c 'import sys; print("版本：" + sys.version.replace("\n", " "))'
        "$PY" -c 'import ssl; print("ssl：" + ssl.OPENSSL_VERSION)' 2>/dev/null \
            || echo "ssl：缺失（https 上游不可用）"
        exit 0
        ;;
esac

# ---------------------------------------------------------------- key
# 默认不要求服务端存 key：客户端 Authorization 里的 key 会被原样转发给上游
if [ -z "${KEY:-}" ] && [ -f "$SELF_DIR/key.txt" ]; then
    KEY=$(sed -n '1p' "$SELF_DIR/key.txt" 2>/dev/null | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
fi
case "${KEY:-}" in
    KEY=*|STA1N_KEY=*|RPHUB_KEY=*|API_KEY=*) KEY=${KEY#*=} ;;
esac
KEY=${KEY:-}
export KEY

KEY_SOURCE=${KEY_SOURCE:-auto}
export KEY_SOURCE
case "$KEY_SOURCE" in
    client|env|auto|forward|passthrough|server) ;;
    *)
        echo "· KEY_SOURCE=$KEY_SOURCE 不认识，按 auto 处理" >&2
        KEY_SOURCE=auto
        export KEY_SOURCE
        ;;
esac

if [ "$KEY_SOURCE" = "env" ] && [ -z "$KEY" ]; then
    echo "✕ KEY_SOURCE=env 但没给服务端 key" >&2
    echo "  export KEY=sk-你的key   或在同目录 key.txt 第一行写 key" >&2
    echo "  不想存 key 就别设 KEY_SOURCE（默认 auto：客户端自带 key 即透传）" >&2
    exit 2
fi

HOST=${HOST:-0.0.0.0}
PORT=${PORT:-17392}
export HOST PORT
[ -n "${PROXY_KEY:-}" ] && export PROXY_KEY
[ -n "${RETRY_DELAYS:-}" ] && export RETRY_DELAYS
[ -n "${INSECURE_TLS:-}" ] && export INSECURE_TLS
[ -n "${TIMEOUT:-}" ] && export TIMEOUT
[ -n "${FULL_ERROR:-}" ] && export FULL_ERROR
[ -n "${UPSTREAM_BASE:-}" ] && export UPSTREAM_BASE
[ -n "${FAKE_UA:-}" ] && export FAKE_UA

# ---------------------------------------------------------------- 启动
case "$MODE" in
    --bg|--background|-d)
        nohup "$PY" -u "$APP" >>"$LOGFILE" 2>&1 &
        echo $! > "$PIDFILE"
        sleep 1
        if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "✓ 已在后台启动 pid=$(cat "$PIDFILE")"
            echo "  日志: $LOGFILE"
            sed -n '1,6p' "$LOGFILE"
        else
            echo "✕ 启动失败，日志尾部：" >&2
            tail -n 20 "$LOGFILE" >&2
            exit 3
        fi
        exit 0
        ;;
esac

exec "$PY" -u "$APP"
