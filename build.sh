#!/usr/bin/env bash
set -euo pipefail

# Usage: ./build.sh v0.3
VER=${1:-v0.3}
APP="radiotiker-thin-agent"

source .venv/bin/activate

# Figure out the libpython to bundle for --onefile
PYLIB=$(python3 - <<'PY'
import sysconfig, os
libdir=sysconfig.get_config_var("LIBDIR")
libname=sysconfig.get_config_var("LDLIBRARY")
print(os.path.join(libdir, libname))
PY
)

# Clean
rm -rf build dist *.spec

# --- ONEFILE (fast to download; now includes libpython) ---
pyinstaller thin_agent_gui.py \
  --onefile \
  --name ${APP}-${VER}-linux \
  --add-binary "$PYLIB:."

install -d /var/www/radio.tiker.es/html/downloads
install -m755 dist/${APP}-${VER}-linux /var/www/radio.tiker.es/html/downloads/

# Maintain a "latest" pointer
ln -sf ${APP}-${VER}-linux /var/www/radio.tiker.es/html/downloads/${APP}-latest-linux

# --- ONEDIR (bulletproof fallback) ---
rm -rf build dist *.spec
pyinstaller thin_agent_gui.py \
  --onedir \
  --name ${APP}-${VER}-linux

tar -czf ${APP}-${VER}-linux.tar.gz -C dist ${APP}-${VER}-linux
install -m644 ${APP}-${VER}-linux.tar.gz /var/www/radio.tiker.es/html/downloads/
ln -sf ${APP}-${VER}-linux.tar.gz /var/www/radio.tiker.es/html/downloads/${APP}-latest-linux.tar.gz

echo "Published:"
echo "  https://radio.tiker.es/downloads/${APP}-${VER}-linux"
echo "  https://radio.tiker.es/downloads/${APP}-${VER}-linux.tar.gz"
echo "  https://radio.tiker.es/downloads/${APP}-latest-linux"
echo "  https://radio.tiker.es/downloads/${APP}-latest-linux.tar.gz"

