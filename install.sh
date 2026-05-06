#!/usr/bin/env bash
#
# Ravn AI skill installer.
#
# Usage (one-liner from curl):
#   curl -fsSL https://raw.githubusercontent.com/freakspace/ravn-cli/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/freakspace/ravn-cli/main/install.sh | bash -s -- --codex
#   curl -fsSL https://raw.githubusercontent.com/freakspace/ravn-cli/main/install.sh | bash -s -- --dir /custom/path
#
# Or, after cloning manually:
#   ./install.sh
#

set -e
set -o pipefail

REPO_URL="https://github.com/freakspace/ravn-cli"
SKILL_NAME="ravn"
TARGET_DIR=""
RUNTIME_LABEL="Claude Code"
DEFAULT_DIR="$HOME/.claude/skills/$SKILL_NAME"

usage() {
  cat <<USAGE
Ravn AI skill installer.

Usage:
  curl -fsSL https://raw.githubusercontent.com/freakspace/ravn-cli/main/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/freakspace/ravn-cli/main/install.sh | bash -s -- --codex
  curl -fsSL https://raw.githubusercontent.com/freakspace/ravn-cli/main/install.sh | bash -s -- --dir /custom/path

Options:
  --codex          Install for Codex (~/.agents/skills/ravn) instead of Claude Code.
  --dir <PATH>     Install to a custom path.
  -h, --help       Show this help.

Environment:
  RAVN_INSTALL_DIR Same as --dir.
USAGE
}

while [ "${1:-}" != "" ]; do
  case "$1" in
    --codex)
      DEFAULT_DIR="$HOME/.agents/skills/$SKILL_NAME"
      RUNTIME_LABEL="Codex"
      shift
      ;;
    --dir)
      if [ -z "${2:-}" ]; then
        echo "Error: --dir requires a path argument." >&2
        exit 1
      fi
      TARGET_DIR="$2"
      RUNTIME_LABEL="custom path"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown argument '$1'." >&2
      usage >&2
      exit 1
      ;;
  esac
done

TARGET_DIR="${TARGET_DIR:-${RAVN_INSTALL_DIR:-$DEFAULT_DIR}}"

# Pre-flight checks.
if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required but not on PATH." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 (3.10+) is required but not on PATH." >&2
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  echo "Error: Python 3.10+ required (found $PY_VER)." >&2
  exit 1
fi

mkdir -p "$(dirname "$TARGET_DIR")"

if [ -d "$TARGET_DIR" ]; then
  if [ -d "$TARGET_DIR/.git" ]; then
    echo "Existing install at $TARGET_DIR — pulling latest..."
    git -C "$TARGET_DIR" pull --ff-only
  else
    echo "Error: $TARGET_DIR exists but isn't a git checkout." >&2
    echo "Move or remove it and re-run." >&2
    exit 1
  fi
else
  echo "Installing Ravn skill ($RUNTIME_LABEL) to $TARGET_DIR..."
  git clone --depth 1 "$REPO_URL" "$TARGET_DIR"
fi

cat <<DONE

Installed.

Next steps:

  1. Sign in (opens your browser):
       python3 "$TARGET_DIR/scripts/raven_cli.py" login

  2. Verify:
       python3 "$TARGET_DIR/scripts/raven_cli.py" whoami

The skill will be discoverable as 'ravn' the next time you start your AI assistant.

To update later, re-run the same command — the installer pulls the latest version
in place.

DONE
