#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/data-collection-management-platform}"
REPOSITORY_URL="${REPOSITORY_URL:-https://github.com/dinggood615/data-collection-management-platform.git}"

if [ "${EUID}" -ne 0 ]; then echo "请使用 sudo 运行"; exit 1; fi
echo "1) 原生 Linux 安装  2) Docker 安装  3) 卸载"
read -r -p "请选择 [1-3]: " choice
case "$choice" in
  1) bash install-linux.sh "$REPOSITORY_URL" ;;
  2) bash install-docker.sh "$REPOSITORY_URL" ;;
  3)
    read -r -p "确认删除 $INSTALL_DIR 及其采集数据？输入 DELETE 确认: " confirm
    [ "$confirm" = "DELETE" ] || { echo "已取消"; exit 0; }
    systemctl disable --now tender-platform.service tender-manual-browser.service 2>/dev/null || true
    rm -f /etc/systemd/system/tender-platform.service /etc/systemd/system/tender-manual-browser.service
    rm -rf "$INSTALL_DIR"
    systemctl daemon-reload
    echo "已卸载平台与本地数据。"
    ;;
  *) echo "无效选择"; exit 1 ;;
esac
