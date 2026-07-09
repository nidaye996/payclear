#!/bin/bash
# 服务器更新入口：拉取代码后交给 deploy.sh 做备份、依赖安装、systemd 重启和健康检查。

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 或 sudo 运行：sudo bash update.sh"
    exit 1
fi

cd "$PROJECT_DIR"
echo "拉取最新代码..."
git pull origin master

exec bash "$PROJECT_DIR/deploy.sh"
