#!/bin/bash
# 服务器一键部署/更新脚本 - 薪核通 PayClear

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_DIR="/etc/payclear"
ENV_FILE="$ENV_DIR/payclear.env"
SERVICE_FILE="/etc/systemd/system/payclear.service"
BACKUP_DIR="/var/backups/payclear"
PORT="${PAYCLEAR_PORT:-19268}"

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 或 sudo 运行：sudo bash deploy.sh"
    exit 1
fi

echo "========================================"
echo "  薪核通 PayClear - 服务器部署/更新"
echo "========================================"

install_system_deps() {
    echo "安装系统依赖..."
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git curl sqlite3 \
        tesseract-ocr tesseract-ocr-chi-sim poppler-utils
}

ensure_env() {
    mkdir -p "$ENV_DIR"
    chmod 700 "$ENV_DIR"

    if [ ! -f "$ENV_FILE" ]; then
        echo "创建生产环境配置..."
        read -r -p "请输入第一个管理员账号（例如 qwe）: " admin_user
        while true; do
            read -r -s -p "请输入第一个管理员密码（至少10位，不要用简单密码）: " admin_pass
            echo
            read -r -s -p "请再次输入管理员密码: " admin_pass_confirm
            echo
            if [ "$admin_pass" = "$admin_pass_confirm" ]; then
                break
            fi
            echo "两次密码不一致，请重试。"
        done

        secret_key="$("$VENV_DIR/bin/python" - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"

        {
            echo "PAYCLEAR_ENV=production"
            echo "SECRET_KEY=$secret_key"
            echo "INITIAL_ADMIN_USERNAME=$admin_user"
            echo "INITIAL_ADMIN_PASSWORD=$admin_pass"
        } > "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        echo "环境配置已写入 $ENV_FILE"
    else
        echo "已存在 $ENV_FILE，继续使用现有生产配置。"
    fi
}

ensure_python_deps() {
    if [ ! -d "$VENV_DIR" ]; then
        echo "创建 Python 虚拟环境..."
        python3 -m venv "$VENV_DIR"
    fi
    echo "安装 Python 依赖..."
    "$VENV_DIR/bin/pip" install -q --upgrade pip
    "$VENV_DIR/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"
}

backup_existing_data() {
    db_path="$BACKEND_DIR/data/salary.db"
    if [ ! -f "$db_path" ]; then
        return
    fi
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
    ts="$(date +%Y%m%d-%H%M%S)"
    backup_path="$BACKUP_DIR/payclear-before-deploy-$ts.tar.gz"
    echo "部署前备份数据到 $backup_path"
    tar -C "$BACKEND_DIR" -czf "$backup_path" data
    chmod 600 "$backup_path"
}

install_service() {
    echo "写入 systemd 服务..."
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=PayClear wage management service
After=network.target

[Service]
Type=simple
WorkingDirectory=$BACKEND_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable payclear >/dev/null
}

restart_and_check() {
    echo "启动/重启 PayClear..."
    systemctl restart payclear
    sleep 3
    if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null; then
        echo "部署完成：PayClear 已正常响应 HTTP 200"
        echo "访问地址：http://服务器IP:$PORT"
        echo "首次登录成功后，建议删除 $ENV_FILE 里的 INITIAL_ADMIN_PASSWORD 并重启服务。"
    else
        echo "服务健康检查失败，请查看：journalctl -u payclear -n 100 --no-pager"
        exit 1
    fi
}

cleanup_initial_password_if_admin_exists() {
    if ! grep -q '^INITIAL_ADMIN_PASSWORD=' "$ENV_FILE"; then
        return
    fi
    if PYTHONPATH="$BACKEND_DIR" "$VENV_DIR/bin/python" - <<'PY'
import os
from sqlalchemy.orm import Session
from database import engine
from models import User

with Session(engine) as db:
    raise SystemExit(0 if db.query(User).filter(User.role == "admin", User.is_active == True).first() else 1)
PY
    then
        echo "检测到管理员已存在，清理环境文件中的初始化密码..."
        sed -i.bak '/^INITIAL_ADMIN_USERNAME=/d;/^INITIAL_ADMIN_PASSWORD=/d' "$ENV_FILE"
        rm -f "$ENV_FILE.bak"
        chmod 600 "$ENV_FILE"
    fi
}

cd "$PROJECT_DIR"
install_system_deps
ensure_python_deps
ensure_env
backup_existing_data
install_service
restart_and_check
cleanup_initial_password_if_admin_exists
systemctl restart payclear
echo "全部完成。"
