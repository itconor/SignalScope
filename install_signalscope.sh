#!/usr/bin/env bash
set -Eeuo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

ok(){ echo -e "${GREEN}✔ $*${NC}"; }
warn(){ echo -e "${YELLOW}⚠ $*${NC}"; }
err(){ echo -e "${RED}✖ $*${NC}" >&2; }
step(){ echo -e "\n${BLUE}==> $*${NC}"; }
info(){ echo -e "${CYAN}$*${NC}"; }

APP_NAME="SignalScope"
SERVICE_NAME="signalscope"
APP_PY_NAME="signalscope.py"
LEGACY_APP_PY="LivewireAIMonitor.py"
REPO_URL="https://github.com/itconor/SignalScope.git"
RAW_BASE_URL="https://raw.githubusercontent.com/itconor/SignalScope/main"

INSTALL_ROOT_DEFAULT="/opt/signalscope"
DATA_ROOT_DEFAULT="/var/lib/signalscope"
LOG_ROOT_DEFAULT="/var/log/signalscope"

ENABLE_SDR=""
ENABLE_SERVICE=""
FORCE_OVERWRITE=0
INSTALL_ROOT="${INSTALL_ROOT_DEFAULT}"

SOURCE_DIR=""
SOURCE_APP=""
TEMP_SOURCE_DIR=""
SCRIPT_PATH="${BASH_SOURCE[0]:-}"

INTERACTIVE=0
if [[ -t 0 && -t 1 ]]; then
  INTERACTIVE=1
fi

cleanup() {
  if [[ -n "${TEMP_SOURCE_DIR:-}" && -d "${TEMP_SOURCE_DIR}" ]]; then
    rm -rf "${TEMP_SOURCE_DIR}" || true
  fi
}
trap cleanup EXIT

usage() {
  cat <<EOF
${APP_NAME} installer

Interactive:
  /bin/bash <(curl -fsSL https://your-url/install_signalscope.sh)

Non-interactive:
  curl -fsSL https://your-url/install_signalscope.sh | bash -s -- --service --sdr

Options:
  --service                 Install and enable systemd service
  --no-service              Skip systemd service installation
  --sdr                     Install RTL-SDR tooling, pyrtlsdr, and redsea build deps
  --no-sdr                  Skip SDR support
  --install-dir <path>      Install application under this path (default: ${INSTALL_ROOT_DEFAULT})
  --force                   Overwrite existing app files in install dir
  -h, --help                Show help
EOF
}

ask_yes_no() {
  local prompt="$1"
  local default="$2"
  local reply

  if [[ "${INTERACTIVE}" -ne 1 ]]; then
    [[ "$default" == "y" ]] && return 0 || return 1
  fi

  while true; do
    if [[ "$default" == "y" ]]; then
      read -r -p "${prompt} [Y/n]: " reply || true
      reply="${reply:-Y}"
    else
      read -r -p "${prompt} [y/N]: " reply || true
      reply="${reply:-N}"
    fi
    case "${reply,,}" in
      y|yes) return 0 ;;
      n|no) return 1 ;;
      *) warn "Please answer y or n." ;;
    esac
  done
}

ask_value() {
  local prompt="$1"
  local default="$2"
  local reply

  if [[ "${INTERACTIVE}" -ne 1 ]]; then
    echo "$default"
    return 0
  fi

  read -r -p "${prompt} [${default}]: " reply || true
  echo "${reply:-$default}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --service) ENABLE_SERVICE=1; shift ;;
      --no-service) ENABLE_SERVICE=0; shift ;;
      --sdr) ENABLE_SDR=1; shift ;;
      --no-sdr) ENABLE_SDR=0; shift ;;
      --force) FORCE_OVERWRITE=1; shift ;;
      --install-dir)
        [[ $# -ge 2 ]] || { err "--install-dir requires a value"; exit 1; }
        INSTALL_ROOT="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        err "Unknown option: $1"
        usage
        exit 1
        ;;
    esac
  done
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    err "Missing required command: $1"
    exit 1
  }
}

reexec_with_sudo() {
  if [[ $EUID -ne 0 ]]; then
    need_cmd sudo
    info "Re-running installer with sudo..."
    exec sudo -E bash "$SCRIPT_PATH" "$@"
  fi
}

