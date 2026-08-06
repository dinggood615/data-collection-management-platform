#!/usr/bin/env bash
set -euo pipefail

# Native Linux installer. It runs the FastAPI service with systemd.
REPOSITORY_URL="${1:-https://github.com/REPLACE_ME/tender-collection-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tender-collection-platform}"
SERVICE_USER="tenderplatform"

if [ "${EUID}" -ne 0 ]; then echo "请使用 sudo 运行"; exit 1; fi
if [[ "$REPOSITORY_URL" == *"REPLACE_ME"* ]]; then
  echo "用法：sudo bash install-linux.sh https://github.com/<账号>/tender-collection-platform.git"
  exit 1
fi
apt-get update
apt-get install -y ca-certificates git python3 python3-venv python3-pip build-essential openssl
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
if [ -d "$INSTALL_DIR/.git" ]; then git -C "$INSTALL_DIR" pull --ff-only; else git clone "$REPOSITORY_URL" "$INSTALL_DIR"; fi
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  sed -i "s|APP_SECRET=.*|APP_SECRET=$(openssl rand -hex 32)|;s|ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -dc A-Za-z0-9 | head -c 18)|;s|DATABASE_PATH=.*|DATABASE_PATH=$INSTALL_DIR/data/platform.sqlite3|;s|CHROME_CDP_URL=.*|CHROME_CDP_URL=http://127.0.0.1:9222|" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
cat >/etc/systemd/system/tender-platform.service <<EOF
[Unit]
Description=招标采集管理平台
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now tender-platform.service
echo "完成。服务仅监听 127.0.0.1:8000；请使用 Nginx/Caddy 配置 HTTPS 反向代理。"
