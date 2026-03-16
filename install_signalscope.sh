#!/usr/bin/env bash
set -euo pipefail

APP_NAME="SignalScope"
SERVICE_NAME="signalscope"
APP_PY_NAME="signalscope.py"
LEGACY_APP_PY="LivewireAIMonitor.py"

INSTALL_ROOT_DEFAULT="/opt/signalscope"
DATA_ROOT_DEFAULT="/var/lib/signalscope"
LOG_ROOT_DEFAULT="/var/log/signalscope"

ENABLE_SDR=0
ENABLE_SERVICE=0
FORCE_OVERWRITE=0
INSTALL_ROOT="${INSTALL_ROOT_DEFAULT}"

usage() {
  cat <<EOF
Usage: $0 [--service] [--sdr] [--install-dir /opt/signalscope] [--force]

Options:
  --service                 Install and enable systemd service + watchdog
  --sdr                     Install RTL-SDR tooling and pyrtlsdr
  --install-dir <path>      Install application under this path (default: ${INSTALL_ROOT_DEFAULT})
  --force                   Overwrite existing app files in install dir
EOF
}

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

SOURCE_DIR="$(pwd)"
SOURCE_APP=""
if [[ -f "${SOURCE_DIR}/${APP_PY_NAME}" ]]; then
  SOURCE_APP="${SOURCE_DIR}/${APP_PY_NAME}"
elif [[ -f "${SOURCE_DIR}/${LEGACY_APP_PY}" ]]; then
  SOURCE_APP="${SOURCE_DIR}/${LEGACY_APP_PY}"
else
  echo "Could not find ${APP_PY_NAME} or ${LEGACY_APP_PY} in ${SOURCE_DIR}"
  exit 1
fi

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
  build-essential pkg-config git curl wget ca-certificates \
  ffmpeg ethtool net-tools iproute2 jq \
  libffi-dev libssl-dev

if [[ $ENABLE_SDR -eq 1 ]]; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    rtl-sdr librtlsdr-dev welle.io || true
fi

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

# Copy optional static assets if present
if [[ -d "${SOURCE_DIR}/static" ]]; then
  sudo mkdir -p "${INSTALL_ROOT}/static"
  sudo rsync -a --delete "${SOURCE_DIR}/static/" "${INSTALL_ROOT}/static/" >/dev/null 2>&1 || {
    sudo cp -a "${SOURCE_DIR}/static/." "${INSTALL_ROOT}/static/" || true
  }
  sudo chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/static"
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
  python -m pip install "pyrtlsdr==0.2.93"
fi

# Network tuning
sudo tee /etc/sysctl.d/99-signalscope-network.conf > /dev/null <<'EOF'
net.core.rmem_max=536870912
net.core.rmem_default=536870912
net.core.wmem_max=536870912
net.core.wmem_default=536870912
net.ipv4.udp_rmem_min=1048576
net.ipv4.udp_wmem_min=1048576
net.core.netdev_max_backlog=750000
net.core.optmem_max=65536
EOF
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
sudo tee /etc/default/${SERVICE_NAME} > /dev/null <<EOF
SIGNALSCOPE_INSTALL_DIR=${INSTALL_ROOT}
SIGNALSCOPE_DATA_DIR=${DATA_ROOT_DEFAULT}
SIGNALSCOPE_LOG_DIR=${LOG_ROOT_DEFAULT}
EOF

create_service() {
  sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
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

  sudo tee /usr/local/bin/${SERVICE_NAME}-watchdog.sh > /dev/null <<'EOF'
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
  sudo chmod +x /usr/local/bin/${SERVICE_NAME}-watchdog.sh

  sudo tee "/etc/systemd/system/${SERVICE_NAME}-watchdog.service" > /dev/null <<EOF
[Unit]
Description=${APP_NAME} watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/bin/${SERVICE_NAME}-watchdog.sh
EOF

  sudo tee "/etc/systemd/system/${SERVICE_NAME}-watchdog.timer" > /dev/null <<EOF
[Unit]
Description=Run ${APP_NAME} watchdog every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=${SERVICE_NAME}-watchdog.service

[Install]
WantedBy=timers.target
EOF

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

if [[ $IS_ARM -eq 1 ]]; then
  echo
  echo "ARM note: onnxruntime may not be available automatically on all Raspberry Pi OS builds."
fi
