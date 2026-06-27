#!/bin/bash
# run-fnOS.sh - 在飞牛 fnOS 上直接运行（不通过 Docker）
# 适用于没有 Docker 或想直接 Python 运行的场景

set -e

echo "=== 飞牛 fnOS Docker Panel (直接运行模式) ==="

# 安装依赖
pip3 install fastapi uvicorn pydantic 2>/dev/null || true

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 启动
# 启动（支持：环境变量 APP_PORT=50090 或 --port 参数覆盖）
PORT="\${APP_PORT:-50087}"
exec python3 -m uvicorn main:app --host 0.0.0.0 --port $PORT
