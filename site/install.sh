#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# CopyCat Installer
# Usage: bash <(curl -s https://copycat.adam-works.co/install.sh)
# ─────────────────────────────────────────────────────────────────────────────

set -e

REPO="https://github.com/adamaiworks-hub/copycat/archive/refs/heads/main.tar.gz"
INSTALL_DIR="$HOME/.copycat"
VENV_DIR="$INSTALL_DIR/venv"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${GREEN}  🐱 CopyCat Installer${NC}"
echo -e "${CYAN}  copycat.adam-works.co${NC}"
echo "  ─────────────────────────────────────"
echo ""

# ── Detect OS ─────────────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
echo -e "  Platform: ${CYAN}$OS $ARCH${NC}"

# ── Check Python ──────────────────────────────────────────────────────────────
echo -n "  Checking Python... "
if command -v python3 &>/dev/null; then
  PY=$(python3 --version 2>&1 | awk '{print $2}')
  echo -e "${GREEN}$PY ✓${NC}"
else
  echo -e "${RED}not found${NC}"
  echo ""
  echo "  Python 3.9+ is required."
  echo "  Install from: https://python.org/downloads"
  exit 1
fi

# ── Check Bullpen ─────────────────────────────────────────────────────────────
echo -n "  Checking Bullpen CLI... "
if command -v bullpen &>/dev/null; then
  BV=$(bullpen --version 2>/dev/null || echo "installed")
  echo -e "${GREEN}$BV ✓${NC}"
else
  echo -e "${YELLOW}not found — installing...${NC}"
  if [[ "$OS" == "Darwin" ]]; then
    if command -v brew &>/dev/null; then
      brew install bullpenfi/tap/bullpen
    else
      echo -e "  ${RED}Homebrew not found. Install from: https://brew.sh${NC}"
      exit 1
    fi
  else
    echo -e "  ${RED}Please install Bullpen manually: https://cli.bullpen.fi${NC}"
    exit 1
  fi
fi

# ── Create install dir ────────────────────────────────────────────────────────
echo -n "  Setting up install directory... "
mkdir -p "$INSTALL_DIR"
echo -e "${GREEN}$INSTALL_DIR ✓${NC}"

# ── Download CopyCat ──────────────────────────────────────────────────────────
echo -n "  Downloading CopyCat... "
TMP=$(mktemp -d)
curl -sL "$REPO" -o "$TMP/copycat.tar.gz" 2>/dev/null || {
  echo -e "${RED}download failed${NC}"
  echo "  Visit https://copycat.adam-works.co for manual install."
  exit 1
}
tar -xzf "$TMP/copycat.tar.gz" -C "$TMP" 2>/dev/null
SRC=$(find "$TMP" -maxdepth 1 -type d | tail -1)
cp -r "$SRC/." "$INSTALL_DIR/"
rm -rf "$TMP"
echo -e "${GREEN}done ✓${NC}"

# ── Python virtualenv ─────────────────────────────────────────────────────────
echo -n "  Creating Python environment... "
python3 -m venv "$VENV_DIR" --upgrade-deps 2>/dev/null
echo -e "${GREEN}done ✓${NC}"

echo -n "  Installing dependencies... "
"$VENV_DIR/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo -e "${GREEN}done ✓${NC}"

# ── Bullpen login ─────────────────────────────────────────────────────────────
echo ""
echo -e "  ${YELLOW}Logging in to Bullpen (Polymarket)...${NC}"
bullpen login
echo ""

# ── Create launch script ──────────────────────────────────────────────────────
LAUNCH="$INSTALL_DIR/start.sh"
cat > "$LAUNCH" << 'LAUNCHER'
#!/usr/bin/env bash
cd "$HOME/.copycat"
source venv/bin/activate
python3 dashboard.py
LAUNCHER
chmod +x "$LAUNCH"

# ── macOS: add to PATH ────────────────────────────────────────────────────────
if [[ "$OS" == "Darwin" ]]; then
  ALIAS_LINE="alias copycat='bash $HOME/.copycat/start.sh'"
  for RC in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [[ -f "$RC" ]] && ! grep -q "alias copycat=" "$RC"; then
      echo "$ALIAS_LINE" >> "$RC"
    fi
  done
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo "  ─────────────────────────────────────"
echo -e "  ${GREEN}✓ CopyCat installed successfully!${NC}"
echo ""
echo -e "  ${CYAN}Starting dashboard...${NC}"
echo "  Open your browser to: http://localhost:5001"
echo "  Enter your license key when prompted."
echo ""

# Launch dashboard
bash "$LAUNCH"