print_banner() {
  echo
  echo "   _____ _                   _  _____"
  echo "  / ____(_)                 | |/ ____|"
  echo " | (___  _  __ _ _ __   __ _| | (___   ___ ___  _ __   ___"
  echo "  \___ \| |/ _\` | '_ \ / _\` | |\___ \ / __/ _ \| '_ \ / _ \\"
  echo "  ____) | | (_| | | | | (_| | |____) | (_| (_) | |_) |  __/"
  echo " |_____/|_|\__, |_| |_|\__,_|_|_____/ \___\___/| .__/ \___|"
  echo "             __/ |                              | |"
  echo "            |___/                               |_|"
  echo
  echo "== ${APP_NAME} production installer =="
}

resolve_source_tree() {
  local cwd app_from_cwd legacy_from_cwd
  cwd="$(pwd)"
  app_from_cwd="${cwd}/${APP_PY_NAME}"
  legacy_from_cwd="${cwd}/${LEGACY_APP_PY}"

  if [[ -f "${app_from_cwd}" ]]; then
    SOURCE_DIR="${cwd}"
    SOURCE_APP="${app_from_cwd}"
    ok "Using local source: ${SOURCE_APP}"
    return 0
  fi

  if [[ -f "${legacy_from_cwd}" ]]; then
    SOURCE_DIR="${cwd}"
    SOURCE_APP="${legacy_from_cwd}"
    ok "Using local legacy source: ${SOURCE_APP}"
    return 0
  fi

  TEMP_SOURCE_DIR="$(mktemp -d /tmp/signalscope-src.XXXXXX)"
  info "Local app file missing; fetching SignalScope from GitHub..."

  if command -v git >/dev/null 2>&1; then
    if git clone --depth 1 "${REPO_URL}" "${TEMP_SOURCE_DIR}" >/dev/null 2>&1; then
      :
    else
      warn "Git clone failed, falling back to direct file download..."
      mkdir -p "${TEMP_SOURCE_DIR}/static"
      curl -fsSL "${RAW_BASE_URL}/${APP_PY_NAME}" -o "${TEMP_SOURCE_DIR}/${APP_PY_NAME}"
    fi
  else
    mkdir -p "${TEMP_SOURCE_DIR}/static"
    curl -fsSL "${RAW_BASE_URL}/${APP_PY_NAME}" -o "${TEMP_SOURCE_DIR}/${APP_PY_NAME}"
  fi

  if [[ ! -f "${TEMP_SOURCE_DIR}/${APP_PY_NAME}" && -f "${TEMP_SOURCE_DIR}/${LEGACY_APP_PY}" ]]; then
    cp -f "${TEMP_SOURCE_DIR}/${LEGACY_APP_PY}" "${TEMP_SOURCE_DIR}/${APP_PY_NAME}"
  fi

  if [[ ! -f "${TEMP_SOURCE_DIR}/${APP_PY_NAME}" ]]; then
    err "Failed to obtain ${APP_PY_NAME} from ${REPO_URL}"
    exit 1
  fi

  SOURCE_DIR="${TEMP_SOURCE_DIR}"
  SOURCE_APP="${TEMP_SOURCE_DIR}/${APP_PY_NAME}"
  ok "Fetched source from GitHub: ${SOURCE_APP}"
}

install_redsea() {
  if [[ "${ENABLE_SDR}" != "1" ]]; then
    return 0
  fi

  if command -v redsea >/dev/null 2>&1; then
    ok "redsea already installed at $(command -v redsea)"
    return 0
  fi

  local build_root="/tmp/redsea-build.$$"
  step "Installing redsea"
  rm -rf "${build_root}"
  git clone --depth 1 https://github.com/windytan/redsea.git "${build_root}" >/dev/null 2>&1
  meson setup "${build_root}/build" "${build_root}" --wipe >/dev/null
  meson compile -C "${build_root}/build" >/dev/null
  meson install -C "${build_root}/build" >/dev/null
  rm -rf "${build_root}"

  if command -v redsea >/dev/null 2>&1; then
    ok "redsea installed successfully"
  else
    warn "redsea installation completed but binary was not found in PATH"
  fi
}

