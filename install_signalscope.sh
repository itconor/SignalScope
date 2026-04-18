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

LEGACY_SERVICE_NAME="livewireaimonitor"
LEGACY_INSTALL_DETECTED=0
LEGACY_WORKDIR=""
LEGACY_APP_PATH=""

ENABLE_SDR=""
ENABLE_SERVICE=""
ENABLE_NGINX=""
ENABLE_LIVEWIRE=""
ENABLE_ICECAST=""
FORCE_RETUNE=0
NGINX_FQDN=""
NGINX_FQDN_DEFAULT=""   # pre-filled from a broken/existing config during repair
NGINX_HTTPS=""
ENABLE_OVERCLOCK=""
FORCE_OVERWRITE=0
INSTALL_ROOT="${INSTALL_ROOT_DEFAULT}"
APP_BACKUP=""

PI_MODEL_STR=""
PI_GEN=0

SOURCE_DIR=""
SOURCE_APP=""
TEMP_SOURCE_DIR=""
SELF_COPY=""
SCRIPT_PATH="${BASH_SOURCE[0]:-}"

INTERACTIVE=0
if [[ -t 0 && -t 1 ]]; then
  INTERACTIVE=1
fi

cleanup() {
  if [[ -n "${TEMP_SOURCE_DIR:-}" && -d "${TEMP_SOURCE_DIR}" ]]; then
    rm -rf "${TEMP_SOURCE_DIR}" || true
  fi
  if [[ -n "${SELF_COPY:-}" && -f "${SELF_COPY}" ]]; then
    rm -f "${SELF_COPY}" || true
  fi
}
trap cleanup EXIT

usage() {
  cat <<EOF
${APP_NAME} installer

Interactive:
  /bin/bash <(curl -fsSL https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh)

Non-interactive:
  curl -fsSL https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh | bash -s -- --service --sdr

Options:
  --service                 Install and enable systemd service
  --no-service              Skip systemd service installation
  --sdr                     Install RTL-SDR tooling, pyrtlsdr, and redsea build deps
  --no-sdr                  Skip SDR support
  --livewire                Apply Livewire/AES67 UDP kernel tuning (2× standard buffer sizes)
  --no-livewire             Apply standard UDP tuning only
  --icecast                 Install Icecast2 (for the Icecast Streaming plugin)
  --no-icecast              Skip Icecast2 installation
  --retune                  Re-apply kernel network tuning even on an update (combine with --livewire)
  --nginx                   Install and configure nginx reverse proxy
  --no-nginx                Skip nginx setup
  --fqdn <hostname>         Fully-qualified domain name for nginx vhost and TLS cert
  --https                   Request a Let's Encrypt certificate via certbot (requires --fqdn)
  --no-https                Skip Let's Encrypt even if --fqdn is set
  --install-dir <path>      Install application under this path (default: ${INSTALL_ROOT_DEFAULT})
  --force                   Overwrite existing app files in install dir
  --pi-overclock            Apply Raspberry Pi overclock settings (requires reboot)
  --no-pi-overclock         Skip Raspberry Pi overclock prompt
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


detect_existing_proxy_setup() {
  local conf="/etc/nginx/sites-available/signalscope"
  if [[ -f "$conf" ]] || [[ -L "/etc/nginx/sites-enabled/signalscope" ]]; then
    return 0
  fi
  return 1
}

detect_legacy_livewire_install() {
  LEGACY_INSTALL_DETECTED=0
  LEGACY_WORKDIR=""
  LEGACY_APP_PATH=""

  local svc="/etc/systemd/system/${LEGACY_SERVICE_NAME}.service"
  [[ -f "${svc}" ]] || svc="/lib/systemd/system/${LEGACY_SERVICE_NAME}.service"
  if [[ ! -f "${svc}" ]]; then
    return 1
  fi

  LEGACY_INSTALL_DETECTED=1
  LEGACY_WORKDIR="$(awk -F= '/^WorkingDirectory=/{print $2; exit}' "${svc}" | xargs)"
  LEGACY_APP_PATH="$(awk -F= '/^ExecStart=/{print $2; exit}' "${svc}" | sed 's/.*python[0-9.]*[[:space:]]\+//' | awk '{print $1}' | xargs)"

  if [[ -z "${LEGACY_WORKDIR}" && -n "${LEGACY_APP_PATH}" ]]; then
    LEGACY_WORKDIR="$(dirname "${LEGACY_APP_PATH}")"
  fi
  return 0
}

migrate_legacy_livewire_install() {
  if [[ "${LEGACY_INSTALL_DETECTED}" != "1" ]]; then
    return 0
  fi

  step "Migrating legacy LivewireAIMonitor install"
  warn "Legacy ${LEGACY_SERVICE_NAME}.service detected. Treating this as a fresh SignalScope install."

  local svc="/etc/systemd/system/${LEGACY_SERVICE_NAME}.service"
  [[ -f "${svc}" ]] || svc="/lib/systemd/system/${LEGACY_SERVICE_NAME}.service"

  # Stop/disable old service before copying anything.
  ${SUDO} systemctl stop "${LEGACY_SERVICE_NAME}" >/dev/null 2>&1 || true
  ${SUDO} systemctl disable "${LEGACY_SERVICE_NAME}" >/dev/null 2>&1 || true

  # Copy legacy config / models / snippets if they exist.
  local legacy_root="${LEGACY_WORKDIR}"
  local copied_any=0

  ${SUDO} mkdir -p "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"

  for candidate in \
    "${legacy_root}/config.json" \
    "${legacy_root}/alert_log.json" \
    "${legacy_root}/hub_state.json"; do
    if [[ -n "${candidate}" && -f "${candidate}" ]]; then
      ${SUDO} cp -an "${candidate}" "${INSTALL_ROOT}/" || true
      copied_any=1
    fi
  done

  for dir_candidate in \
    "${legacy_root}/ai_models" \
    "${legacy_root}/models" \
    "${legacy_root}/alert_snippets" \
    "${legacy_root}/static"; do
    if [[ -n "${dir_candidate}" && -d "${dir_candidate}" ]]; then
      ${SUDO} mkdir -p "${INSTALL_ROOT}/$(basename "${dir_candidate}")"
      ${SUDO} rsync -a "${dir_candidate}/" "${INSTALL_ROOT}/$(basename "${dir_candidate}")/" || true
      copied_any=1
    fi
  done

  # Remove the old service unit so the new branded service owns the machine.
  if [[ -f "/etc/systemd/system/${LEGACY_SERVICE_NAME}.service" ]]; then
    ${SUDO} rm -f "/etc/systemd/system/${LEGACY_SERVICE_NAME}.service"
  fi
  ${SUDO} rm -f "/etc/systemd/system/multi-user.target.wants/${LEGACY_SERVICE_NAME}.service" || true
  ${SUDO} systemctl daemon-reload

  if [[ "${copied_any}" == "1" ]]; then
    ok "Copied legacy config / AI models into ${INSTALL_ROOT}"
  else
    warn "Legacy service found, but no old config or model folders were detected to migrate"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --service) ENABLE_SERVICE=1; shift ;;
      --no-service) ENABLE_SERVICE=0; shift ;;
      --sdr) ENABLE_SDR=1; shift ;;
      --no-sdr) ENABLE_SDR=0; shift ;;
      --nginx) ENABLE_NGINX=1; shift ;;
      --no-nginx) ENABLE_NGINX=0; shift ;;
      --fqdn)
        [[ $# -ge 2 ]] || { err "--fqdn requires a value"; exit 1; }
        NGINX_FQDN="$2"; shift 2 ;;
      --https) NGINX_HTTPS=1; shift ;;
      --no-https) NGINX_HTTPS=0; shift ;;
      --force) FORCE_OVERWRITE=1; shift ;;
      --livewire) ENABLE_LIVEWIRE=1; shift ;;
      --no-livewire) ENABLE_LIVEWIRE=0; shift ;;
      --icecast) ENABLE_ICECAST=1; shift ;;
      --no-icecast) ENABLE_ICECAST=0; shift ;;
      --retune) FORCE_RETUNE=1; shift ;;
      --pi-overclock) ENABLE_OVERCLOCK=1; shift ;;
      --no-pi-overclock) ENABLE_OVERCLOCK=0; shift ;;
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

