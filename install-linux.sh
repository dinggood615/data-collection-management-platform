#!/usr/bin/env bash
set -euo pipefail

# Native installer for systemd-based Debian/Ubuntu, RHEL/Rocky/Alma/Fedora,
# openSUSE and Arch Linux. It intentionally does not try to support containers.
REPOSITORY_URL="${1:-https://github.com/REPLACE_ME/tender-collection-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tender-collection-platform}"
SERVICE_USER="tenderplatform"
PORT="${PORT:-8000}"

die() { echo "错误：$*" >&2; exit 1; }
[ "${EUID}" -eq 0 ] || die "请使用 sudo 运行"
[ -d /run/systemd/system ] || die "此原生安装需要 systemd。容器、Alpine 等环境请使用 Docker 安装方式。"
[[ "$REPOSITORY_URL" != *"REPLACE_ME"* ]] || die "用法：sudo bash install-linux.sh https://github.com/<账号>/tender-collection-platform.git"

install_packages() {
  if command -v apt-get >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git python3 python3-venv python3-pip build-essential openssl curl
  elif command -v dnf >/dev/null; then
    dnf install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl
  elif command -v yum >/dev/null; then
    yum install -y ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install ca-certificates git python3 python3-pip gcc gcc-c++ make openssl curl
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm ca-certificates git python python-pip base-devel openssl curl
  else
    die "未识别的软件包管理器。支持 apt、dnf、yum、zypper、pacman。"
  fi
}

install_packages
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
if [ -d "$INSTALL_DIR/.git" ]; then git -C "$INSTALL_DIR" pull --ff-only; else git clone "$REPOSITORY_URL" "$INSTALL_DIR"; fi
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
"$INSTALL_DIR/install-browser.sh" "$INSTALL_DIR" "$SERVICE_USER" || echo "提示：可视 Chrome 未安装；仍可使用静态站点采集。"
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
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now tender-platform.service
echo "完成：服务监听 127.0.0.1:$PORT。请通过 Nginx 或 Caddy 配置 HTTPS 与公网访问。"
