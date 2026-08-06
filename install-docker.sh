#!/usr/bin/env bash
set -euo pipefail

# One-command Docker installer for Ubuntu/Debian.
REPOSITORY_URL="${1:-https://github.com/REPLACE_ME/tender-collection-platform.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tender-collection-platform}"

if [ "${EUID}" -ne 0 ]; then echo "请使用 sudo 运行"; exit 1; fi
if [[ "$REPOSITORY_URL" == *"REPLACE_ME"* ]]; then
  echo "用法：sudo bash install-docker.sh https://github.com/<账号>/tender-collection-platform.git"
  exit 1
fi
apt-get update
apt-get install -y ca-certificates curl git openssl
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
git_repo() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git -c http.extraHeader="Authorization: Bearer ${GITHUB_TOKEN}" "$@"
  else
    git "$@"
  fi
}
if [ -d "$INSTALL_DIR/.git" ]; then git_repo -C "$INSTALL_DIR" pull --ff-only; else git_repo clone "$REPOSITORY_URL" "$INSTALL_DIR"; fi
cd "$INSTALL_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|APP_SECRET=.*|APP_SECRET=$(openssl rand -hex 32)|;s|ADMIN_USERNAME=.*|ADMIN_USERNAME=admin|;s|ADMIN_PASSWORD=.*|ADMIN_PASSWORD=admin|" .env
  chmod 600 .env
fi
docker compose up -d --build
echo "完成。请编辑 $INSTALL_DIR/.env 设置 SMTP，随后访问 http://服务器IP:8000"
