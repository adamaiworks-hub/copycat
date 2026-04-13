#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  CopyCat Installer
#  Polymarket Copy Trading Bot
# ─────────────────────────────────────────────────────────────
set -e

INSTALL_DIR="$HOME/CopyCat"
VENV_DIR="$INSTALL_DIR/.venv"
LICENSE_DIR="$HOME/.copycat"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colors ────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
info() { echo -e "  ${BLUE}→${NC}  $1"; }
warn() { echo -e "  ${YELLOW}!${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC}  $1"; exit 1; }
step() { echo -e "\n${BOLD}$1${NC}"; }

clear
echo -e "${BOLD}"
cat << 'EOF'
   ___                 ____        _
  / __|___  _ __ _  _ | __ )  ___ | |_
 | |  / _ \| '_ \ || ||  _ \ / _ \|  _|
 | |_| (_) | |_) \_, ||_) | (_) | |_
  \___\___/| .__/|__/ |____/ \___/ \__|
           |_|
  Polymarket Copy Trading — Installer
EOF
echo -e "${NC}"
echo -e "  ${DIM}Version 1.0  •  copycat.fi${NC}"
echo ""

# ── Step 1: Check Python ──────────────────────────────────────
step "Step 1/5  Checking requirements"

if ! command -v python3 &>/dev/null; then
  fail "Python 3 not found. Install from https://python.org"
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
PY_MINOR=$(echo $PY_VER | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || [ "$PY_MINOR" -lt 9 ]; then
  fail "Python 3.9+ required (found $PY_VER). Upgrade at https://python.org"
fi
ok "Python $PY_VER"

# ── Step 2: Install Bullpen CLI ───────────────────────────────
step "Step 2/5  Installing Bullpen CLI"

if command -v bullpen &>/dev/null; then
  ok "Bullpen already installed ($(bullpen --version 2>/dev/null || echo 'version unknown'))"
else
  if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
  info "Installing bullpen..."
  brew install bullpenfi/tap/bullpen
  ok "Bullpen installed"
fi

# ── Step 3: Create install directory + virtualenv ─────────────
step "Step 3/5  Setting up CopyCat"

mkdir -p "$INSTALL_DIR"
mkdir -p "$LICENSE_DIR"

info "Creating Python environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

info "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet flask requests cryptography python-dotenv

ok "Environment ready at $INSTALL_DIR"

# ── Step 4: Copy bot files ────────────────────────────────────
step "Step 4/5  Installing bot files"

FILES=(
  "polymarket_copycat.py"
  "kalshi_mm_bot.py"
  "dashboard.py"
  "license.py"
  "requirements.txt"
)

for f in "${FILES[@]}"; do
  if [ -f "$REPO_DIR/$f" ]; then
    cp "$REPO_DIR/$f" "$INSTALL_DIR/$f"
    ok "Installed $f"
  else
    warn "Skipping $f (not found)"
  fi
done

# Create .env if it doesn't exist
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cat > "$INSTALL_DIR/.env" << 'ENVEOF'
# ── Polymarket CopyCat ──────────────────────────────────────
COPY_TRADE_AMOUNT=10
DAILY_LOSS_LIMIT=100
POLL_INTERVAL_SECS=30
TOP_N_TRADERS=5

# ── Kalshi Bot (Pro + Kalshi only) ────────────────────────────
KALSHI_API_KEY_ID=
KALSHI_PRIVATE_KEY_PATH=
ENVEOF
  ok "Created .env config"
fi

# ── Step 5: License key ───────────────────────────────────────
step "Step 5/5  Activate your license"

echo ""
if [ -f "$LICENSE_DIR/license.key" ]; then
  ok "License already activated"
else
  echo -e "  Enter your CopyCat license key."
  echo -e "  ${DIM}(Format: CB-PRO-XXXXXXXXXXXXXXXX — from your purchase confirmation)${NC}"
  echo ""
  read -rp "  License key: " LICENSE_KEY

  if [[ "$LICENSE_KEY" == CB-* ]]; then
    echo "$LICENSE_KEY" > "$LICENSE_DIR/license.key"
    ok "License saved"
  else
    warn "Key format looks off — you can enter it later in the dashboard Settings tab."
  fi
fi

# ── Create launch script ──────────────────────────────────────
cat > "$INSTALL_DIR/launch.sh" << LAUNCHEOF
#!/usr/bin/env bash
cd "$INSTALL_DIR"
source "$VENV_DIR/bin/activate"
echo ""
echo "  Starting CopyCat Dashboard..."
echo "  Open → http://localhost:5001"
echo ""
python3 dashboard.py &
sleep 1
open http://localhost:5001
wait
LAUNCHEOF
chmod +x "$INSTALL_DIR/launch.sh"

# ── Log in to Bullpen ─────────────────────────────────────────
echo ""
echo -e "${BOLD}Almost done!${NC}"
echo ""
echo -e "  You'll need to log in to Bullpen to connect your Polymarket wallet."
read -rp "  Log in now? [Y/n] " DO_LOGIN
if [[ "$DO_LOGIN" != "n" && "$DO_LOGIN" != "N" ]]; then
  bullpen login
fi

# ── Done ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  ✓ CopyCat installed successfully!${NC}"
echo ""
echo -e "  ${BOLD}To launch:${NC}"
echo -e "  bash $INSTALL_DIR/launch.sh"
echo ""
echo -e "  ${DIM}Dashboard will open at http://localhost:5001${NC}"
echo ""
