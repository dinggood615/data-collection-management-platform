#!/usr/bin/env bash
set -euo pipefail

# One-command native installer for systemd Linux distributions.
REPOSITORY_URL="${1:-https://github.com/REPLACE_ME/tender-collection-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tender-collection-platform}"
SERVICE_USER="tenderplatform"
PUBLIC_PORT="${PORT:-5555}"
BACKEND_PORT=8000
TLS_DIR=/etc/tender-platform/tls

die() { echo "错误：$*" >&2; exit 1; }
[ "${EUID}" -eq 0 ] || die "请使用 sudo 运行"
[ -d /run/systemd/system ] || die "原生安装需要 systemd；容器环境请使用 Docker 安装。"
[[ "$REPOSITORY_URL" != *"REPLACE_ME"* ]] || die "用法：sudo bash install-linux.sh https://github.com/<账号>/tender-collection-platform.git"

install_packages() {
  if command -v apt-get >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git python3 python3-venv python3-pip build-essential openssl curl nginx
  elif command -v dnf >/dev/null; then
    dnf install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
  elif command -v yum >/dev/null; then
    yum install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl nginx
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm ca-certificates git python python-pip base-devel openssl curl nginx
  else
    die "未识别的软件包管理器。支持 apt、dnf、yum、zypper、pacman。"
  fi
}

git_repo() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git -c http.extraHeader="Authorization: Bearer ${GITHUB_TOKEN}" "$@"
  else
    git "$@"
  fi
}

install_packages
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
if [ -d "$INSTALL_DIR/.git" ]; then git_repo -C "$INSTALL_DIR" pull --ff-only; else git_repo clone "$REPOSITORY_URL" "$INSTALL_DIR"; fi
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  sed -i "s|APP_SECRET=.*|APP_SECRET=$(openssl rand -hex 32)|;s|ADMIN_USERNAME=.*|ADMIN_USERNAME=admin|;s|ADMIN_PASSWORD=.*|ADMIN_PASSWORD=admin|;s|DATABASE_PATH=.*|DATABASE_PATH=$INSTALL_DIR/data/platform.sqlite3|;s|SCRAPLING_STORAGE_PATH=.*|SCRAPLING_STORAGE_PATH=$INSTALL_DIR/data/scrapling-selectors.sqlite3|;s|CHROME_CDP_URL=.*|CHROME_CDP_URL=http://127.0.0.1:9222|" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
# Initialize SQLite before the authenticated web endpoint is exposed.  This
# prevents a first-request race from creating an empty database file.
su -s /bin/bash "$SERVICE_USER" -c "set -a; source '$INSTALL_DIR/.env'; set +a; cd '$INSTALL_DIR'; .venv/bin/python -c 'from app.database import init_db; init_db()'"
"$INSTALL_DIR/install-browser.sh" "$INSTALL_DIR" "$SERVICE_USER" || echo "提示：可视 Chrome 未安装；静态采集仍可使用。"

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
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

install -d -m 700 "$TLS_DIR"
if [ ! -f "$TLS_DIR/cert.pem" ] || [ ! -f "$TLS_DIR/key.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 3650 -subj "/CN=$(hostname -f 2>/dev/null || hostname)" -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem"
  chmod 600 "$TLS_DIR/key.pem"
fi
if [ -d /etc/nginx/sites-available ]; then
  NGINX_SITE=/etc/nginx/sites-available/tender-platform
  NGINX_ENABLED=/etc/nginx/sites-enabled/tender-platform
  install -m 644 "$INSTALL_DIR/nginx/tender-platform.conf" "$NGINX_SITE"
  ln -sfn "$NGINX_SITE" "$NGINX_ENABLED"
  rm -f /etc/nginx/sites-enabled/default
else
  NGINX_SITE=/etc/nginx/conf.d/tender-platform.conf
  install -m 644 "$INSTALL_DIR/nginx/tender-platform.conf" "$NGINX_SITE"
fi
sed -i "s/listen 5555 ssl;/listen $PUBLIC_PORT ssl;/" "$NGINX_SITE"
nginx -t
systemctl daemon-reload
systemctl enable --now tender-platform.service
systemctl enable nginx.service
systemctl restart nginx.service
echo "完成：访问 https://服务器IP:$PUBLIC_PORT。初始账户 admin/admin，请立即修改。"
echo "人工验证：在自定义站点卡片点击‘打开此站验证’，无需 SSH 隧道。"
