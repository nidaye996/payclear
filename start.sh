#!/bin/bash
# 启动脚本 - 农民工工资核对系统

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_DIR="$PROJECT_DIR/.venv"

echo "=================================================="
echo "   农民工工资核对系统 - 启动脚本"
echo "=================================================="

# 检查 Python 3.12
if command -v python3.12 &> /dev/null; then
    PYTHON=python3.12
elif command -v python3 &> /dev/null; then
    PYTHON=python3
else
    echo "❌ 未找到 Python3，请先安装 Python 3.12"
    exit 1
fi

echo "✅ Python 版本: $($PYTHON --version)"

# 创建虚拟环境
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 创建虚拟环境..."
    $PYTHON -m venv "$VENV_DIR"
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"

# 安装依赖
echo "📦 安装依赖包..."
pip install -q -r "$PROJECT_DIR/requirements.txt"

echo ""
echo "=================================================="
echo "🚀 启动服务..."
echo "   访问地址: http://localhost:19268"
echo "   默认账号: admin / admin123"
echo "   按 Ctrl+C 停止服务"
echo "=================================================="
echo ""

# 启动服务
cd "$BACKEND_DIR"
python -m uvicorn main:app --host 0.0.0.0 --port 19268 --reload
