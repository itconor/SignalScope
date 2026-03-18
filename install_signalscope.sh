#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="SignalScope"
REPO_URL="https://github.com/itconor/SignalScope.git"
DEFAULT_BRANCH="main"
DEFAULT_INSTALL_DIR="/opt/signalscope"
DEFAULT_SERVICE_NAME="signalscope"
DEFAULT_USER="signalscope"
PYTHON_BIN="python3"
APP_FILE="signalscope.py"

INSTALL_DIR="$DEFAULT_INSTALL_DIR"
INSTALL_SERVICE=""
INSTALL_SDR=""
FORCE=0
BRANCH="$DEFAULT_BRANCH"

if [[ -t 0 && -t 1 ]]; then
  INTERACTIVE=1
else
  INTERACTIVE=0
fi

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
BLU='\033[0;34m'
CYN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${BLU}[$APP_NAME]${NC} $*"; }
ok()   { echo -e "${GRN}[OK]${NC} $*"; }
warn() { echo -e "${YLW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*" >&2; }
step() { echo; echo -e "${CYN}==>${NC} $*"; }

die() {
  err "$*"
  exit 1
}

usage() {
  cat <<EOF
$APP_NAME installer

Interactive:
  /bin/bash <(curl -fsSL https://your-url/install_signalscope.sh)

Non-interactive:
  curl -fsSL https://your-url/install_signalscope.sh | bash -s -- --service --sdr

Options:
  --service           Install systemd service
  --no-service        Do not install systemd service
  --sdr               Install SDR support
  --no-sdr            Do not install SDR support
  --install-dir PATH  Install directory (default: $DEFAULT_INSTALL_DIR)
  --branch NAME       Git branch/tag to use (default: $DEFAULT_BRANCH)
  --force             Skip confirmation prompts where possible
  -h, --help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) INSTALL_SERVICE=1; shift ;;
    --no-service) INSTALL_SERVICE=0; shift ;;
    --sdr) INSTALL_SDR=1; shift ;;
    --no-sdr) INSTALL_SDR=0; shift ;;
    --install-dir)
      [[ $# -ge 2 ]] || die "--install-dir needs a value"
      INSTALL_DIR="$2"; shift 2 ;;
    --branch)
      [[ $# -ge 2 ]] || die "--branch needs a value"
      BRANCH="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

ask_yes_no() {
  local prompt="$1"
  local default="$2"
  local reply

  if [[ "$FORCE" -eq 1 ]]; then
    [[ "$default" == "y" ]] && return 0 || return 1
  fi

  if [[ "$INTERACTIVE" -eq 0 ]]; then
    [[ "$default" == "y" ]] && return 0 || return 1
  fi

  while true; do
    if [[ "$default" == "y" ]]; then
      read -r -p "$prompt [Y/n]: " reply
      reply="${reply:-Y}"
    else
      read -r -p "$prompt [y/N]: " reply
      reply="${reply:-N}"
    fi
    case "${reply,,}" in
      y|yes) return 0 ;;
      n|no) return 1 ;;
    esac
  done
}

ask_value() {
  local prompt="$1"
  local default="$2"
  local reply

  if [[ "$FORCE" -eq 1 || "$INTERACTIVE" -eq 0 ]]; then
    echo "$default"
    return
  fi

  read -r -p "$prompt [$default]: " reply
  echo "${reply:-$default}"
}

require_root() {
  if [[ $EUID -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
      log "Re-running with sudo..."
      exec sudo bash "$0" "$@"
    else
      die "Run this installer as root or install sudo."
    fi
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y "$@"
}

install_base_packages() {
  step "Installing base packages"
  apt_install \
    git curl ca-certificates \
    ffmpeg \
    "$PYTHON_BIN" "$PYTHON_BIN"-venv "$PYTHON_BIN"-dev \
    build-essential pkg-config \
    libsndfile1
}

install_sdr_packages() {
  step "Installing SDR packages"
  apt_install rtl-sdr librtlsdr-dev welle-cli

  if [[ ! -f /etc/modprobe.d/rtlsdr.conf ]]; then
    echo 'blacklist dvb_usb_rtl28xxu' > /etc/modprobe.d/rtlsdr.conf
    ok "Created /etc/modprobe.d/rtlsdr.conf"
  fi

  modprobe -r dvb_usb_rtl28xxu >/dev/null 2>&1 || true
}

clone_or_update_repo() {
  step "Fetching $APP_NAME"
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Existing git repo found, updating..."
    git -C "$INSTALL_DIR" fetch --all --tags
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
  else
    rm -rf "$INSTALL_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
}

ensure_app_file() {
  [[ -f "$INSTALL_DIR/$APP_FILE" ]] || die "Expected app file not found: $INSTALL_DIR/$APP_FILE"
}

setup_venv() {
  step "Creating Python virtual environment"
  "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
  # shellcheck disable=SC1091
  source "$INSTALL_DIR/venv/bin/activate"

  step "Upgrading pip"
  pip install --upgrade pip wheel setuptools

  step "Installing Python dependencies"
  pip install \
    flask waitress numpy onnx onnxruntime cryptography \
    pyrtlsdr==0.2.93
}

set_python_cap() {
  step "Setting Python capability for low ports"
  local pybin
  pybin="$(readlink -f "$(command -v "$PYTHON_BIN")")"
  if [[ -n "$pybin" && -f "$pybin" ]]; then
    if command -v setcap >/dev/null 2>&1; then
      setcap cap_net_bind_service=+ep "$pybin" || warn "Could not set cap_net_bind_service on $pybin"
      ok "Capability set on $pybin"
    else
      warn "setcap not found; low ports may require root"
    fi
  fi
}

create_service_user() {
  id -u "$DEFAULT_USER" >/dev/null 2>&1 || useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$DEFAULT_USER"
  chown -R "$DEFAULT_USER:$DEFAULT_USER" "$INSTALL_DIR"
}

install_systemd_service() {
  step "Installing systemd service"
  create_service_user

  cat >/etc/systemd/system/${DEFAULT_SERVICE_NAME}.service <<EOF
[Unit]
Description=$APP_NAME
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$DEFAULT_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/$APP_FILE
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "${DEFAULT_SERVICE_NAME}.service"
  ok "Service installed: ${DEFAULT_SERVICE_NAME}.service"
}

show_banner() {
  cat <<EOF

${APP_NAME} Installer
---------------------
This installer will:
 - install system dependencies
 - clone/update the SignalScope repository
 - create a Python virtualenv
 - install Python dependencies
 - optionally install SDR support
 - optionally create a systemd service

EOF
}

main() {
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    [[ "${ID:-}" == "ubuntu" || "${ID_LIKE:-}" == *debian* ]] || warn "This installer is designed for Ubuntu/Debian."
  fi

  if [[ -z "$INSTALL_SERVICE" ]]; then
    if ask_yes_no "Install as a systemd service?" "y"; then
      INSTALL_SERVICE=1
    else
      INSTALL_SERVICE=0
    fi
  fi

  if [[ -z "$INSTALL_SDR" ]]; then
    if ask_yes_no "Install SDR support (rtl-sdr + welle-cli)?" "y"; then
      INSTALL_SDR=1
    else
      INSTALL_SDR=0
    fi
  fi

  INSTALL_DIR="$(ask_value "Install directory" "$INSTALL_DIR")"

  show_banner
  log "Install dir: $INSTALL_DIR"
  log "Service: $([[ "$INSTALL_SERVICE" -eq 1 ]] && echo yes || echo no)"
  log "SDR support: $([[ "$INSTALL_SDR" -eq 1 ]] && echo yes || echo no)"
  log "Branch: $BRANCH"

  if [[ "$FORCE" -ne 1 && "$INTERACTIVE" -eq 1 ]]; then
    echo
    read -r -p "Continue? [Y/n]: " confirm
    confirm="${confirm:-Y}"
    [[ "${confirm,,}" == "y" || "${confirm,,}" == "yes" ]] || exit 0
  fi

  need_cmd git
  need_cmd curl

  install_base_packages

  if [[ "$INSTALL_SDR" -eq 1 ]]; then
    install_sdr_packages
  fi

  clone_or_update_repo
  ensure_app_file
  setup_venv
  set_python_cap

  mkdir -p /var/log/signalscope
  chmod 755 /var/log/signalscope || true

  if [[ "$INSTALL_SERVICE" -eq 1 ]]; then
    install_systemd_service
    echo
    ok "Start with:"
    echo "  sudo systemctl start ${DEFAULT_SERVICE_NAME}.service"
    echo "  sudo systemctl status ${DEFAULT_SERVICE_NAME}.service"
  else
    echo
    ok "Run manually with:"
    echo "  source \"$INSTALL_DIR/venv/bin/activate\" && python \"$INSTALL_DIR/$APP_FILE\""
  fi

  echo
  ok "Installation complete."
}

require_root "$@"
main "$@"