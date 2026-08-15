#!/usr/bin/env bash
set -euo pipefail

[ "${EUID}" -eq 0 ] || { echo "请使用 sudo 运行本地模型安装。" >&2; exit 1; }

INSTALL_DIR="${INSTALL_DIR:-/opt/data-collection-management-platform}"
MODEL_DIR="${LOCAL_MODEL_DIR:-/opt/tender-local-model}"
SERVICE_USER="${SERVICE_USER:-tenderplatform}"
MODEL_FILE="$MODEL_DIR/models/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf"
MODEL_URL="${LOCAL_MODEL_URL:-https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf?download=true}"

case "$(uname -m)" in
  x86_64|amd64) ASSET_PATTERN='bin-ubuntu-x64.tar.gz$' ;;
  *) echo "当前一键安装仅支持 x86_64/amd64；其他架构请自行编译 llama.cpp。" >&2; exit 1 ;;
esac

command -v python3 >/dev/null || { echo "缺少 Python 3。" >&2; exit 1; }
command -v curl >/dev/null || { echo "缺少 curl。" >&2; exit 1; }

install -d -m 755 "$MODEL_DIR/bin" "$MODEL_DIR/models"
temporary="$(mktemp -d /tmp/tender-local-model.XXXXXX)"
cleanup() { rm -rf "$temporary"; }
trap cleanup EXIT

if [ ! -x "$MODEL_DIR/bin/llama-cli" ]; then
  echo "正在安装低资源 llama.cpp CPU 运行器……"
  release_url="$(python3 - "$ASSET_PATTERN" <<'PY'
import json, re, sys, urllib.request
request = urllib.request.Request(
    "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest",
    headers={"Accept": "application/vnd.github+json", "User-Agent": "tender-platform-installer"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    release = json.load(response)
pattern = re.compile(sys.argv[1])
for asset in release.get("assets", []):
    if pattern.search(asset.get("name", "")):
        print(asset["browser_download_url"])
        break
else:
    raise SystemExit("未找到适合当前系统的 llama.cpp 官方发行包")
PY
)"
  curl -fL --retry 3 --connect-timeout 20 "$release_url" -o "$temporary/llama.tar.gz"
  tar -xzf "$temporary/llama.tar.gz" -C "$temporary"
  binary="$(find "$temporary" -type f -name llama-cli -perm -u+x | head -1)"
  [ -n "$binary" ] || { echo "llama.cpp 发行包中未找到 llama-cli。" >&2; exit 1; }
  install -m 755 "$binary" "$MODEL_DIR/bin/llama-cli"
  find "$(dirname "$binary")" -maxdepth 1 -type f -name '*.so*' -exec install -m 755 {} "$MODEL_DIR/bin/" \;
fi

if [ ! -s "$MODEL_FILE" ]; then
  echo "正在下载 Qwen2.5-Coder-0.5B-Instruct Q4_K_M（约 491MB）……"
  curl -fL --retry 4 --connect-timeout 30 "$MODEL_URL" -o "$temporary/model.gguf"
  [ "$(wc -c < "$temporary/model.gguf")" -gt 400000000 ] || { echo "模型文件下载不完整。" >&2; exit 1; }
  [ "$(dd if="$temporary/model.gguf" bs=4 count=1 status=none)" = "GGUF" ] || { echo "模型文件格式校验失败。" >&2; exit 1; }
  install -m 644 "$temporary/model.gguf" "$MODEL_FILE"
fi

chown -R root:root "$MODEL_DIR"
if [ -f "$INSTALL_DIR/.env" ]; then
  grep -q '^LOCAL_MODEL_ENABLED=' "$INSTALL_DIR/.env" || printf '\nLOCAL_MODEL_ENABLED=1\n' >> "$INSTALL_DIR/.env"
  grep -q '^LOCAL_MODEL_BINARY=' "$INSTALL_DIR/.env" || printf 'LOCAL_MODEL_BINARY=%s\n' "$MODEL_DIR/bin/llama-cli" >> "$INSTALL_DIR/.env"
  grep -q '^LOCAL_MODEL_PATH=' "$INSTALL_DIR/.env" || printf 'LOCAL_MODEL_PATH=%s\n' "$MODEL_FILE" >> "$INSTALL_DIR/.env"
  grep -q '^LOCAL_MODEL_THREADS=' "$INSTALL_DIR/.env" || printf 'LOCAL_MODEL_THREADS=1\nLOCAL_MODEL_MEMORY_MB=900\nLOCAL_MODEL_BATCH_SIZE=3\n' >> "$INSTALL_DIR/.env"
  chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi

echo "本地模型已就绪：$MODEL_FILE"
