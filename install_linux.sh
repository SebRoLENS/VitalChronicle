#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "$project_dir/.venv"
"$project_dir/.venv/bin/python" -m pip install --upgrade pip
"$project_dir/.venv/bin/python" -m pip install "$project_dir"

echo "Installation complete. Start VitalChronicle with:"
echo "$project_dir/.venv/bin/vitalchronicle"