create_service() {
  step "Installing systemd service"

  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=${APP_NAME}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
EnvironmentFile=/etc/default/${SERVICE_NAME}
WorkingDirectory=${INSTALL_ROOT}
ExecStart=${VENV_DIR}/bin/python ${TARGET_APP}
Restart=always
RestartSec=5
CPUSchedulingPolicy=rr
CPUSchedulingPriority=20
Nice=-10
LimitRTPRIO=95
LimitNICE=-20

[Install]
WantedBy=multi-user.target
EOF

  cat > /usr/local/bin/${SERVICE_NAME}-watchdog.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SERVICE="signalscope"
if ! systemctl is-active --quiet "${SERVICE}"; then
  exit 0
fi
if ! curl -sk --max-time 10 https://127.0.0.1/ >/dev/null 2>&1; then
  if ! curl -s --max-time 10 http://127.0.0.1:5000/ >/dev/null 2>&1; then
    logger -t signalscope-watchdog "Health check failed; restarting ${SERVICE}"
    systemctl restart "${SERVICE}"
  fi
fi
EOF
  chmod +x /usr/local/bin/${SERVICE_NAME}-watchdog.sh

  cat > "/etc/systemd/system/${SERVICE_NAME}-watchdog.service" <<EOF
[Unit]
Description=${APP_NAME} watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/bin/${SERVICE_NAME}-watchdog.sh
EOF

  cat > "/etc/systemd/system/${SERVICE_NAME}-watchdog.timer" <<EOF
[Unit]
Description=Run ${APP_NAME} watchdog every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=${SERVICE_NAME}-watchdog.service

[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}.service"
  systemctl enable --now "${SERVICE_NAME}-watchdog.timer"
  ok "Service enabled: ${SERVICE_NAME}"
}

main() {
  print_banner
  parse_args "$@"

  need_cmd bash
  need_cmd python3
  need_cmd install
  need_cmd curl

  if [[ $EUID -eq 0 ]]; then
    err "Please run this installer as a normal user with sudo access, not as root."
    exit 1
  fi

  # If launched via curl pipe, there is no reusable script path for sudo re-exec.
  # In that case require the caller to use sudo on the outside or process substitution.
  if [[ ! -f "${SCRIPT_PATH}" ]]; then
    warn "Installer appears to be running from stdin."
    warn "For interactive prompts use:"
    warn "  /bin/bash <(curl -fsSL https://your-url/install_signalscope.sh)"
    warn "For non-interactive curl|bash use:"
    warn "  curl -fsSL https://your-url/install_signalscope.sh | bash -s -- --service --sdr"
    exec sudo -E bash -s -- "$@" < /dev/stdin
  fi

  if [[ -z "${ENABLE_SERVICE}" ]]; then
    if ask_yes_no "Install and enable the systemd service?" "y"; then
      ENABLE_SERVICE=1
    else
      ENABLE_SERVICE=0
    fi
  fi

  if [[ -z "${ENABLE_SDR}" ]]; then
    if ask_yes_no "Install SDR support (rtl-sdr, pyrtlsdr, redsea, welle.io)?" "n"; then
      ENABLE_SDR=1
    else
      ENABLE_SDR=0
    fi
  fi

  INSTALL_ROOT="$(ask_value "Install directory" "${INSTALL_ROOT}")"

  echo
  info "OS: $(. /etc/os-release && echo "${PRETTY_NAME:-Linux}")"
  info "Arch: $(dpkg --print-architecture 2>/dev/null || uname -m)"
  info "Install dir: ${INSTALL_ROOT}"
  info "Install service: $([[ "${ENABLE_SERVICE}" == "1" ]] && echo yes || echo no)"
  info "Install SDR: $([[ "${ENABLE_SDR}" == "1" ]] && echo yes || echo no)"
  if [[ "${INTERACTIVE}" == "1" ]]; then
    echo
    if ! ask_yes_no "Continue with installation?" "y"; then
      warn "Cancelled."
      exit 0
    fi
  fi

  # escalate after prompts
  reexec_with_sudo "$@"

  SERVICE_USER="${SUDO_USER:-$USER}"
  SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  IS_ARM=0
  case "$ARCH" in
    armhf|arm64|aarch64) IS_ARM=1 ;;
  esac

  step "Installing apt packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y \
    python3 python3-venv python3-pip python3-dev python3-setuptools \
    build-essential pkg-config git curl wget ca-certificates rsync \
    ffmpeg ethtool net-tools iproute2 jq \
    libffi-dev libssl-dev meson ninja-build

  if [[ "${ENABLE_SDR}" == "1" ]]; then
    step "Installing SDR apt packages"
    apt-get install -y \
      rtl-sdr librtlsdr-dev welle.io \
      libsndfile1 libsndfile1-dev libliquid-dev libfftw3-dev nlohmann-json3-dev || true
  fi

  resolve_source_tree

  step "Preparing directories"
  mkdir -p "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"

  VENV_DIR="${INSTALL_ROOT}/venv"
  TARGET_APP="${INSTALL_ROOT}/${APP_PY_NAME}"

  step "Installing application files"
  if [[ -f "${TARGET_APP}" && "${FORCE_OVERWRITE}" -ne 1 ]]; then
    warn "Existing ${TARGET_APP} found; keeping it. Re-run with --force to overwrite."
  else
    install -m 0644 "${SOURCE_APP}" "/tmp/${APP_PY_NAME}.$$"
    mv "/tmp/${APP_PY_NAME}.$$" "${TARGET_APP}"
    chown "${SERVICE_USER}:${SERVICE_GROUP}" "${TARGET_APP}"
    ok "Installed ${TARGET_APP}"
  fi

  if [[ -d "${SOURCE_DIR}/static" ]]; then
    mkdir -p "${INSTALL_ROOT}/static"
    rsync -a --delete "${SOURCE_DIR}/static/" "${INSTALL_ROOT}/static/"
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/static"
    ok "Copied static assets"
  else
    warn "No static/ directory found in source tree; continuing without copied assets."
  fi

  step "Creating Python virtual environment"
  python3 -m venv "${VENV_DIR}"
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"

  step "Installing Python packages"
  python -m pip install --upgrade "pip<25" wheel "setuptools<81"
  python -m pip install \
    flask waitress cheroot numpy scipy requests certifi cryptography

  step "Installing ONNX stack"
  if ! python -m pip install onnx; then
    warn "Failed to install onnx"
  fi
  if ! python -m pip install onnxruntime; then
    if [[ $IS_ARM -eq 1 ]]; then
      warn "onnxruntime wheel may be unavailable on this ARM platform; AI features may be unavailable."
    else
      warn "Failed to install onnxruntime"
    fi
  fi

  if [[ "${ENABLE_SDR}" == "1" ]]; then
    step "Installing SDR Python packages"
    python -m pip install --upgrade "pyrtlsdr==0.2.93"
    python - <<'PYEOF'
