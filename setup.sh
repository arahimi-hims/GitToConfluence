#!/bin/bash
set -e

echo "Installing Pandoc..."
brew install pandoc

echo "Installing Mermaid CLI..."
npm install -g @mermaid-js/mermaid-cli

echo "Installing uv..."
pip install uv

echo "Syncing dependencies..."
uv sync

echo "Setup complete!"
