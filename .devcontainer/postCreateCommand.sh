#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
. .venv/bin/activate

pip install --upgrade pip wheel setuptools

# Home Assistant core + common dev tooling
pip install "homeassistant" \
  "ruff" "pytest" "pytest-cov" "requests" "voluptuous" "aiohttp"

# Prepare runtime HA config folder
mkdir -p hass_config/custom_components

# Link your integration code into HA config
rm -rf hass_config/custom_components/waste_collection_schedule || true
ln -s "$(pwd)/custom_components/waste_collection_schedule" \
  hass_config/custom_components/waste_collection_schedule

# Copy base config (overwrite each time so edits in .devcontainer/configuration.yaml propagate)
cp .devcontainer/configuration.yaml hass_config/configuration.yaml

echo "Devcontainer setup complete."
echo "Run: Tasks → Run Home Assistant on port 9123"
echo "Docker Compose: $(docker-compose version 2>/dev/null || echo not installed)"
