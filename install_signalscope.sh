#!/usr/bin/env bash
set -euo pipefail

APP_NAME="SignalScope"
SERVICE_NAME="signalscope"
APP_PY_NAME="signalscope.py"
LEGACY_APP_PY="LivewireAIMonitor.py"
REPO_URL="https://github.com/itconor/SignalScope.git"
RAW_BASE_URL="https://raw.githubusercontent.com/itconor/SignalScope/main"

INSTALL_ROOT_DEFAULT="/opt/signalscope"
DATA_ROOT_DEFAULT="/var/lib/signalscope"
LOG_ROOT_DEFAULT="/var/log/signalscope"

ENABLE_SDR=0
ENABLE_SERVICE=0
FORCE_OVERWRITE=0
INSTALL_ROOT="${INSTALL_ROOT_DEFAULT}"

SOURCE_DIR=""
SOURCE_APP=""
TEMP_SOURCE_DIR=""

usage() {
  cat <<EOF2
Usage: $0 [--service] [--sdr] [--install-dir /opt/signalscope] [--force]

Options:
  --service                 Install and enable systemd service + watchdog
  --sdr                     Install RTL-SDR tooling, pyrtlsdr, and redsea build deps
  --install-dir <path>      Install application under this path (default: ${INSTALL_ROOT_DEFAULT})
  --force                   Overwrite existing app files in install dir
EOF2
}

cleanup() {
  if [[ -n "${TEMP_SOURCE_DIR:-}" && -d "${TEMP_SOURCE_DIR}" ]]; then
    rm -rf "${TEMP_SOURCE_DIR}" || true
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) ENABLE_SERVICE=1; shift ;;
    --sdr) ENABLE_SDR=1; shift ;;
    --force) FORCE_OVERWRITE=1; shift ;;
    --install-dir)
      INSTALL_ROOT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1"
    exit 1
  }
}

need_cmd sudo
need_cmd python3
need_cmd install

if [[ $EUID -eq 0 ]]; then
  echo "Please run this installer as a normal user with sudo access, not as root."
  exit 1
fi

SERVICE_USER="${SUDO_USER:-$USER}"
SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
OS_PRETTY="$(. /etc/os-release && echo "${PRETTY_NAME:-Linux}")"
IS_ARM=0
IS_PI=0
case "$ARCH" in
  armhf|arm64|aarch64) IS_ARM=1 ;;
esac
if grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null || grep -qi "raspbian\|raspberry pi" /etc/os-release 2>/dev/null; then
  IS_PI=1
fi

resolve_source_tree() {
  local cwd app_from_cwd legacy_from_cwd
  cwd="$(pwd)"
  app_from_cwd="${cwd}/${APP_PY_NAME}"
  legacy_from_cwd="${cwd}/${LEGACY_APP_PY}"

  if [[ -f "${app_from_cwd}" ]]; then
    SOURCE_DIR="${cwd}"
    SOURCE_APP="${app_from_cwd}"
    echo "Using local source: ${SOURCE_APP}"
    return 0
  fi

  if [[ -f "${legacy_from_cwd}" ]]; then
    SOURCE_DIR="${cwd}"
    SOURCE_APP="${legacy_from_cwd}"
    echo "Using local legacy source: ${SOURCE_APP}"
    return 0
  fi

  TEMP_SOURCE_DIR="$(mktemp -d /tmp/signalscope-src.XXXXXX)"
  echo "Local app file missing; fetching SignalScope from GitHub..."

  if command -v git >/dev/null 2>&1; then
    if git clone --depth 1 "${REPO_URL}" "${TEMP_SOURCE_DIR}" >/dev/null 2>&1; then
      :
    else
      echo "Git clone failed, falling back to direct file download..."
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
    echo "Failed to obtain ${APP_PY_NAME} from ${REPO_URL}"
    exit 1
  fi

  SOURCE_DIR="${TEMP_SOURCE_DIR}"
  SOURCE_APP="${TEMP_SOURCE_DIR}/${APP_PY_NAME}"
  echo "Fetched source from GitHub: ${SOURCE_APP}"
}