make_rerunnable_copy_if_needed() {
  if [[ -f "${SCRIPT_PATH}" && "${SCRIPT_PATH}" != /dev/fd/* && "${SCRIPT_PATH}" != /proc/self/fd/* ]]; then
    echo "${SCRIPT_PATH}"
    return 0
  fi

  SELF_COPY="$(mktemp /tmp/signalscope-installer.XXXXXX.sh)"
  chmod 700 "${SELF_COPY}"

  if [[ -r "${SCRIPT_PATH}" ]]; then
    cat "${SCRIPT_PATH}" > "${SELF_COPY}"
  else
    err "Could not read installer source for sudo re-launch."
    err "As a workaround, save the script to a file and run it locally."
    exit 1
  fi
  echo "${SELF_COPY}"
}

# Set the SUDO variable immediately (no password prompt yet).
# Call auth_sudo later, right before any privileged commands run.
init_sudo() {
  if [[ $EUID -ne 0 ]]; then
    need_cmd sudo
    SUDO="sudo"
  else
    SUDO=""
  fi
}

# Prompt for the sudo password (if needed).  Call this after all interactive
# questions have been answered so the password prompt doesn't appear in the
# middle of unrelated prompts.
auth_sudo() {
  if [[ -n "${SUDO}" ]]; then
    info "Requesting sudo access..."
    sudo -v
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

ensure_rtlsdr_blacklist() {
  local bl="/etc/modprobe.d/blacklist-rtlsdr.conf"
  if [[ ! -f "${bl}" ]]; then
    ${SUDO} tee "${bl}" > /dev/null <<'EOF'
# Prevent DVB-T kernel driver from claiming the RTL-SDR dongle
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
    ok "RTL-SDR kernel module blacklist written"
  fi
}

ensure_usbfs_unlimited() {
  # Running multiple RTL-SDR dongles simultaneously requires more USB kernel
  # buffer memory than the Linux default (16 MB).  Without this, rtl_fm prints
  # "Failed to allocate zero-copy buffer" and "Failed to submit transfer 0"
  # and the dongle produces no audio.  Setting usbfs_memory_mb=0 removes the
  # cap entirely (matches the official rtl-sdr / Raspberry Pi guidance).
  #
  # 1. Apply immediately (takes effect now, before any reboot).
  if [[ -f /sys/module/usbcore/parameters/usbfs_memory_mb ]]; then
    echo 0 | ${SUDO} tee /sys/module/usbcore/parameters/usbfs_memory_mb > /dev/null
  fi

  # 2. Persist across reboots via /etc/rc.local (works on Pi OS / Debian).
  local rc="/etc/rc.local"
  local marker="usbfs_memory_mb"
  if ! grep -q "${marker}" "${rc}" 2>/dev/null; then
    if [[ -f "${rc}" ]]; then
      # Insert before the final 'exit 0' line
      ${SUDO} sed -i "/^exit 0/i echo 0 > /sys/module/usbcore/parameters/usbfs_memory_mb" "${rc}"
    else
      ${SUDO} tee "${rc}" > /dev/null <<'EOF'
#!/bin/sh -e
# Set unlimited USB buffer memory — required for multiple RTL-SDR dongles
echo 0 > /sys/module/usbcore/parameters/usbfs_memory_mb
exit 0
EOF
      ${SUDO} chmod +x "${rc}"
    fi
    ok "usbfs_memory_mb=0 written to ${rc} (persists across reboots)"
  else
    ok "usbfs_memory_mb already configured in ${rc}"
  fi
}

detect_pi_model() {
  PI_MODEL_STR=""
  PI_GEN=0
  [[ -f /proc/device-tree/model ]] || return 1
  PI_MODEL_STR="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
  [[ "${PI_MODEL_STR}" == *"Raspberry Pi"* ]] || { PI_MODEL_STR=""; return 1; }
  if   [[ "${PI_MODEL_STR}" == *"Raspberry Pi 5"* ]]; then PI_GEN=5
  elif [[ "${PI_MODEL_STR}" == *"Raspberry Pi 4"* ]]; then PI_GEN=4
  elif [[ "${PI_MODEL_STR}" == *"Raspberry Pi 3"* ]]; then PI_GEN=3
  elif [[ "${PI_MODEL_STR}" == *"Raspberry Pi 2"* ]]; then PI_GEN=2
  else PI_GEN=1
  fi
  return 0
}

apply_pi_overclock() {
  local boot_config=""
  if   [[ -f /boot/firmware/config.txt ]]; then boot_config="/boot/firmware/config.txt"
  elif [[ -f /boot/config.txt         ]]; then boot_config="/boot/config.txt"
  else
    warn "Cannot find /boot/firmware/config.txt or /boot/config.txt — skipping overclock"
    return 1
  fi

  # Idempotent — don't apply twice
  if grep -q "# SignalScope overclock" "${boot_config}" 2>/dev/null; then
    ok "Raspberry Pi overclock already applied in ${boot_config}"
    return 0
  fi

  local oc_block=""
  case "${PI_GEN}" in
    4)
      info "Pi 4: arm_freq=2000 MHz, over_voltage=6, gpu_freq=750 (stock 1500 MHz) — requires heatsink"
      oc_block=$'over_voltage=6\narm_freq=2000\ngpu_freq=750'
      ;;
    3)
      info "Pi 3: arm_freq=1450 MHz, over_voltage=2, gpu_freq=500 (stock 1200–1400 MHz) — requires heatsink"
      oc_block=$'over_voltage=2\narm_freq=1450\ngpu_freq=500'
      ;;
    *)
      warn "Overclocking not configured for Pi generation ${PI_GEN} — skipping"
      return 0
      ;;
  esac

  warn "Overclocking requires adequate cooling. Ensure a heatsink (and fan for Pi 4/5) is fitted."
  warn "A reboot is required for the overclock to take effect."

  ${SUDO} tee -a "${boot_config}" > /dev/null <<EOF

# SignalScope overclock — applied $(date '+%Y-%m-%d')
${oc_block}
EOF
  ok "Overclock settings written to ${boot_config} — reboot to apply"
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
  if ! sudo meson install -C "${build_root}/build" >/dev/null; then
    err "redsea install failed"
    rm -rf "${build_root}"
    exit 1
  fi
  rm -rf "${build_root}"

  if command -v redsea >/dev/null 2>&1; then
    ok "redsea installed successfully"
  else
    warn "redsea installation completed but binary was not found in PATH"
  fi
}

# ── Version helpers ───────────────────────────────────────────────────────────

# Extract "X.Y.Z" from the BUILD line in a signalscope.py file.
extract_build_version() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  grep -oP '(?<=SignalScope-)\d+\.\d+\.\d+' "$file" 2>/dev/null | head -1 || true
}

# Returns 0 (true) if $1 > $2 (both "X.Y.Z"). Empty treated as 0.0.0.
version_gt() {
  python3 - <<PYEOF
a = tuple(int(x) for x in ('${1:-0.0.0}'.split('.')))
b = tuple(int(x) for x in ('${2:-0.0.0}'.split('.')))
import sys; sys.exit(0 if a > b else 1)
PYEOF
}

# Global state set by resolve_best_source
INSTALLED_VER=""
WINNING_VER=""
WINNING_SOURCE=""  # "installed" | "local" | "remote"
IS_UPDATE=0

# Compare installed / local CWD / remote GitHub versions and pick the highest.
# Sets SOURCE_DIR, SOURCE_APP, INSTALLED_VER, WINNING_VER, WINNING_SOURCE, IS_UPDATE.
resolve_best_source() {
  local cwd installed_app local_app remote_app fetch_ok local_ver remote_ver

  cwd="$(pwd)"
  installed_app="${INSTALL_ROOT}/${APP_PY_NAME}"
  local_app="${cwd}/${APP_PY_NAME}"

  # 1. Installed version
  INSTALLED_VER=$(extract_build_version "${installed_app}")
  if [[ -n "${INSTALLED_VER}" ]]; then
    info "Installed version : ${INSTALLED_VER}  (${installed_app})"
    IS_UPDATE=1
  else
    info "No existing installation found at ${installed_app}"
  fi

  # 2. Local source version (CWD)
  local_ver=$(extract_build_version "${local_app}")
  [[ -n "${local_ver}" ]] && info "Local file version: ${local_ver}  (${local_app})"

  # 3. Remote version from GitHub
  step "Checking remote version on GitHub"
  TEMP_SOURCE_DIR="$(mktemp -d /tmp/signalscope-src.XXXXXX)"
  remote_app="${TEMP_SOURCE_DIR}/${APP_PY_NAME}"
  fetch_ok=0

  if command -v git >/dev/null 2>&1; then
    if git clone --depth 1 "${REPO_URL}" "${TEMP_SOURCE_DIR}" >/dev/null 2>&1; then
      fetch_ok=1
    else
      warn "git clone failed, trying direct download"
    fi
  fi
  if [[ "${fetch_ok}" -eq 0 ]]; then
    mkdir -p "${TEMP_SOURCE_DIR}"
    if curl -fsSL --max-time 30 "${RAW_BASE_URL}/${APP_PY_NAME}" -o "${remote_app}" 2>/dev/null; then
      fetch_ok=1
      # Also fetch static assets (curl doesn't get the full repo)
      mkdir -p "${TEMP_SOURCE_DIR}/static"
      for asset in signalscope_icon.ico signalscope_icon.png signalscope_logo.jpg signalscope_logo.png; do
        curl -fsSL --max-time 10 "${RAW_BASE_URL}/static/${asset}" -o "${TEMP_SOURCE_DIR}/static/${asset}" 2>/dev/null || true
      done
    else
      warn "Could not fetch remote file from GitHub"
    fi
  fi

  remote_ver=""
  if [[ "${fetch_ok}" -eq 1 && -f "${remote_app}" ]]; then
    remote_ver=$(extract_build_version "${remote_app}")
    [[ -n "${remote_ver}" ]] && info "Remote version     : ${remote_ver}  (GitHub)" \
                              || warn "Could not parse version from remote file"
  fi

  # 4. Pick winner (highest version)
  WINNING_VER="${INSTALLED_VER:-0.0.0}"
  WINNING_SOURCE="installed"

  # Local file wins if strictly higher version, or equal version but from a
  # different path (e.g. user is reinstalling a locally-modified copy).
  if [[ -n "${local_ver}" && "${local_app}" != "${installed_app}" ]]; then
    if version_gt "${local_ver}" "${WINNING_VER}" || [[ "${local_ver}" == "${WINNING_VER}" ]]; then
      WINNING_VER="${local_ver}"; WINNING_SOURCE="local"
    fi
  fi
  if [[ -n "${remote_ver}" ]] && version_gt "${remote_ver}" "${WINNING_VER}"; then
    WINNING_VER="${remote_ver}"; WINNING_SOURCE="remote"
  fi

  # 5. Set SOURCE_DIR / SOURCE_APP
  case "${WINNING_SOURCE}" in
    local)
      SOURCE_DIR="${cwd}"; SOURCE_APP="${local_app}"
      if [[ -n "${INSTALLED_VER}" ]]; then
        if version_gt "${WINNING_VER}" "${INSTALLED_VER}"; then
          ok "Update available: ${INSTALLED_VER} → ${WINNING_VER}  (local file wins)"
        else
          ok "Reinstalling ${WINNING_VER} from local file (same version, local copy used)"
        fi
      else
        ok "Source: local file ${WINNING_VER}"
      fi
      ;;
    remote)
      SOURCE_DIR="${TEMP_SOURCE_DIR}"; SOURCE_APP="${remote_app}"
      [[ -n "${INSTALLED_VER}" ]] \
        && ok "Update available: ${INSTALLED_VER} → ${WINNING_VER}  (GitHub wins)" \
        || ok "Source: GitHub ${WINNING_VER}"
      ;;
    installed)
      SOURCE_DIR="${INSTALL_ROOT}"; SOURCE_APP="${installed_app}"
      ok "Already up to date: ${WINNING_VER}"
      ;;
  esac

  # Legacy filename fallback
  if [[ ! -f "${SOURCE_APP}" ]]; then
    local legacy_cwd="${cwd}/${LEGACY_APP_PY}"
    if [[ -f "${legacy_cwd}" ]]; then
      SOURCE_DIR="${cwd}"; SOURCE_APP="${legacy_cwd}"
      warn "Using legacy app file: ${SOURCE_APP}"
    elif [[ -f "${INSTALL_ROOT}/${LEGACY_APP_PY}" ]]; then
      SOURCE_DIR="${INSTALL_ROOT}"; SOURCE_APP="${INSTALL_ROOT}/${LEGACY_APP_PY}"
      warn "Using legacy installed app file: ${SOURCE_APP}"
    else
      err "No application file found — cannot continue"; exit 1
    fi
  fi
}

# ── nginx reverse proxy ───────────────────────────────────────────────────────

configure_nginx() {
  local fqdn="$1"
  local use_https="${2:-0}"

  step "Installing nginx"
  export DEBIAN_FRONTEND=noninteractive
  ${SUDO} apt-get install -y nginx

  local conf_file="/etc/nginx/sites-available/signalscope"
  local enabled_dir="/etc/nginx/sites-enabled"

  # Common stream location block (reused in both HTTP and HTTPS configs)
  local stream_loc
  stream_loc='    # Live audio — disable buffering so chunks flow immediately
    location /stream/ {
        proxy_pass             http://127.0.0.1:5000;
        proxy_buffering        off;
        proxy_cache            off;
        proxy_read_timeout     3600s;
        proxy_send_timeout     3600s;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }'

  if [[ -z "${fqdn}" ]]; then
    # HTTP-only, no FQDN — proxy on port 80 to app on 5000
    ${SUDO} tee "${conf_file}" > /dev/null <<EOF
# SignalScope nginx reverse proxy (HTTP, no FQDN)
server {
    listen 80 default_server;
    server_name _;

    # Allow large clip uploads (WAV/MP3) from client nodes
    client_max_body_size 20m;

    proxy_set_header Host              \$host;
    proxy_set_header X-Real-IP         \$remote_addr;
    proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;

    location /stream/ {
        proxy_pass          http://127.0.0.1:5000;
        proxy_buffering     off;
        proxy_cache         off;
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        proxy_pass          http://127.0.0.1:5000;
        proxy_read_timeout  120s;
        proxy_send_timeout  120s;
    }
}
EOF
  else
    # Write full config — start with HTTP-only so nginx can start even before cert
    ${SUDO} tee "${conf_file}" > /dev/null <<EOF
# SignalScope nginx reverse proxy
server {
    listen 80;
    server_name ${fqdn};

    # Allow large clip uploads (WAV/MP3) from client nodes
    client_max_body_size 20m;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location /stream/ {
        proxy_pass          http://127.0.0.1:5000;
        proxy_buffering     off;
        proxy_cache         off;
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        proxy_pass          http://127.0.0.1:5000;
        proxy_read_timeout  120s;
        proxy_send_timeout  120s;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  fi

  ${SUDO} ln -sf "${conf_file}" "${enabled_dir}/signalscope"
  ${SUDO} rm -f "${enabled_dir}/default" 2>/dev/null || true
  ${SUDO} systemctl enable --now nginx
  ${SUDO} nginx -t && { ${SUDO} nginx -s reload 2>/dev/null || ${SUDO} systemctl restart nginx; }
  ok "nginx configured${fqdn:+ for ${fqdn}}"

  # TLS via certbot
  if [[ "${use_https}" == "1" && -n "${fqdn}" ]]; then
    step "Installing certbot"
    ${SUDO} apt-get install -y certbot python3-certbot-nginx

    step "Requesting Let's Encrypt certificate for ${fqdn}"
    info "Port 80 must be reachable from the internet for the ACME HTTP-01 challenge."
    if ${SUDO} certbot --nginx -d "${fqdn}" --non-interactive --agree-tos \
         --register-unsafely-without-email --redirect 2>&1; then
      ok "TLS certificate issued for ${fqdn}"
      ${SUDO} nginx -s reload
    else
      warn "certbot failed — you can retry manually: sudo certbot --nginx -d ${fqdn}"
    fi

    # Auto-renewal
    ${SUDO} systemctl enable --now certbot.timer 2>/dev/null \
      || ${SUDO} systemctl enable --now snap.certbot.renew.timer 2>/dev/null || true
    ok "certbot auto-renewal enabled"
  fi
}

write_logrotate() {
  ${SUDO} tee /etc/logrotate.d/${SERVICE_NAME} > /dev/null <<'EOF'
/var/log/signalscope/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su root root
}
EOF
}

rollback_app() {
  local backup="$1"
  warn "Rolling back to backup: $(basename "${backup}")"
  ${SUDO} cp -a "${backup}" "${TARGET_APP}"
  ${SUDO} chown "${SERVICE_USER}:${SERVICE_GROUP}" "${TARGET_APP}"
  if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    ${SUDO} systemctl restart "${SERVICE_NAME}" 2>/dev/null || true
  elif systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    ${SUDO} systemctl start "${SERVICE_NAME}" 2>/dev/null || true
  fi
  sleep 8
  if curl -fsS --max-time 4 http://127.0.0.1:5000/ >/dev/null 2>&1; then
    ok "Rollback succeeded — previous version is running"
    ok "Backup retained at: ${backup}"
  else
    err "Rollback also failed — manual intervention required"
    err "Backup file: ${backup}"
    err "Logs: journalctl -fu ${SERVICE_NAME}"
  fi
}

verify_app_health() {
  local max_tries=12   # 12 × 5 s = 60 s
  step "Verifying application is responding on port 5000"
  local i
  for i in $(seq 1 "${max_tries}"); do
    sleep 5
    if curl -fsS --max-time 4 http://127.0.0.1:5000/ >/dev/null 2>&1; then
      ok "Application is up and responding (tried ${i}/${max_tries})"
      # New version is healthy — remove backup so disk isn't wasted
      if [[ -n "${APP_BACKUP}" && -f "${APP_BACKUP}" ]]; then
        ${SUDO} rm -f "${APP_BACKUP}" 2>/dev/null || true
        info "Backup removed (new version verified healthy)"
      fi
      return 0
    fi
    info "  Waiting for app to start... (${i}/${max_tries})"
  done

  err "Application did not respond on port 5000 within 60 s"
  err "Logs: journalctl -fu ${SERVICE_NAME}"
  if [[ -n "${APP_BACKUP}" && -f "${APP_BACKUP}" ]]; then
    warn "Attempting automatic rollback to $(basename "${APP_BACKUP}")"
    rollback_app "${APP_BACKUP}"
  fi
  return 1
}

write_watchdog() {
  ${SUDO} tee /usr/local/bin/${SERVICE_NAME}-watchdog.sh > /dev/null <<'WDEOF'
#!/usr/bin/env bash
APP_SERVICE="signalscope"
NGINX_SERVICE="nginx"
LOG_TAG="signalscope-watchdog"

# ── SignalScope app on port 5000 ──────────────────────────────────────────────
if curl -fsS --max-time 8 http://127.0.0.1:5000/ >/dev/null 2>&1; then
  : # app responding — nothing to do
elif systemctl is-active --quiet "${APP_SERVICE}" 2>/dev/null; then
  logger -t "${LOG_TAG}" "Port 5000 unresponsive — restarting ${APP_SERVICE}"
  systemctl restart "${APP_SERVICE}"
elif systemctl is-enabled --quiet "${APP_SERVICE}" 2>/dev/null; then
  logger -t "${LOG_TAG}" "${APP_SERVICE} not running — starting"
  systemctl start "${APP_SERVICE}" || true
fi

# ── nginx on port 443 / 80 ────────────────────────────────────────────────────
if systemctl is-enabled --quiet "${NGINX_SERVICE}" 2>/dev/null; then
  if curl -fsk --max-time 8 https://127.0.0.1/ >/dev/null 2>&1 || \
     curl -fs  --max-time 8 http://127.0.0.1/  >/dev/null 2>&1; then
    : # nginx responding — nothing to do
  elif systemctl is-active --quiet "${NGINX_SERVICE}" 2>/dev/null; then
    logger -t "${LOG_TAG}" "nginx not responding on 80/443 — restarting"
    systemctl restart "${NGINX_SERVICE}"
  else
    logger -t "${LOG_TAG}" "nginx not active — starting"
    systemctl start "${NGINX_SERVICE}" || true
  fi
fi
WDEOF
  ${SUDO} chmod +x /usr/local/bin/${SERVICE_NAME}-watchdog.sh
}

setup_ptp() {
  # Install linuxptp (ptp4l + pmc) and configure it as a monitor-only PTP slave.
  #
  # free_running 1  — ptp4l measures offsetFromMaster but NEVER adjusts the
  #                   system clock.  NTP continues to own clock discipline
  #                   entirely.  There is no conflict.
  # slaveOnly 1     — this machine will never participate in BMCA as a potential
  #                   master and will never send Announce messages.
  # time_stamping software — no hardware PHC required; works on any NIC.
  #
  # The only network traffic added: Delay_Req packets (~44 bytes, ~1/s) sent to
  # the grandmaster so path delay can be measured.  The GM expects these from
  # every slave and handles many simultaneously.
  #
  # Idempotent — safe to run on updates; skips if already configured identically.

  step "Installing linuxptp (ptp4l + pmc)"
  if command -v apt-get &>/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    ${SUDO} apt-get install -y linuxptp || { warn "linuxptp install failed — PTP monitoring will use passive listener only"; return 0; }
  else
    warn "apt-get not available — skipping linuxptp install"
    return 0
  fi

  # Detect the default network interface (used for PTP multicast)
  local ptp_iface
  ptp_iface=$(ip route get 1.1.1.1 2>/dev/null \
    | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' \
    | head -1)
  [[ -z "$ptp_iface" ]] && ptp_iface="eth0"

  ${SUDO} mkdir -p /etc/linuxptp

  ${SUDO} tee /etc/linuxptp/ptp4l.conf > /dev/null <<EOF
# ptp4l configuration — managed by SignalScope installer
#
# MONITOR-ONLY mode:
#   slaveOnly 1     — never becomes a PTP master
#   free_running 1  — measures offset but does NOT adjust the system clock
#                     NTP continues to own clock discipline; no conflict possible
#
# To change the network interface: edit the section header at the bottom of
# this file and run:  sudo systemctl restart ptp4l

[global]
slaveOnly               1
free_running            1
time_stamping           software
logging_level           5
summary_interval        1
kernel_leap             1
check_fup_sync          1
follow_up_info          1
tx_timestamp_timeout    10

[$ptp_iface]
EOF

  # Some distros install linuxptp without a systemd service file.
  # If ptp4l binary exists but the service doesn't, create it ourselves.
  if ! systemctl list-unit-files ptp4l.service 2>/dev/null | grep -q ptp4l; then
    if command -v ptp4l &>/dev/null; then
      PTP4L_BIN=$(command -v ptp4l)
      ${SUDO} tee /etc/systemd/system/ptp4l.service > /dev/null <<EOF
[Unit]
Description=Precision Time Protocol (PTP) service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${PTP4L_BIN} -f /etc/linuxptp/ptp4l.conf
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
      ${SUDO} systemctl daemon-reload
      ok "Created ptp4l.service (was missing from linuxptp package)"
    fi
  fi

  # Override the default service ExecStart so it always reads our config
  # (distribution defaults vary — some pass -i eth0, some pass nothing)
  if systemctl list-unit-files ptp4l.service 2>/dev/null | grep -q ptp4l; then
    ${SUDO} mkdir -p /etc/systemd/system/ptp4l.service.d
    ${SUDO} tee /etc/systemd/system/ptp4l.service.d/signalscope.conf > /dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/sbin/ptp4l -f /etc/linuxptp/ptp4l.conf
EOF

    ${SUDO} systemctl daemon-reload
    ${SUDO} systemctl enable ptp4l 2>/dev/null || true
    if ${SUDO} systemctl restart ptp4l 2>/dev/null; then
      ok "ptp4l running (slave-only, monitor mode) on interface: ${ptp_iface}"
    else
      warn "ptp4l failed to start — PTP traffic may not be present on ${ptp_iface}"
      warn "To change interface: edit /etc/linuxptp/ptp4l.conf, then: sudo systemctl restart ptp4l"
    fi
    ok "pmc available for accurate PTP offset readings"
  else
    warn "ptp4l binary not found — PTP monitoring will use passive listener only"
  fi
}

create_service() {
  step "Installing systemd service"

  ${SUDO} tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
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
# Apply USB buffer fix before starting (runs as root regardless of User=).
# Removes the 16 MB usbfs limit that causes RTL-SDR zero-copy failures
# when multiple dongles are in use. No-op on non-Linux or if already set.
ExecStartPre=+/bin/sh -c 'echo 0 > /sys/module/usbcore/parameters/usbfs_memory_mb 2>/dev/null || true'
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

  write_watchdog
  ${SUDO} chmod +x /usr/local/bin/${SERVICE_NAME}-watchdog.sh

  ${SUDO} tee "/etc/systemd/system/${SERVICE_NAME}-watchdog.service" > /dev/null <<EOF
[Unit]
Description=${APP_NAME} watchdog

[Service]
Type=oneshot
ExecStart=/usr/local/bin/${SERVICE_NAME}-watchdog.sh
EOF

  ${SUDO} tee "/etc/systemd/system/${SERVICE_NAME}-watchdog.timer" > /dev/null <<EOF
[Unit]
Description=Run ${APP_NAME} watchdog every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=${SERVICE_NAME}-watchdog.service

[Install]
WantedBy=timers.target
EOF

  ${SUDO} systemctl daemon-reload
  ${SUDO} systemctl enable --now "${SERVICE_NAME}.service"
  ${SUDO} systemctl enable --now "${SERVICE_NAME}-watchdog.timer"
  ok "Service enabled: ${SERVICE_NAME}"
}

main() {
  print_banner
  parse_args "$@"

  need_cmd bash
  need_cmd python3
  need_cmd install
  need_cmd curl

  if [[ $EUID -eq 0 && -z "${SUDO_USER:-}" ]]; then
    warn "Running directly as root."
  fi

  # Set SUDO variable now so it's always defined; actual password prompt
  # (auth_sudo) runs later, after all interactive questions are answered.
  init_sudo

  INSTALL_ROOT="$(ask_value "Install directory" "${INSTALL_ROOT}")"

  # ── Legacy LivewireAIMonitor detection (must run before version check) ───────
  # If the old service exists we always treat this as a fresh install so that
  # the full setup path (apt, venv, service creation) runs and files are migrated.
  if detect_legacy_livewire_install; then
    warn "Legacy LivewireAIMonitor service detected${LEGACY_WORKDIR:+ at ${LEGACY_WORKDIR}}."
    warn "The old service will be stopped and config/models migrated to ${INSTALL_ROOT}."
    warn "This will be treated as a fresh SignalScope install."
    IS_UPDATE=0   # force fresh-install mode regardless of what resolve_best_source finds
  fi

  # ── Version check: compare installed / local / remote ──────────────────────
  echo
  resolve_best_source

  # If legacy was detected, override the IS_UPDATE that resolve_best_source may
  # have re-set to 1 (e.g. a partial SignalScope install already exists).
  if [[ "${LEGACY_INSTALL_DETECTED}" -eq 1 ]]; then
    IS_UPDATE=0
  fi

  # ── Already current? (only relevant when no legacy migration needed) ─────────
  if [[ "${IS_UPDATE}" -eq 1 && "${WINNING_SOURCE}" == "installed" && "${FORCE_OVERWRITE}" -ne 1 ]]; then
    ok "${APP_NAME} ${WINNING_VER} is already the latest version."
    if [[ "${INTERACTIVE}" -eq 1 ]]; then
      ask_yes_no "Reinstall/repair dependencies anyway?" "n" || { warn "Nothing to do."; exit 0; }
    else
      exit 0
    fi
  fi

  # ── Interactive prompts — skip heavy questions in update mode ───────────────
  if [[ "${IS_UPDATE}" -eq 1 ]]; then
    info "Update mode: ${INSTALLED_VER} → ${WINNING_VER}"
    [[ -z "${ENABLE_SERVICE}" ]] && ENABLE_SERVICE=0
    if [[ -z "${ENABLE_SDR}" ]]; then
      # Default to yes if rtl-sdr tools are already present on this machine
      local _sdr_default="n"
      command -v rtl_fm &>/dev/null && _sdr_default="y"
      if ask_yes_no "Apply SDR kernel fixes (usbfs buffer, rtlsdr blacklist)?" "${_sdr_default}"; then
        ENABLE_SDR=1
      else
        ENABLE_SDR=0
      fi
    fi
  else
    info "Fresh install mode${LEGACY_INSTALL_DETECTED:+ (migrating from LivewireAIMonitor)}"

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

    if [[ -z "${ENABLE_LIVEWIRE}" ]]; then
      echo
      info "Livewire/AES67 streams (RTP multicast) require larger kernel UDP receive buffers."
      if ask_yes_no "Are you using Livewire or AES67 RTP multicast inputs?" "n"; then
        ENABLE_LIVEWIRE=1
      else
        ENABLE_LIVEWIRE=0
      fi
    fi

    if [[ -z "${ENABLE_ICECAST}" ]]; then
      echo
      info "The Icecast plugin lets you re-stream any monitored input as a live Icecast2 stream."
      if ask_yes_no "Install Icecast2 (for the Icecast Streaming plugin)?" "n"; then
        ENABLE_ICECAST=1
      else
        ENABLE_ICECAST=0
      fi
    fi
  fi

  EXISTING_PROXY=0
  detect_existing_proxy_setup && EXISTING_PROXY=1 || true

  if [[ -z "${ENABLE_NGINX}" ]]; then
    if [[ "${IS_UPDATE}" -eq 1 && "${EXISTING_PROXY}" -eq 0 ]]; then
      if ask_yes_no "No reverse proxy detected. Install nginx now?" "y"; then
        ENABLE_NGINX=1
      else
        ENABLE_NGINX=0
      fi
    elif [[ "${IS_UPDATE}" -ne 1 ]]; then
      if ask_yes_no "Install nginx as a reverse proxy (recommended)?" "y"; then
        ENABLE_NGINX=1
      else
        ENABLE_NGINX=0
      fi
    else
      # ── Repair / update run with existing nginx config ─────────────────────
      _nginx_ok=0; ${SUDO} nginx -t >/dev/null 2>&1 && _nginx_ok=1 || true
      _exist_fqdn=""
      if [[ -f /etc/nginx/sites-available/signalscope ]]; then
        _exist_fqdn=$(grep -oP 'server_name\s+\K[^\s;]+' /etc/nginx/sites-available/signalscope 2>/dev/null \
                     | grep -v '^_' | grep '\.' | head -1 || true)
      fi
      _cert_ok=0
      [[ -n "${_exist_fqdn}" && -f "/etc/letsencrypt/live/${_exist_fqdn}/fullchain.pem" ]] && _cert_ok=1 || true
      _needs_repair=0
      if [[ "${_nginx_ok}" -eq 0 ]]; then
        warn "Existing nginx config fails the config test — it is broken."
        _needs_repair=1
      elif [[ -n "${_exist_fqdn}" && "${_cert_ok}" -eq 0 ]]; then
        warn "Nginx is configured for '${_exist_fqdn}' but no TLS certificate was found."
        warn "The Let's Encrypt step most likely failed during the original install."
        _needs_repair=1
      fi
      if [[ "${_needs_repair}" -eq 1 ]]; then
        info "Existing config: /etc/nginx/sites-available/signalscope${_exist_fqdn:+ — FQDN: ${_exist_fqdn}}"
        if ask_yes_no "Remove the existing nginx config and start over?" "y"; then
          ${SUDO} rm -f /etc/nginx/sites-available/signalscope
          ${SUDO} rm -f /etc/nginx/sites-enabled/signalscope
          [[ -n "${_exist_fqdn}" ]] && ${SUDO} rm -f "/etc/letsencrypt/renewal/${_exist_fqdn}.conf" 2>/dev/null || true
          ok "Existing nginx config cleared"
          EXISTING_PROXY=0
          ENABLE_NGINX=1
          [[ -n "${_exist_fqdn}" ]] && NGINX_FQDN_DEFAULT="${_exist_fqdn}"
        else
          ENABLE_NGINX=0
        fi
      else
        ok "Nginx appears healthy${_exist_fqdn:+ (serving ${_exist_fqdn})}."
        if ask_yes_no "Reconfigure nginx (change FQDN or TLS settings)?" "n"; then
          ${SUDO} rm -f /etc/nginx/sites-available/signalscope
          ${SUDO} rm -f /etc/nginx/sites-enabled/signalscope
          ok "Existing nginx config cleared"
          EXISTING_PROXY=0
          ENABLE_NGINX=1
          [[ -n "${_exist_fqdn}" ]] && NGINX_FQDN_DEFAULT="${_exist_fqdn}"
        else
          ENABLE_NGINX=0
        fi
      fi
    fi
  fi

  if [[ "${ENABLE_NGINX}" == "1" && -z "${NGINX_FQDN}" ]]; then
    NGINX_FQDN="$(ask_value "FQDN for nginx vhost (blank for HTTP-only)" "${NGINX_FQDN_DEFAULT:-}")"
  fi

  if [[ "${ENABLE_NGINX}" == "1" && -n "${NGINX_FQDN}" && -z "${NGINX_HTTPS}" ]]; then
    if ask_yes_no "Request a Let's Encrypt TLS certificate for ${NGINX_FQDN}?" "y"; then
      NGINX_HTTPS=1
    else
      NGINX_HTTPS=0
    fi
  fi

  echo
  info "OS: $(. /etc/os-release && echo "${PRETTY_NAME:-Linux}")"
  info "Arch: $(dpkg --print-architecture 2>/dev/null || uname -m)"
  info "Install dir: ${INSTALL_ROOT}"
  if [[ "${IS_UPDATE}" -eq 1 ]]; then
    info "Mode: update ${INSTALLED_VER} → ${WINNING_VER}"
  elif [[ "${LEGACY_INSTALL_DETECTED}" -eq 1 ]]; then
    info "Mode: fresh install ${WINNING_VER}  (migrating from LivewireAIMonitor)"
  else
    info "Mode: fresh install ${WINNING_VER}"
  fi
  info "Install service: $([[ "${ENABLE_SERVICE}" == "1" ]] && echo yes || echo no)"
  info "Install SDR: $([[ "${ENABLE_SDR}" == "1" ]] && echo yes || echo no)"
  info "Install nginx: $([[ "${ENABLE_NGINX}" == "1" ]] && echo yes || echo no)"
  if [[ "${IS_UPDATE}" -ne 1 ]]; then
    info "UDP tuning: $([[ "${ENABLE_LIVEWIRE}" == "1" ]] && echo "Livewire/AES67 (1.5 GB default, ~2 GB burst, IGMP limit → 512)" || echo "standard")"
  else
    # On updates the tuning block is skipped — report what is currently live on disk.
    _sysctl_conf="/etc/sysctl.d/99-signalscope-network.conf"
    if [[ -f "${_sysctl_conf}" ]]; then
      if grep -q "Livewire" "${_sysctl_conf}" 2>/dev/null; then
        info "UDP tuning: Livewire/AES67 profile already applied (re-run with --livewire --retune to reapply)"
      else
        info "UDP tuning: standard profile already applied (re-run with --livewire --retune to switch to Livewire/AES67)"
      fi
    else
      info "UDP tuning: not yet applied (run a fresh install or use --livewire --force)"
    fi
  fi
  [[ -n "${PI_MODEL_STR}" ]] && info "Pi model: ${PI_MODEL_STR}"
  [[ -n "${PI_MODEL_STR}" && "${PI_GEN}" -ne 5 ]] && info "Pi overclock: $([[ "${ENABLE_OVERCLOCK}" == "1" ]] && echo yes || echo no)"
  if [[ "${ENABLE_NGINX}" == "1" ]]; then
    info "nginx FQDN: ${NGINX_FQDN:-<none, HTTP-only>}"
    info "Let's Encrypt: $([[ "${NGINX_HTTPS}" == "1" ]] && echo yes || echo no)"
  fi
  if [[ "${INTERACTIVE}" == "1" ]]; then
    echo
    if ! ask_yes_no "Continue?" "y"; then
      warn "Cancelled."; exit 0
    fi
  fi

  # All questions answered — now authenticate sudo before any privileged work.
  auth_sudo

  SERVICE_USER="${SUDO_USER:-$USER}"
  SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  IS_ARM=0
  case "$ARCH" in armhf|arm64|aarch64) IS_ARM=1 ;; esac

  # ── Raspberry Pi detection & optional overclock ───────────────────────────────
  detect_pi_model || true
  if [[ "${PI_GEN}" -ge 3 ]]; then
    info "Detected: ${PI_MODEL_STR}"
    if [[ "${PI_GEN}" -eq 5 ]]; then
      info "Raspberry Pi 5 detected — overclocking is not supported by this installer (skip)."
      ENABLE_OVERCLOCK=0
    else
      if [[ -z "${ENABLE_OVERCLOCK}" && "${INTERACTIVE}" -eq 1 ]]; then
        echo
        warn "Overclocking can improve DAB decode / AI performance but requires adequate cooling."
        if ask_yes_no "Apply Raspberry Pi overclock settings for ${PI_MODEL_STR}?" "n"; then
          ENABLE_OVERCLOCK=1
        else
          ENABLE_OVERCLOCK=0
        fi
      fi
      if [[ "${ENABLE_OVERCLOCK}" == "1" ]]; then
        step "Applying Raspberry Pi overclock settings"
        apply_pi_overclock
      fi
    fi
  fi

  # ── System packages — only on fresh install ─────────────────────────────────
  if [[ "${IS_UPDATE}" -ne 1 ]]; then
    step "Installing apt packages"
    export DEBIAN_FRONTEND=noninteractive
    ${SUDO} apt-get update -y
    ${SUDO} apt-get install -y \
      python3 python3-venv python3-dev python3-setuptools \
      build-essential pkg-config git curl wget ca-certificates rsync \
      ffmpeg ethtool net-tools iproute2 jq \
      libffi-dev libssl-dev \
      libportaudio2

    if [[ "${ENABLE_SDR}" == "1" ]]; then
      step "Installing SDR apt packages"
      ${SUDO} apt-get install -y \
        rtl-sdr librtlsdr-dev welle.io \
        libsndfile1 libsndfile1-dev libliquid-dev libfftw3-dev nlohmann-json3-dev \
        meson ninja-build || true
      ensure_rtlsdr_blacklist
      ensure_usbfs_unlimited
    fi

    if [[ "${ENABLE_ICECAST}" == "1" ]]; then
      step "Installing Icecast2"
      # Pre-answer the debconf questions so apt doesn't open an interactive dialog
      echo "icecast2 icecast2/icecast-setup boolean false" | ${SUDO} debconf-set-selections 2>/dev/null || true
      if ${SUDO} apt-get install -y icecast2; then
        ok "Icecast2 installed — configure via SignalScope Plugins → Icecast Streaming"
      else
        warn "Icecast2 install failed — you can install it later with: sudo apt install icecast2"
      fi
    fi

  fi

  # ── Runtime apt packages needed on fresh installs AND updates ────────────────
  # (libportaudio2 is required by sounddevice; apt-get install is idempotent)
  if command -v apt-get &>/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    ${SUDO} apt-get install -y libportaudio2 || warn "Could not install libportaudio2 (sound device input may not work)"
  fi

  # ── SDR kernel fixes — always applied (idempotent) ───────────────────────────
  if [[ "${ENABLE_SDR}" == "1" ]]; then
    ensure_rtlsdr_blacklist
    ensure_usbfs_unlimited
  fi

  # ── Directories ──────────────────────────────────────────────────────────────
  step "Preparing directories"
  ${SUDO} mkdir -p "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"
  ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"

  # ── Migrate legacy LivewireAIMonitor install (runs whenever detected) ─────────
  if [[ "${LEGACY_INSTALL_DETECTED}" -eq 1 ]]; then
    migrate_legacy_livewire_install
  fi

  VENV_DIR="${INSTALL_ROOT}/venv"
  TARGET_APP="${INSTALL_ROOT}/${APP_PY_NAME}"

  # ── Application file ─────────────────────────────────────────────────────────
  step "Installing application files"
  if [[ "${WINNING_SOURCE}" == "installed" && "${FORCE_OVERWRITE}" -ne 1 ]]; then
    info "App file is current (${WINNING_VER}), skipping copy."
  else
    # Back up the running file before replacing it so we can auto-rollback if
    # the new version fails to start.
    if [[ -f "${TARGET_APP}" ]]; then
      APP_BACKUP="${TARGET_APP%.py}.backup-${INSTALLED_VER:-prev}.py"
      ${SUDO} cp -a "${TARGET_APP}" "${APP_BACKUP}"
      ok "Backed up current version (${INSTALLED_VER:-?}) → $(basename "${APP_BACKUP}")"
    fi
    install -m 0644 "${SOURCE_APP}" "/tmp/${APP_PY_NAME}.$$"
    ${SUDO} mv "/tmp/${APP_PY_NAME}.$$" "${TARGET_APP}"
    ${SUDO} chown "${SERVICE_USER}:${SERVICE_GROUP}" "${TARGET_APP}"
    ok "Installed ${TARGET_APP} (${WINNING_VER})"
  fi

  # ── Metrics DB migration note (3.0.5+) ───────────────────────────────────────
  # metrics_history.db is created automatically by SignalScope on first start via
  # CREATE TABLE IF NOT EXISTS — no manual step is required.  We just note it so
  # operators know where the file lives.
  if [[ "${IS_UPDATE}" -eq 1 ]]; then
    METRICS_DB="${INSTALL_ROOT}/metrics_history.db"
    if [[ ! -f "${METRICS_DB}" ]]; then
      info "New in this version: SQLite metric history (metrics_history.db)."
      info "  → The file will be created automatically on first start at ${METRICS_DB}"
    fi
  fi

  # Copy static assets if source has them AND either:
  #   - this is a fresh install / update (not "installed"), OR
  #   - the install dir is missing static assets
  if [[ -d "${SOURCE_DIR}/static" ]]; then
    if [[ "${WINNING_SOURCE}" != "installed" || ! -f "${INSTALL_ROOT}/static/signalscope_icon.png" ]]; then
      ${SUDO} mkdir -p "${INSTALL_ROOT}/static"
      ${SUDO} rsync -a "${SOURCE_DIR}/static/" "${INSTALL_ROOT}/static/"
      ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/static"
      ok "Updated static assets"
    fi
  elif [[ ! -f "${INSTALL_ROOT}/static/signalscope_icon.png" ]]; then
    # No source static dir and no installed assets — fetch directly
    ${SUDO} mkdir -p "${INSTALL_ROOT}/static"
    for asset in signalscope_icon.ico signalscope_icon.png signalscope_logo.jpg signalscope_logo.png; do
      ${SUDO} curl -fsSL --max-time 10 "${RAW_BASE_URL}/static/${asset}" -o "${INSTALL_ROOT}/static/${asset}" 2>/dev/null || true
    done
    ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/static"
    ok "Downloaded static assets from GitHub"
  fi

  # ── Python version check ─────────────────────────────────────────────────────
  step "Checking Python version"
  PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  PY_MAJ="$(python3 -c 'import sys; print(sys.version_info.major)')"
  PY_MIN="$(python3 -c 'import sys; print(sys.version_info.minor)')"
  if [[ "${PY_MAJ}" -lt 3 || ( "${PY_MAJ}" -eq 3 && "${PY_MIN}" -lt 9 ) ]]; then
    warn "Python ${PY_VER} detected — 3.9+ required for AI/ONNX features."
  else
    ok "Python ${PY_VER}"
  fi

  # ── Virtual environment ───────────────────────────────────────────────────────
  step "Creating/updating Python virtual environment"
  python3 -m venv "${VENV_DIR}"
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"

  # ── Python packages — pip is idempotent, safe to run on updates too ──────────
  step "Installing/updating Python packages"
  python -m pip install --upgrade pip wheel "setuptools<81"
  python -m pip install flask waitress cheroot numpy scipy requests certifi cryptography psutil sounddevice "httpx[http2]" pyotp "qrcode[pil]"

  step "Installing optional MP3 encoder (lameenc)"
  python -m pip install lameenc || warn "lameenc not available — MP3 clip encoding will fall back to ffmpeg or WAV"

  step "Installing optional SNMP library (pysnmp)"
  python -m pip install pysnmp || warn "pysnmp not available — Codec Monitor will use HTTP/TCP fallback for SNMP devices (Prodys Quantum ST, APT WorldCast)"

  step "Installing optional Zetta SOAP client (zeep)"
  python -m pip install zeep || warn "zeep not available — Zetta plugin station discovery and sequencer polling will be unavailable"

  step "Installing optional WebRTC stack (aiortc) for IP Link server-side routing"
  # aiortc enables server-side WebRTC in the IP Link plugin — talent codecs stay
  # connected and Livewire output runs even when no browser is open.
  # av (PyAV) and numpy are also required; numpy is already installed above.
  # On Raspberry Pi this may take a few minutes to compile from source.
  if python -m pip install "aiortc" "av"; then
    ok "aiortc installed — IP Link server-side WebRTC available"
  else
    warn "aiortc not available — IP Link will fall back to browser-managed WebRTC (Livewire server routing still works)"
  fi

  step "Installing/checking ONNX stack"
  python -m pip install onnx || warn "Failed to install onnx"
  if ! python -m pip install onnxruntime; then
    [[ $IS_ARM -eq 1 ]] \
      && warn "onnxruntime wheel may be unavailable on this ARM platform; AI features may be unavailable." \
      || warn "Failed to install onnxruntime"
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

  # ── System tuning — fresh install, or forced retune via --retune ─────────────
  if [[ "${IS_UPDATE}" -ne 1 || "${FORCE_RETUNE}" -eq 1 ]]; then
    step "Applying network tuning"
    # Standard values — sufficient for HTTP/RTP unicast workloads.
    # Livewire/AES67 multicast uses 4× standard buffers to handle the high-rate
    # 1 ms packet bursts across many simultaneous streams without kernel-side drops.
    # Values are just per-socket maximums — the kernel lazy-allocates; no RAM is
    # consumed until a socket actually fills its buffer.
    # Kernel stores rmem/wmem as signed int — hard ceiling is INT_MAX (2^31-1 = 2147483647).
    # net.ipv4.udp_rmem_min / udp_wmem_min were added in kernel 4.15; skip gracefully on older kernels.
    if [[ "${ENABLE_LIVEWIRE}" == "1" ]]; then
      _rmem_max=2147483647   # INT_MAX ~2 GB — burst ceiling (highest the kernel accepts as signed int)
      _rmem_def=1610612736   # 1.5 GB = 3× 512 MB — default per socket
      _wmem_max=2147483647
      _wmem_def=1610612736
      _udp_rmin=3145728      # 3 MB = 3× 1 MB
      _udp_wmin=3145728
      _backlog=2250000       # 3× 750000
      _optmem=196608         # 3× 65536
      ok "Livewire/AES67 mode: 3× default buffers (1.5 GB), burst ceiling INT_MAX (~2 GB)"
    else
      _rmem_max=536870912    # 512 MB
      _rmem_def=536870912
      _wmem_max=536870912
      _wmem_def=536870912
      _udp_rmin=1048576      # 1 MB
      _udp_wmin=1048576
      _backlog=750000
      _optmem=65536
      ok "Standard UDP tuning (re-run with --livewire --retune for Livewire/AES67 optimisation)"
    fi

    ${SUDO} tee /etc/sysctl.d/99-signalscope-network.conf > /dev/null <<EOF
# SignalScope network tuning$([[ "${ENABLE_LIVEWIRE}" == "1" ]] && echo " — Livewire/AES67 profile" || echo " — standard profile")
net.core.rmem_max=${_rmem_max}
net.core.rmem_default=${_rmem_def}
net.core.wmem_max=${_wmem_max}
net.core.wmem_default=${_wmem_def}
net.core.netdev_max_backlog=${_backlog}
net.core.optmem_max=${_optmem}
EOF
    # udp_rmem_min / udp_wmem_min only exist on kernel ≥ 4.15 — apply individually and ignore EINVAL
    ${SUDO} sysctl -w net.ipv4.udp_rmem_min="${_udp_rmin}" 2>/dev/null \
      && echo "net.ipv4.udp_rmem_min=${_udp_rmin}" | ${SUDO} tee -a /etc/sysctl.d/99-signalscope-network.conf >/dev/null \
      || warn "net.ipv4.udp_rmem_min not supported on this kernel — skipping"
    ${SUDO} sysctl -w net.ipv4.udp_wmem_min="${_udp_wmin}" 2>/dev/null \
      && echo "net.ipv4.udp_wmem_min=${_udp_wmin}" | ${SUDO} tee -a /etc/sysctl.d/99-signalscope-network.conf >/dev/null \
      || warn "net.ipv4.udp_wmem_min not supported on this kernel — skipping"
    # Livewire/AES67: raise the per-socket IGMP multicast group membership limit.
    # Linux default is 20 — one join per stream, so this hard-caps you at 20 Livewire
    # inputs per socket.  512 comfortably covers any realistic deployment.
    # Written to sysctl.d so it survives reboots; applied live so no reboot is needed.
    if [[ "${ENABLE_LIVEWIRE}" == "1" ]]; then
      ${SUDO} sysctl -w net.ipv4.igmp_max_memberships=512 2>/dev/null \
        && echo "net.ipv4.igmp_max_memberships=512" | ${SUDO} tee -a /etc/sysctl.d/99-signalscope-network.conf >/dev/null \
        || warn "net.ipv4.igmp_max_memberships not settable on this kernel — skipping"
      ok "IGMP membership limit raised to 512 (supports up to ~512 simultaneous Livewire/AES67 multicast streams)"
    fi
    ${SUDO} sysctl --system >/dev/null || true
    ok "Network tuning applied live (no reboot needed)"

    DEFAULT_IFACE="$(ip route 2>/dev/null | awk '/default/ {print $5; exit}')"
    if [[ -n "${DEFAULT_IFACE:-}" ]]; then
      info "Applying best-effort NIC tuning to ${DEFAULT_IFACE}"
      ${SUDO} ethtool -K "${DEFAULT_IFACE}" gro off gso off tso off lro off >/dev/null 2>&1 || true
      ${SUDO} ethtool -G "${DEFAULT_IFACE}" rx 4096 tx 4096 >/dev/null 2>&1 || true
    fi

    step "Setting Python low-port capability"
    PYBIN="$(readlink -f "$(command -v python3)")"
    [[ -f "${PYBIN}" ]] && ${SUDO} setcap cap_net_bind_service=+ep "${PYBIN}" || true

    getent group plugdev >/dev/null 2>&1 && ${SUDO} usermod -aG plugdev "${SERVICE_USER}" || true

    step "Writing environment file"
    ${SUDO} tee /etc/default/${SERVICE_NAME} > /dev/null <<EOF
SIGNALSCOPE_INSTALL_DIR=${INSTALL_ROOT}
SIGNALSCOPE_DATA_DIR=${DATA_ROOT_DEFAULT}
SIGNALSCOPE_LOG_DIR=${LOG_ROOT_DEFAULT}
EOF

    [[ "${ENABLE_SERVICE}" == "1" ]] && create_service
  fi

  # ── Patch existing service file with ExecStartPre usbfs fix (updates) ───────
  # If the service file already exists but does not yet contain the ExecStartPre
  # usbfs fix, rewrite it so existing installs get the fix without needing --service.
  local _svc_file="/etc/systemd/system/${SERVICE_NAME}.service"
  if [[ -f "${_svc_file}" ]] && ! grep -q "usbfs_memory_mb" "${_svc_file}" 2>/dev/null; then
    ${SUDO} sed -i \
      '/^ExecStart=/i ExecStartPre=+\/bin\/sh -c '"'"'echo 0 > \/sys\/module\/usbcore\/parameters\/usbfs_memory_mb 2>\/dev\/null || true'"'" \
      "${_svc_file}"
    ${SUDO} systemctl daemon-reload
    ok "Patched ${_svc_file} with usbfs ExecStartPre fix"
  fi

  # ── linuxptp — monitor-only PTP slave (fresh install and updates) ────────────
  # Runs on every install/update so existing machines get ptp4l on upgrade.
  # safe to re-run: config is overwritten only if changed, service is restarted.
  setup_ptp

  # ── nginx (fresh install or explicitly requested) ────────────────────────────
  if [[ "${ENABLE_NGINX}" == "1" ]]; then
    configure_nginx "${NGINX_FQDN:-}" "${NGINX_HTTPS:-0}"
  fi

  # ── Ensure client_max_body_size is set in any existing nginx config ───────────
  # Clip uploads (WAV/MP3) can be large; nginx's 1 MB default causes 413 errors.
  # Patch every server block in the SignalScope config that lacks the directive.
  _ng_conf="/etc/nginx/sites-available/signalscope"
  if [[ -f "${_ng_conf}" ]] && ! grep -q "client_max_body_size" "${_ng_conf}" 2>/dev/null; then
    step "Patching nginx config: adding client_max_body_size 20m"
    # Insert directive on the line after each 'server {' opening brace.
    # Match regardless of leading whitespace (certbot may rewrite the file
    # with indented server blocks).
    ${SUDO} sed -i '/^[[:space:]]*server[[:space:]]*{/a\    client_max_body_size 20m;' "${_ng_conf}"
    if ${SUDO} nginx -t >/dev/null 2>&1; then
      ${SUDO} nginx -s reload 2>/dev/null || ${SUDO} systemctl reload nginx 2>/dev/null || true
      ok "nginx patched: client_max_body_size 20m"
    else
      warn "nginx config test failed after patch — reverting client_max_body_size change"
      ${SUDO} sed -i '/client_max_body_size 20m;/d' "${_ng_conf}"
    fi
  fi

  # ── Log rotation — written on every run so updates also get it ──────────────
  write_logrotate
  ok "Log rotation configured (daily, 14-day retention)"

  # ── Refresh watchdog script on every run (update or reinstall) ───────────────
  if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    step "Refreshing watchdog script"
    write_watchdog
    ${SUDO} systemctl daemon-reload
    ${SUDO} systemctl enable --now "${SERVICE_NAME}-watchdog.timer" 2>/dev/null || true
    ok "Watchdog updated"
  fi

  # ── Restart service after update, then health-check with auto-rollback ────────
  if [[ "${IS_UPDATE}" -eq 1 && "${WINNING_SOURCE}" != "installed" ]]; then
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
      step "Restarting ${SERVICE_NAME} to apply update"
      ${SUDO} systemctl restart "${SERVICE_NAME}"
    elif systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
      step "Starting ${SERVICE_NAME}"
      ${SUDO} systemctl start "${SERVICE_NAME}"
    fi
  fi

  # ── Post-start health check (fresh install and updates) ──────────────────────
  if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    verify_app_health || true
  fi

  # ── Summary ───────────────────────────────────────────────────────────────────
  echo
  if [[ "${IS_UPDATE}" -eq 1 && "${WINNING_SOURCE}" != "installed" ]]; then
    ok "Update complete: ${INSTALLED_VER} → ${WINNING_VER}"
  else
    ok "Installation complete (${WINNING_VER})"
  fi
  echo "Installed app : ${TARGET_APP}"
  echo "Virtualenv    : ${VENV_DIR}"
  echo "Data dir      : ${DATA_ROOT_DEFAULT}"
  echo "Log dir       : ${LOG_ROOT_DEFAULT}"
  if [[ "${ENABLE_SERVICE}" == "1" ]] || systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    echo "Service       : ${SERVICE_NAME}"
    echo "Status        : systemctl status ${SERVICE_NAME}"
    echo "Logs          : journalctl -fu ${SERVICE_NAME}"
  else
    echo "Run manually  : source \"${VENV_DIR}/bin/activate\" && python \"${TARGET_APP}\""
  fi
  echo
  if [[ "${ENABLE_NGINX}" == "1" && -n "${NGINX_FQDN:-}" ]]; then
    [[ "${NGINX_HTTPS:-0}" == "1" ]] && echo "Open: https://${NGINX_FQDN}" || echo "Open: http://${NGINX_FQDN}"
  else
    echo "Open: http://<server-ip>:5000"
  fi
  if [[ "${ENABLE_OVERCLOCK}" == "1" && "${PI_GEN}" -ge 3 ]]; then
    echo
    warn "Raspberry Pi overclock settings were written. Reboot to apply: sudo reboot"
  fi
}

main "$@"
