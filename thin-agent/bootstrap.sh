#!/bin/bash

echo "🔧 Bootstrapping RadioTiker Thin Agent..."

# Set environment directory
VENV_DIR=".venv"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv $VENV_DIR
else
    echo "✅ Virtual environment already exists."
fi

# Activate the environment
source $VENV_DIR/bin/activate

# Install requirements
echo "📚 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "✅ All set up! You can now run:"
echo ""
echo "  source $VENV_DIR/bin/activate"
echo "  python thin_agent.py     # CLI version"
echo "  python thin_agent_gui.py # GUI version"
echo ""