echo "== ${APP_NAME} production installer =="
echo "OS: ${OS_PRETTY}"
echo "Arch: ${ARCH}"
echo "Install dir: ${INSTALL_ROOT}"
if [[ $IS_PI -eq 1 ]]; then
  echo "Platform detected: Raspberry Pi"
fi

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip python3-dev python3-setuptools \
  build-essential pkg-config git curl wget ca-certificates rsync \
  ffmpeg ethtool net-tools iproute2 jq \
  libffi-dev libssl-dev meson ninja-build

if [[ $ENABLE_SDR -eq 1 ]]; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    rtl-sdr librtlsdr-dev welle.io \
    libsndfile1 libsndfile1-dev libliquid-dev libfftw3-dev nlohmann-json3-dev || true
fi

resolve_source_tree

sudo mkdir -p "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"
sudo chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"

VENV_DIR="${INSTALL_ROOT}/venv"
TARGET_APP="${INSTALL_ROOT}/${APP_PY_NAME}"

# Copy application
if [[ -f "${TARGET_APP}" && $FORCE_OVERWRITE -ne 1 ]]; then
  echo "Existing ${TARGET_APP} found."
  echo "Re-run with --force if you want to overwrite the installed app file."
else
  install -m 0644 "${SOURCE_APP}" "/tmp/${APP_PY_NAME}.$$"
  sudo mv "/tmp/${APP_PY_NAME}.$$" "${TARGET_APP}"
  sudo chown "${SERVICE_USER}:${SERVICE_GROUP}" "${TARGET_APP}"
fi

# Copy optional static assets if present locally or from fetched repo
if [[ -d "${SOURCE_DIR}/static" ]]; then
  sudo mkdir -p "${INSTALL_ROOT}/static"
  sudo rsync -a --delete "${SOURCE_DIR}/static/" "${INSTALL_ROOT}/static/"
  sudo chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/static"
else
  echo "No static/ directory found in source tree; continuing without copied assets."
fi

# Create venv if needed
python3 -m venv "${VENV_DIR}"
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade "pip<25" wheel "setuptools<81"

python -m pip install \
  flask waitress cheroot numpy scipy requests certifi

install_onnx_stack() {
  local onnx_ok=0
  local ort_ok=0
  if python -m pip install onnx; then
    onnx_ok=1
  else
    echo "WARN: Failed to install onnx."
  fi
  if python -m pip install onnxruntime; then
    ort_ok=1
  else
    echo "WARN: Failed to install onnxruntime."
  fi
  if [[ $IS_ARM -eq 1 && $ort_ok -eq 0 ]]; then
    echo "INFO: ARM platform detected; onnxruntime wheel may be unavailable."
    echo "INFO: ${APP_NAME} will still install, but AI/ONNX features may be unavailable until onnxruntime is installed manually."
  fi
  if [[ $onnx_ok -eq 0 ]]; then
    echo "WARN: onnx not installed."
  fi
}
install_onnx_stack

if [[ $ENABLE_SDR -eq 1 ]]; then
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
fi

install_redsea() {
  if ! [[ $ENABLE_SDR -eq 1 ]]; then
    return 0
  fi

  if command -v redsea >/dev/null 2>&1; then
    echo "INFO: redsea already installed at $(command -v redsea)"
    return 0
  fi

  local build_root="/tmp/redsea-build.$$"
  echo "Installing redsea..."
  rm -rf "${build_root}"
  git clone --depth 1 https://github.com/windytan/redsea.git "${build_root}"
  meson setup "${build_root}/build" "${build_root}" --wipe
  meson compile -C "${build_root}/build"
  sudo meson install -C "${build_root}/build"
  rm -rf "${build_root}"

  if command -v redsea >/dev/null 2>&1; then
    echo "INFO: redsea installed successfully."
  else
    echo "WARN: redsea installation completed but binary was not found in PATH."
  fi
}

install_redsea

