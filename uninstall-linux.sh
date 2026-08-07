#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/tender-collection-platform}"
AUTO_CONFIRM="${1:-}"

if [ "${EUID}" -ne 0 ]; then
  echo "请使用 sudo 运行。" >&2
  exit 1
fi

if [ "$AUTO_CONFIRM" != "--yes" ]; then
  read -r -p "将删除数据采集平台及其本地数据。输入 DELETE 确认: " confirm
  [ "$confirm" = "DELETE" ] || { echo "已取消。"; exit 0; }
fi

if [ -f "$INSTALL_DIR/docker-compose.yml" ] && command -v docker >/dev/null 2>&1; then
  docker compose -f "$INSTALL_DIR/docker-compose.yml" down -v --remove-orphans 2>/dev/null || true
fi

systemctl disable --now tender-platform.service tender-manual-browser.service 2>/dev/null || true
rm -f /etc/systemd/system/tender-platform.service /etc/systemd/system/tender-manual-browser.service
rm -f /etc/nginx/sites-enabled/tender-platform /etc/nginx/sites-available/tender-platform /etc/nginx/conf.d/tender-platform.conf
rm -rf /etc/tender-platform "$INSTALL_DIR"
systemctl daemon-reload
systemctl reset-failed

if command -v nginx >/dev/null 2>&1; then
  nginx -t && systemctl reload nginx || true
fi
echo "数据采集平台已卸载。Nginx、Docker 和其他系统服务未删除。"
