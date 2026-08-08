#!/usr/bin/env bash
set -euo pipefail

# One-command native installer for systemd Linux distributions.
REPOSITORY_URL="${1:-https://github.com/dinggood615/data-collection-management-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/data-collection-management-platform}"
SERVICE_USER="tenderplatform"
PUBLIC_PORT="${PORT:-5555}"
BACKEND_PORT=8000
TLS_DIR=/etc/tender-platform/tls
DOMAIN="${DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"

die() { echo "错误：$*" >&2; exit 1; }
[ "${EUID}" -eq 0 ] || die "请使用 sudo 运行"
[ -d /run/systemd/system ] || die "原生安装需要 systemd；容器环境请使用 Docker 安装。"

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

install_certbot() {
  if command -v certbot >/dev/null; then return; fi
  if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get install -y certbot
  elif command -v dnf >/dev/null; then dnf install -y certbot
  elif command -v yum >/dev/null; then yum install -y certbot
  elif command -v zypper >/dev/null; then zypper --non-interactive install certbot
  elif command -v pacman >/dev/null; then pacman -Sy --noconfirm certbot
  else die "无法安装 Certbot；请手动安装后重新执行。"
  fi
}

valid_domain() {
  [ -z "$DOMAIN" ] || printf '%s' "$DOMAIN" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'
}

open_tls_firewall_ports() {
  # Ubuntu/Debian deployments commonly use UFW.  Opening these ports here
  # prevents a successful DNS update from still failing the ACME HTTP check.
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q 'Status: active'; then
    ufw allow 80/tcp
    ufw allow 443/tcp
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
valid_domain || die "DOMAIN 格式不正确；请只填写域名，例如 tender.example.com。"
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
# Invoke explicitly with bash: Git mirrors may not preserve executable bits.
bash "$INSTALL_DIR/install-browser.sh" "$INSTALL_DIR" "$SERVICE_USER"
systemctl is-active --quiet tender-manual-browser.service || die "可视 Chrome 服务未能启动，请检查 tender-manual-browser.service 日志"

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
# Keep standard HTTPS 443 for Enterprise WeChat.  Avoid duplicate listen
# directives when the dashboard port itself is configured as 443.
if [ "$PUBLIC_PORT" != "443" ]; then
  sed -i "s/listen 5555 ssl;/listen $PUBLIC_PORT ssl;/" "$NGINX_SITE"
else
  sed -i '/listen 5555 ssl;/d' "$NGINX_SITE"
fi
if [ -n "$DOMAIN" ]; then
  sed -i "s/server_name _;/server_name $DOMAIN;/" "$NGINX_SITE"
fi
nginx -t
systemctl daemon-reload
systemctl enable --now tender-platform.service
systemctl enable nginx.service
systemctl restart nginx.service
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  # Check Uvicorn directly.  Nginx may still be reloading its certificate or
  # access-control configuration, so it should not make a successful install
  # look like a failed application start.
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/healthz" >/dev/null; then
    break
  fi
  [ "$attempt" -eq 10 ] && die "平台健康检查失败，请执行：journalctl -u tender-platform -n 80 --no-pager"
  sleep 2
done
if [ -n "$DOMAIN" ]; then
  echo "正在为 $DOMAIN 申请 Let's Encrypt 证书；请确认 DNS 已解析到本服务器且已放行 80、443。"
  open_tls_firewall_ports
  install_certbot
  systemctl stop nginx.service
  CERTBOT_ARGS=(certonly --standalone --non-interactive --agree-tos --keep-until-expiring -d "$DOMAIN")
  if [ -n "$LETSENCRYPT_EMAIL" ]; then CERTBOT_ARGS+=(--email "$LETSENCRYPT_EMAIL"); else CERTBOT_ARGS+=(--register-unsafely-without-email); fi
  if ! certbot "${CERTBOT_ARGS[@]}"; then
    systemctl start nginx.service
    die "证书申请失败。请检查域名解析和 80/443 入站规则后重试。"
  fi
  ln -sfn "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$TLS_DIR/cert.pem"
  ln -sfn "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$TLS_DIR/key.pem"
  systemctl start nginx.service
  systemctl reload nginx.service
  echo "企业微信回调地址：https://$DOMAIN/wecom/callback"
else
  echo "提示：当前使用自签名证书；企业微信聊天助手需要有效域名 HTTPS 证书。"
fi
echo "完成：访问 https://服务器IP:$PUBLIC_PORT。初始账户 admin/admin，请立即修改。"
echo "人工验证：在自定义站点卡片点击‘打开此站验证’，无需 SSH 隧道。"
