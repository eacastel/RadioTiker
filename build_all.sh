#!/bin/bash

set -e  # Exit on any error

APP_NAME="radiotiker-thin-agent"
VERSION="v0.1"

echo "🔧 Activating virtual environment..."
source .venv/bin/activate

echo "🧹 Cleaning previous builds..."
rm -rf build/ dist/ *.spec

echo "📦 Building Windows executable..."
pyinstaller thin_agent_gui.py --onefile --name ${APP_NAME}-${VERSION}-win.exe

echo "📦 Building Linux binary..."
pyinstaller thin_agent_gui.py --onefile --name ${APP_NAME}-${VERSION}-linux

echo "🗜️ Creating tar.gz for Linux..."
tar -czvf ${APP_NAME}-${VERSION}-linux.tar.gz -C dist ${APP_NAME}-${VERSION}-linux

echo "📁 Copying builds to NGINX downloads folder..."
sudo cp dist/${APP_NAME}-${VERSION}-win.exe /var/www/radio.tiker.es/html/downloads/
sudo cp ${APP_NAME}-${VERSION}-linux.tar.gz /var/www/radio.tiker.es/html/downloads/

echo "✅ Done! Downloads are live at:"
echo "   https://radio.tiker.es/downloads/${APP_NAME}-${VERSION}-win.exe"
echo "   https://radio.tiker.es/downloads/${APP_NAME}-${VERSION}-linux.tar.gz"

