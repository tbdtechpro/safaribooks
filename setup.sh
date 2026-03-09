#!/usr/bin/env bash
# setup.sh — SafariBooks environment setup for Ubuntu 24.04
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# What this script does:
#   1. Verifies Ubuntu 24.04 (warns on other platforms, doesn't abort)
#   2. Installs system packages: python3, python3-pip, python3-venv, calibre
#   3. Creates a Python virtual environment (.venv)
#   4. Installs Python dependencies (including bubbletea/lipgloss from your forks)
#   5. Prints a quick-start guide

set -euo pipefail

###############################################################################
# Helpers
###############################################################################

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[*]${RESET} $*"; }
success() { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✗]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

###############################################################################
# 1. Platform check
###############################################################################

info "Checking platform…"

if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    if [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]]; then
        success "Ubuntu 24.04 detected."
    else
        warn "This script targets Ubuntu 24.04. Detected: ${PRETTY_NAME:-unknown}."
        warn "Continuing anyway — adjust as needed for your distribution."
    fi
else
    warn "Cannot determine OS. Continuing anyway."
fi

###############################################################################
# 2. System packages
###############################################################################

info "Installing system packages (requires sudo)…"

if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        git
    success "System packages installed."
else
    warn "apt-get not found — skipping system package installation."
    warn "Ensure Python 3.11+, pip, venv, and build tools are installed manually."
fi

###############################################################################
# 3. Calibre
###############################################################################

info "Checking for Calibre (ebook-convert)…"

if command -v ebook-convert &>/dev/null; then
    CALIBRE_VER=$(ebook-convert --version 2>&1 | head -1)
    success "Calibre already installed: ${CALIBRE_VER}"
else
    info "Calibre not found. Installing via apt-get…"
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y calibre
        if command -v ebook-convert &>/dev/null; then
            success "Calibre installed."
        else
            warn "Calibre installation may have failed. Try:"
            warn "  sudo apt-get install calibre"
            warn "  or visit https://calibre-ebook.com/download_linux"
        fi
    else
        warn "Cannot auto-install Calibre. Please install it manually:"
        warn "  https://calibre-ebook.com/download_linux"
    fi
fi

###############################################################################
# 4. Python virtual environment
###############################################################################

VENV_DIR="${SCRIPT_DIR}/.venv"

info "Setting up Python virtual environment at ${VENV_DIR}…"

if [[ -d "${VENV_DIR}" ]]; then
    warn "Virtual environment already exists — reusing it."
else
    python3 -m venv "${VENV_DIR}"
    success "Virtual environment created."
fi

# Activate
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

python_ver=$(python3 --version)
info "Active Python: ${python_ver}"

# Confirm Python ≥ 3.11
python3 -c "
import sys
if sys.version_info < (3, 11):
    print('Python 3.11+ is required. Got:', sys.version)
    sys.exit(1)
" || die "Please install Python 3.11 or newer."

###############################################################################
# 5. Python dependencies
###############################################################################

info "Upgrading pip…"
pip install --quiet --upgrade pip

info "Installing core Python dependencies…"
pip install --quiet \
    "lxml>=4.9.0" \
    "requests>=2.28.0" \
    "browser_cookie3"

info "Installing bubbletea (tbdtechpro fork)…"
pip install --quiet "git+https://github.com/tbdtechpro/bubbletea"

info "Installing lipgloss (tbdtechpro fork)…"
pip install --quiet "git+https://github.com/tbdtechpro/lipgloss"

success "All Python dependencies installed."

###############################################################################
# 6. Smoke-test imports
###############################################################################

info "Verifying imports…"

python3 - <<'PYCHECK'
import importlib.util
import sys

missing = []
for pkg in ("lxml", "requests", "browser_cookie3", "bubbletea", "lipgloss"):
    if importlib.util.find_spec(pkg) is None:
        missing.append(pkg)

if missing:
    print("Missing packages:", ", ".join(missing))
    sys.exit(1)
print("All imports OK.")
PYCHECK

success "Import check passed."

###############################################################################
# 7. Quick-start
###############################################################################

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  SafariBooks — Setup Complete!${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "  ${CYAN}Activate the environment:${RESET}"
echo -e "    source .venv/bin/activate"
echo ""
echo -e "  ${CYAN}Launch the interactive TUI:${RESET}"
echo -e "    python tui.py"
echo ""
echo -e "  ${CYAN}Or use the command-line interface:${RESET}"
echo -e "    python safaribooks.py --cred \"email@example.com:password\" <BOOK_ID>"
echo ""
echo -e "  ${CYAN}Save session cookie from your browser (for SSO/company login):${RESET}"
echo -e "    python retrieve_cookies.py --cookie \"orm-jwt=eyJ...; orm-rt=...\""
echo -e "    python retrieve_cookies.py --login \"email@example.com:password\""
echo ""
echo -e "  ${CYAN}Convert downloaded EPUBs with Calibre:${RESET}"
echo -e "    python calibre_convert.py Books/*/*.epub"
echo ""
echo -e "  ${CYAN}Populate library registry from existing downloads:${RESET}"
echo -e "    python safaribooks.py --scan-library"
echo ""
echo -e "  ${YELLOW}Note:${RESET} Email/password login is the recommended method."
echo -e "  For SSO or company accounts, log in via browser first, then use"
echo -e "  retrieve_cookies.py or the TUI cookie screen to save your session."
echo ""
