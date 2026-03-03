#!/usr/bin/env bash
set -euo pipefail

# Build and publish vNext thin-agent artifacts (Linux x86_64) + onboarding helper.
# Run on Hetzner host from thin-agent directory.
#
# Usage:
#   ./build_and_publish_vnext_thin.sh v0.5

VER="${1:-v0.5}"
APP_BASE="radiotiker-thin-agent-vnext"
BIN_NAME="${APP_BASE}-${VER}-linux"
TGZ_NAME="${BIN_NAME}.tar.gz"
ONBOARD_NAME="radiotiker-vnext-onboard.sh"

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${ROOT_DIR}"

if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt pyinstaller

rm -rf build dist *.spec
pyinstaller thin_agent.py --onefile --name "${BIN_NAME}"

tar -czf "${TGZ_NAME}" -C dist "${BIN_NAME}"

echo "Built:"
echo "  dist/${BIN_NAME}"
echo "  ${ROOT_DIR}/${TGZ_NAME}"

echo "Publishing to /var/www/radio.tiker.es/html/downloads (requires sudo)..."
sudo mkdir -p /var/www/radio.tiker.es/html/downloads
sudo install -m 755 "dist/${BIN_NAME}" "/var/www/radio.tiker.es/html/downloads/${BIN_NAME}"
sudo install -m 644 "${TGZ_NAME}" "/var/www/radio.tiker.es/html/downloads/${TGZ_NAME}"
sudo install -m 755 "onboard_and_start.sh" "/var/www/radio.tiker.es/html/downloads/${ONBOARD_NAME}"

sudo ln -sf "${BIN_NAME}" /var/www/radio.tiker.es/html/downloads/radiotiker-thin-agent-vnext-latest-linux
sudo ln -sf "${TGZ_NAME}" /var/www/radio.tiker.es/html/downloads/radiotiker-thin-agent-vnext-latest-linux.tar.gz
sudo ln -sf "${ONBOARD_NAME}" /var/www/radio.tiker.es/html/downloads/radiotiker-vnext-onboard-latest.sh

echo
echo "Published URLs:"
echo "  https://radio.tiker.es/downloads/${BIN_NAME}"
echo "  https://radio.tiker.es/downloads/${TGZ_NAME}"
echo "  https://radio.tiker.es/downloads/radiotiker-thin-agent-vnext-latest-linux"
echo "  https://radio.tiker.es/downloads/radiotiker-vnext-onboard-latest.sh"
