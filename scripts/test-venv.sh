#!/usr/bin/env bash
# Create the test virtualenv on the gateway's OWN runtime, so the suite runs against the KiroCrew it ships on.
#
#   scripts/test-venv.sh            # ~/.venvs/aidlc-studio-test (override with KC_TEST_VENV)
#
# The venv sees the desktop bundle's site-packages (kiro_crew, aiohttp…) through --system-site-packages
# and adds only pytest and pytest-xdist. The bundle ships no pip, so `uv` installs them.
set -euo pipefail
BUNDLE_PY="${BUNDLE_PY:-/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12}"
VENV="${KC_TEST_VENV:-$HOME/.venvs/aidlc-studio-test}"
[ -x "$BUNDLE_PY" ] || { echo "test-venv: gateway interpreter not found: $BUNDLE_PY (set BUNDLE_PY)" >&2; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "test-venv: uv is required (https://docs.astral.sh/uv/)" >&2; exit 1; }
rm -rf "$VENV"
"$BUNDLE_PY" -m venv --system-site-packages --without-pip "$VENV"
uv pip install -q --python "$VENV/bin/python" "pytest>=9" "pytest-xdist>=3.5"
"$VENV/bin/python" -c "import kiro_crew, pytest, xdist; print('test venv ready:', '$VENV', 'kiro_crew', kiro_crew.__version__)"
