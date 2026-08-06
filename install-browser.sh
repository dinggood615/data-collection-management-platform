#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${1:-/opt/tender-collection-platform}"
SERVICE_USER="${2:-tenderplatform}"
apt-get update
apt-get install -y xvfb x11vnc novnc websockify wget ca-certificates
if ! command -v google-chrome >/dev/null; then
  package_file="$(mktemp /tmp/google-chrome.XXXXXX.deb)"
  wget -q -O "$package_file" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y "$package_file"
  rm -f "$package_file"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$INSTALL_DIR/browser-profile"
printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'export DISPLAY=:99' 'Xvfb :99 -screen 0 1366x768x24 -nolisten tcp &' 'x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 &' 'websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 &' "exec /usr/bin/google-chrome --no-first-run --no-default-browser-check --disable-gpu --disable-dev-shm-usage --password-store=basic --user-data-dir=$INSTALL_DIR/browser-profile --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --remote-allow-origins=* https://www.szecp.com.cn/first_zbgg/index.html" >"$INSTALL_DIR/manual-browser.sh"
chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/manual-browser.sh"
chmod 750 "$INSTALL_DIR/manual-browser.sh"
printf '%s\n' '[Unit]' 'Description=Persistent Chrome for tender site manual verification' 'After=network-online.target' '' '[Service]' "User=$SERVICE_USER" "Group=$SERVICE_USER" "WorkingDirectory=$INSTALL_DIR" "ExecStart=$INSTALL_DIR/manual-browser.sh" 'Restart=always' '' '[Install]' 'WantedBy=multi-user.target' >/etc/systemd/system/tender-manual-browser.service
systemctl daemon-reload
systemctl enable --now tender-manual-browser.service
