#!/usr/bin/env bash
# Install fpod: create a venv, install the package (editable), and symlink the
# `fpod` entry point onto your PATH. Idempotent — safe to re-run after a pull.
#
# Usage:
#   ./install.sh
#
# Env overrides:
#   PYTHON       interpreter to build the venv with (default: python3)
#   FPOD_BIN_DIR where to put the symlink     (default: ~/.local/bin)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_DIR/.venv"
BIN_DIR="${FPOD_BIN_DIR:-$HOME/.local/bin}"
PY="${PYTHON:-python3}"

# 1. Python version gate
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "error: '$PY' not found. Set PYTHON=/path/to/python3.11+." >&2
  exit 1
fi
if [ "$("$PY" -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')" != "1" ]; then
  ver="$("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  echo "error: need Python >= 3.11, found $ver ($PY)." >&2
  exit 1
fi

# 2. venv + editable install
if [ ! -d "$VENV" ]; then
  echo "==> creating venv at $VENV"
  "$PY" -m venv "$VENV"
fi
echo "==> installing fpod (editable)"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$REPO_DIR"

# 3. symlink onto PATH
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/fpod" "$BIN_DIR/fpod"
echo "==> linked $BIN_DIR/fpod -> $VENV/bin/fpod"

# 4. PATH sanity check
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    echo "warning: $BIN_DIR is not on your PATH."
    echo "  add this to your shell rc:  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

echo
echo "fpod $("$VENV/bin/fpod" version) installed."
echo "Next: fpod init   (then: fpod create <name>)"
