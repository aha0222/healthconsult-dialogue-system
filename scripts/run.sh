#!/usr/bin/env bash
# 小暖健康陪护 · 一键运行脚本（macOS / Linux）
# 用法: bash scripts/run.sh [--setup-only] [--no-tools] [--host H] [--port P]
set -euo pipefail

# ── 定位仓库根目录（本脚本位于 scripts/） ────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
SETUP_ONLY=0
INSTALL_TOOLS=1

while [ $# -gt 0 ]; do
  case "$1" in
    --setup-only) SETUP_ONLY=1 ;;
    --no-tools) INSTALL_TOOLS=0 ;;
    --host) HOST="$2"; shift ;;
    --port) PORT="$2"; shift ;;
    -h|--help)
      cat <<'EOF'
用法: bash scripts/run.sh [选项]
  --setup-only   只创建环境 / 装依赖 / 生成 .env，不启动服务
  --no-tools     不安装 tools/requirements.txt
  --host <host>  监听地址（默认 127.0.0.1，仅本机）
  --port <port>  监听端口（默认 8000）
  -h, --help     显示本帮助
EOF
      exit 0 ;;
    *) echo "未知参数：$1（用 -h 查看帮助）"; exit 1 ;;
  esac
  shift
done

# ── Python ───────────────────────────────────────────────────
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "✗ 找不到 $PYTHON，请先安装 Python 3.10+。"
  exit 1
fi

# ── 虚拟环境 ─────────────────────────────────────────────────
if [ ! -d .venv ]; then
  echo "==> 创建虚拟环境 .venv"
  if ! "$PYTHON" -m venv .venv 2>.venv-create.log; then
    echo "✗ 创建虚拟环境失败。"
    if grep -qi "ensurepip" .venv-create.log 2>/dev/null; then
      echo "  Ubuntu/Debian 缺少 venv 支持，请先执行："
      echo "    sudo apt update && sudo apt install -y python3-venv"
    else
      cat .venv-create.log
    fi
    rm -f .venv-create.log
    exit 1
  fi
  rm -f .venv-create.log
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> 安装后端依赖"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r backend/requirements.txt

if [ "$INSTALL_TOOLS" -eq 1 ] && [ -f tools/requirements.txt ]; then
  echo "==> 安装 tools 依赖"
  python -m pip install -r tools/requirements.txt
fi

# ── .env ─────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> 已生成 .env（请填写 DEEPSEEK_API_KEY）"
fi

KEY="$(grep -E '^DEEPSEEK_API_KEY=' .env | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)"
if [ -z "$KEY" ] || [ "$KEY" = "sk-xxx" ]; then
  echo "⚠ .env 中的 DEEPSEEK_API_KEY 还未填写，后端无法调用大模型。"
  echo "  请编辑 .env 后重新运行本脚本。"
fi

if [ "$SETUP_ONLY" -eq 1 ]; then
  echo "✓ 环境准备完成。启动后端："
  echo "    source .venv/bin/activate"
  echo "    uvicorn backend.app.main:app --host $HOST --port $PORT"
  exit 0
fi

echo "==> 启动后端：http://$HOST:$PORT"
echo "    前端：浏览器打开 frontend/chat.html，在设置里确认后端地址"
exec uvicorn backend.app.main:app --host "$HOST" --port "$PORT"
