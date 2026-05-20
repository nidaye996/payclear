#!/bin/bash
# 服务器更新脚本 - 拉取最新代码、安装依赖、重启服务

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$PROJECT_DIR/.venv/bin"

echo "=============================="
echo "  薪核通 PayClear - 更新脚本"
echo "=============================="

# 拉取最新代码
echo "📥 拉取最新代码..."
git pull origin master

# 安装/更新 tesseract（如果没有）
if ! command -v tesseract &> /dev/null; then
    echo "📦 安装 tesseract OCR..."
    apt-get update -qq && apt-get install -y -qq tesseract-ocr tesseract-ocr-chi-sim
else
    echo "✅ tesseract 已安装"
fi

# 安装/更新 Python 依赖
echo "📦 更新 Python 依赖..."
$VENV/pip install -q -r "$PROJECT_DIR/requirements.txt"

# 重启服务
echo "🔄 重启服务..."
pkill -f "uvicorn main:app" 2>/dev/null || true
sleep 1
cd "$PROJECT_DIR/backend"
nohup $VENV/uvicorn main:app --host 0.0.0.0 --port 19268 > "$PROJECT_DIR/server.log" 2>&1 &

sleep 2
if pgrep -f "uvicorn main:app" > /dev/null; then
    echo "✅ 服务启动成功，运行在端口 19268"
else
    echo "❌ 服务启动失败，查看日志: $PROJECT_DIR/server.log"
fi
