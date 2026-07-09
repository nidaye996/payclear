#!/bin/bash
# 本地开发启动脚本 - 薪核通 PayClear

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_DIR="$PROJECT_DIR/.venv"

echo "=================================================="
echo "   薪核通 PayClear - 本地开发启动脚本"
echo "=================================================="

# 安装系统依赖（tesseract OCR，用于识别用工协议PDF）
if ! command -v tesseract &> /dev/null; then
    echo "📦 安装 tesseract OCR..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq tesseract-ocr tesseract-ocr-chi-sim
    elif command -v brew &> /dev/null; then
        brew install tesseract
        # 下载中文语言包
        TESSDATA=$(brew --prefix tesseract)/share/tessdata
        [ ! -f "$TESSDATA/chi_sim.traineddata" ] && \
            curl -sL -o "$TESSDATA/chi_sim.traineddata" \
            https://github.com/tesseract-ocr/tessdata/raw/main/chi_sim.traineddata
    else
        echo "⚠️  无法自动安装 tesseract，请手动安装后重试"
    fi
else
    echo "✅ tesseract 已安装: $(tesseract --version 2>&1 | head -1)"
fi

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
echo "   本地首次启动如没有管理员，可临时设置："
echo "   INITIAL_ADMIN_USERNAME=你的账号 INITIAL_ADMIN_PASSWORD=强密码 bash start.sh"
echo "   按 Ctrl+C 停止服务"
echo "=================================================="
echo ""

# 启动服务
cd "$BACKEND_DIR"
export PAYCLEAR_ENV="${PAYCLEAR_ENV:-development}"
export SECRET_KEY="${SECRET_KEY:-dev-only-secret-change-in-production}"
python -m uvicorn main:app --host 0.0.0.0 --port 19268 --reload