import sys
try:
    import rtlsdr
    version = getattr(rtlsdr, "__version__", "unknown")
    print(f"[OK] pyrtlsdr version: {version}")
except Exception as exc:
    print(f"[WARN] pyrtlsdr import check failed: {exc}")
    sys.exit(1)
PYEOF
    install_redsea
  fi

  step "Applying network tuning"
  cat > /etc/sysctl.d/99-signalscope-network.conf <<'EOF'
net.core.rmem_max=536870912
net.core.rmem_default=536870912
net.core.wmem_max=536870912
net.core.wmem_default=536870912
net.ipv4.udp_rmem_min=1048576
net.ipv4.udp_wmem_min=1048576
net.core.netdev_max_backlog=750000
net.core.optmem_max=65536
EOF
  sysctl --system >/dev/null || true

  DEFAULT_IFACE="$(ip route 2>/dev/null | awk '/default/ {print $5; exit}')"
  if [[ -n "${DEFAULT_IFACE:-}" ]]; then
    info "Applying best-effort NIC tuning to ${DEFAULT_IFACE}"
    ethtool -K "${DEFAULT_IFACE}" gro off gso off tso off lro off >/dev/null 2>&1 || true
    ethtool -G "${DEFAULT_IFACE}" rx 4096 tx 4096 >/dev/null 2>&1 || true
  fi

  step "Setting Python low-port capability"
  PYBIN="$(readlink -f "$(command -v python3)")"
  if [[ -f "${PYBIN}" ]]; then
    setcap cap_net_bind_service=+ep "${PYBIN}" || true
  fi

  if getent group plugdev >/dev/null 2>&1; then
    usermod -aG plugdev "${SERVICE_USER}" || true
  fi

  step "Writing environment file"
  cat > /etc/default/${SERVICE_NAME} <<EOF
SIGNALSCOPE_INSTALL_DIR=${INSTALL_ROOT}
SIGNALSCOPE_DATA_DIR=${DATA_ROOT_DEFAULT}
SIGNALSCOPE_LOG_DIR=${LOG_ROOT_DEFAULT}
EOF

  if [[ "${ENABLE_SERVICE}" == "1" ]]; then
    create_service
  fi

  echo
  ok "Installation complete"
  echo "Installed app: ${TARGET_APP}"
  echo "Virtualenv: ${VENV_DIR}"
  echo "Data dir: ${DATA_ROOT_DEFAULT}"
  echo "Log dir: ${LOG_ROOT_DEFAULT}"
  if [[ "${ENABLE_SERVICE}" == "1" ]]; then
    echo "Service enabled: ${SERVICE_NAME}"
    echo "Status: systemctl status ${SERVICE_NAME}"
    echo "Logs: journalctl -fu ${SERVICE_NAME}"
  else
    echo "Run manually with:"
    echo "  source \"${VENV_DIR}/bin/activate\" && python \"${TARGET_APP}\""
  fi
}

main "$@"