# Network tuning
sudo tee /etc/sysctl.d/99-signalscope-network.conf > /dev/null <<'EOF2'
net.core.rmem_max=536870912
net.core.rmem_default=536870912
net.core.wmem_max=536870912
net.core.wmem_default=536870912
net.ipv4.udp_rmem_min=1048576
net.ipv4.udp_wmem_min=1048576
net.core.netdev_max_backlog=750000
net.core.optmem_max=65536
EOF2
sudo sysctl --system >/dev/null || true

DEFAULT_IFACE="$(ip route 2>/dev/null | awk '/default/ {print $5; exit}')"
if [[ -n "${DEFAULT_IFACE:-}" ]]; then
  echo "Applying best-effort NIC tuning to ${DEFAULT_IFACE}"
  sudo ethtool -K "${DEFAULT_IFACE}" gro off gso off tso off lro off >/dev/null 2>&1 || true
  sudo ethtool -G "${DEFAULT_IFACE}" rx 4096 tx 4096 >/dev/null 2>&1 || true
fi

# Capability for low ports, best effort
PYBIN="$(readlink -f "$(command -v python3)")"
if [[ -f "${PYBIN}" ]]; then
  sudo setcap cap_net_bind_service=+ep "${PYBIN}" || true
fi

# SDR access group
if getent group plugdev >/dev/null 2>&1; then
  sudo usermod -aG plugdev "${SERVICE_USER}" || true
fi

# Create env file used by service
sudo tee /etc/default/${SERVICE_NAME} > /dev/null <<EOF2
SIGNALSCOPE_INSTALL_DIR=${INSTALL_ROOT}
SIGNALSCOPE_DATA_DIR=${DATA_ROOT_DEFAULT}
SIGNALSCOPE_LOG_DIR=${LOG_ROOT_DEFAULT}
EOF2

create_service() {
  sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF2
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
EOF2

  sudo tee /usr/local/bin/${SERVICE_NAME}-watchdog.sh > /dev/null <<'EOF2'
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
EOF2
  sudo chmod +x /usr/local/bin/${SERVICE_NAME}-watchdog.sh

  sudo tee "/etc/systemd/system/${SERVICE_NAME}-watchdog.service" > /dev/null <<EOF2
[Unit]
Description=${APP_NAME} watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/bin/${SERVICE_NAME}-watchdog.sh
EOF2

  sudo tee "/etc/systemd/system/${SERVICE_NAME}-watchdog.timer" > /dev/null <<EOF2
[Unit]
Description=Run ${APP_NAME} watchdog every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=${SERVICE_NAME}-watchdog.service

[Install]
WantedBy=timers.target
EOF2

  sudo systemctl daemon-reload
  sudo systemctl enable --now "${SERVICE_NAME}.service"
  sudo systemctl enable --now "${SERVICE_NAME}-watchdog.timer"
}

if [[ $ENABLE_SERVICE -eq 1 ]]; then
  create_service
fi

echo
echo "Installation complete."
echo "Installed app: ${TARGET_APP}"
echo "Virtualenv: ${VENV_DIR}"
echo "Data dir: ${DATA_ROOT_DEFAULT}"
echo "Log dir: ${LOG_ROOT_DEFAULT}"

if [[ $ENABLE_SERVICE -eq 1 ]]; then
  echo "Service enabled: ${SERVICE_NAME}"
  echo "Status: systemctl status ${SERVICE_NAME}"
  echo "Logs: journalctl -fu ${SERVICE_NAME}"
else
  echo "Run manually with:"
  echo "  source \"${VENV_DIR}/bin/activate\" && python \"${TARGET_APP}\""
fi

if [[ $ENABLE_SDR -eq 1 ]]; then
  echo
  echo "SDR components:"
  echo "  rtl_fm: $(command -v rtl_fm || echo not found)"
  echo "  redsea: $(command -v redsea || echo not found)"
  echo "Recommended FM URL format:"
  echo "  fm://99.7?serial=DAB1&ppm=-52&gain=38&backend=rtl_fm"
fi

if [[ $IS_ARM -eq 1 ]]; then
  echo
  echo "ARM note: onnxruntime may not be available automatically on all Raspberry Pi OS builds."
fi
