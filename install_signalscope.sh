#!/usr/bin/env bash
set -Eeuo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
# Use $'...' ANSI-C quoting so the actual ESC byte is stored in the variable.
# Single quotes ('\033[1m') store a literal backslash — fine for printf format
# strings (which interpret \033) but NOT for read -p prompts (which don't).
GREEN=$'\033[0;32m';  YELLOW=$'\033[1;33m';  RED=$'\033[0;31m'
BLUE=$'\033[0;34m';   CYAN=$'\033[0;36m';    BOLD=$'\033[1m'
DIM=$'\033[2m';       NC=$'\033[0m'

# ─── UI / verbosity mode ──────────────────────────────────────────────────────
# --debug shows every command's raw output instead of spinners.
DEBUG=0

# ─── Output helpers ───────────────────────────────────────────────────────────
ok()     { printf "  ${GREEN}✔${NC}  %s\n"        "$*"; }
warn()   { printf "  ${YELLOW}⚠${NC}  %s\n"       "$*" >&2; }
err()    { printf "  ${RED}✖${NC}  %s\n"          "$*" >&2; }
info()   { printf "     ${DIM}%s${NC}\n"           "$*"; }
detail() { printf "     %s\n"                      "$*"; }
step()   { printf "\n  ${BOLD}${BLUE}▸ %s${NC}\n"  "$*"; }

_RL=60  # rule length
rule()    { printf "  ${DIM}%s${NC}\n" "$(printf '─%.0s' $(seq 1 $_RL))"; }
section() { echo; printf "  ${BOLD}%s${NC}\n" "$*"; rule; }

# ─── Spinner ──────────────────────────────────────────────────────────────────
_SPIN_PID=0
_SPIN_FRAMES='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

_spin_start() {
  [[ "${INTERACTIVE}" -ne 1 || "${DEBUG}" -ne 0 ]] && return
  local msg="$1"
  (
    local i=0 n=${#_SPIN_FRAMES}
    while true; do
      printf "\r  \033[36m%s\033[0m  %-56s" "${_SPIN_FRAMES:$((i % n)):1}" "$msg"
      sleep 0.09
      i=$(( i + 1 ))
    done
  ) &
  _SPIN_PID=$!
}

_spin_stop() {
  local rc=$1 msg=$2
  if [[ $_SPIN_PID -ne 0 ]]; then
    kill "$_SPIN_PID" 2>/dev/null || true
    _SPIN_PID=0
    printf "\r%*s\r" 70 ""
  fi
  if [[ $rc -eq 0 ]]; then
    printf "  ${GREEN}✔${NC}  %-56s\n" "$msg"
  else
    printf "  ${RED}✖${NC}  %-56s\n" "$msg"
  fi
}

# ─── Quiet runner ─────────────────────────────────────────────────────────────
# rq "Human label" "shell command"  — spinner in normal mode, raw in --debug
# rq_opt adds an optional-failure warn message and never exits the script.

_LOG_FILE="/tmp/signalscope-install-$$.log"
printf "# SignalScope installer log — %s\n\n" "$(date)" > "$_LOG_FILE"

rq() {
  local label="$1" cmd="$2"
  printf "\n=== %s ===\n" "$label" >> "$_LOG_FILE"
  if [[ "${DEBUG}" -eq 1 ]]; then
    step "$label"
    local rc=0
    eval "$cmd" || rc=$?
    if [[ $rc -ne 0 ]]; then
      echo
      err "Step failed (exit $rc): $label"
      printf "  Full log: %s\n" "$_LOG_FILE"
      echo
      exit 1
    fi
    return 0
  fi
  _spin_start "$label"
  local rc=0
  eval "$cmd" >> "$_LOG_FILE" 2>&1 || rc=$?
  _spin_stop $rc "$label"
  if [[ $rc -ne 0 ]]; then
    echo
    printf "  ${RED}Something went wrong. Last output:${NC}\n"
    tail -8 "$_LOG_FILE" | grep -v '^===' | sed 's/^/    /'
    echo
    printf "  Full log: %s\n" "$_LOG_FILE"
    printf "  ${CYAN}Re-run with --debug to see everything${NC}\n\n"
    exit 1
  fi
}

rq_opt() {
  local label="$1" cmd="$2" wmsg="${3:-}"
  printf "\n=== %s (optional) ===\n" "$label" >> "$_LOG_FILE"
  if [[ "${DEBUG}" -eq 1 ]]; then
    step "$label (optional)"
    local rc=0
    eval "$cmd" || rc=$?
    [[ $rc -ne 0 && -n "$wmsg" ]] && warn "$wmsg"
    return 0
  fi
  _spin_start "$label"
  local rc=0
  eval "$cmd" >> "$_LOG_FILE" 2>&1 || rc=$?
  _spin_stop $rc "$label"
  [[ $rc -ne 0 && -n "$wmsg" ]] && warn "$wmsg"
  return 0
}

# ─── App constants ────────────────────────────────────────────────────────────
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
ENABLE_NDI=""
FORCE_RETUNE=0
NGINX_FQDN=""
NGINX_FQDN_DEFAULT=""
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

# ─── Cleanup ──────────────────────────────────────────────────────────────────
cleanup() {
  if [[ "${_SPIN_PID:-0}" -ne 0 ]]; then
    kill "${_SPIN_PID}" 2>/dev/null || true
    _SPIN_PID=0
    printf "\r%*s\r" 70 ""
  fi
  if [[ -n "${TEMP_SOURCE_DIR:-}" && -d "${TEMP_SOURCE_DIR}" ]]; then
    rm -rf "${TEMP_SOURCE_DIR}" || true
  fi
  if [[ -n "${SELF_COPY:-}" && -f "${SELF_COPY}" ]]; then
    rm -f "${SELF_COPY}" || true
  fi
}
trap cleanup EXIT

# ─── Usage ────────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
${APP_NAME} installer

Interactive:
  /bin/bash <(curl -fsSL https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh)

Non-interactive:
  curl -fsSL .../install_signalscope.sh | bash -s -- --service --sdr

Options:
  --service                 Install and enable systemd service
  --no-service              Skip systemd service installation
  --sdr                     Install RTL-SDR tooling, pyrtlsdr, and redsea build deps
  --no-sdr                  Skip SDR support
  --livewire                Apply Livewire/AES67 UDP kernel tuning (2× standard buffer sizes)
  --no-livewire             Apply standard UDP tuning only
  --icecast                 Install Icecast2 (for the Icecast Streaming plugin)
  --no-icecast              Skip Icecast2 installation
  --ndi                     Install NDI support (ndi-python) for vMix Caller video preview
  --no-ndi                  Skip NDI support
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
  --debug                   Show all command output (no spinners) — for troubleshooting
  -h, --help                Show help
EOF
}

# ─── Banner ───────────────────────────────────────────────────────────────────
print_banner() {
  echo
  printf "${BOLD}"
  echo "   _____ _                   _  _____"
  echo "  / ____(_)                 | |/ ____|"
  echo " | (___  _  __ _ _ __   __ _| | (___   ___ ___  _ __   ___"
  echo "  \___ \| |/ _\` | '_ \ / _\` | |\___ \ / __/ _ \| '_ \ / _ \\"
  echo "  ____) | | (_| | | | | (_| | |____) | (_| (_) | |_) |  __/"
  echo " |_____/|_|\__, |_| |_|\__,_|_|_____/ \___\___/| .__/ \___|"
  echo "             __/ |                              | |"
  echo "            |___/                               |_|"
  printf "${NC}"
  echo
  printf "  ${DIM}Broadcast Signal Intelligence  ·  Production Installer${NC}\n"
  rule
  printf "  ${DIM}Trouble? Re-run with ${NC}${CYAN}--debug${NC}${DIM} to see full command output.${NC}\n"
  rule
  echo
}

# ─── Prompts ──────────────────────────────────────────────────────────────────
ask_yes_no() {
  local prompt="$1" default="$2" reply

  if [[ "${INTERACTIVE}" -ne 1 ]]; then
    [[ "$default" == "y" ]] && return 0 || return 1
  fi

  while true; do
    if [[ "$default" == "y" ]]; then
      read -r -p "  ${BOLD}${prompt}${NC} [Y/n]: " reply || true
      reply="${reply:-Y}"
    else
      read -r -p "  ${BOLD}${prompt}${NC} [y/N]: " reply || true
      reply="${reply:-N}"
    fi
    case "${reply,,}" in
      y|yes) return 0 ;;
      n|no)  return 1 ;;
      *) warn "Please answer y or n." ;;
    esac
  done
}

ask_value() {
  local prompt="$1" default="$2" reply

  if [[ "${INTERACTIVE}" -ne 1 ]]; then
    echo "$default"
    return 0
  fi

  read -r -p "  ${BOLD}${prompt}${NC} [${DIM}${default}${NC}]: " reply || true
  echo "${reply:-$default}"
}

# Print a feature question with a one-line description.
# Returns the exit code of ask_yes_no (0=yes, 1=no).
ask_feature() {
  local title="$1" desc="$2" default="$3"
  echo
  printf "  ${BOLD}${title}${NC}\n"
  printf "  ${DIM}${desc}${NC}\n"
  ask_yes_no "→" "$default"
}

# ─── Detection helpers ────────────────────────────────────────────────────────
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
  LEGACY_APP_PATH="$(awk -F= '/^ExecStart=/{print $2; exit}' "${svc}" \
                    | sed 's/.*python[0-9.]*[[:space:]]\+//' | awk '{print $1}' | xargs)"

  if [[ -z "${LEGACY_WORKDIR}" && -n "${LEGACY_APP_PATH}" ]]; then
    LEGACY_WORKDIR="$(dirname "${LEGACY_APP_PATH}")"
  fi
  return 0
}

migrate_legacy_livewire_install() {
  if [[ "${LEGACY_INSTALL_DETECTED}" != "1" ]]; then
    return 0
  fi

  section "Migrating from LivewireAIMonitor"
  warn "Legacy ${LEGACY_SERVICE_NAME} service detected — stopping and migrating config."

  local svc="/etc/systemd/system/${LEGACY_SERVICE_NAME}.service"
  [[ -f "${svc}" ]] || svc="/lib/systemd/system/${LEGACY_SERVICE_NAME}.service"

  ${SUDO} systemctl stop    "${LEGACY_SERVICE_NAME}" >/dev/null 2>&1 || true
  ${SUDO} systemctl disable "${LEGACY_SERVICE_NAME}" >/dev/null 2>&1 || true

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

  if [[ -f "/etc/systemd/system/${LEGACY_SERVICE_NAME}.service" ]]; then
    ${SUDO} rm -f "/etc/systemd/system/${LEGACY_SERVICE_NAME}.service"
  fi
  ${SUDO} rm -f "/etc/systemd/system/multi-user.target.wants/${LEGACY_SERVICE_NAME}.service" || true
  ${SUDO} systemctl daemon-reload

  if [[ "${copied_any}" == "1" ]]; then
    ok "Config and AI models migrated to ${INSTALL_ROOT}"
  else
    warn "No config or model files found to migrate"
  fi
}

# ─── Arg parsing ──────────────────────────────────────────────────────────────
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --service)     ENABLE_SERVICE=1;  shift ;;
      --no-service)  ENABLE_SERVICE=0;  shift ;;
      --sdr)         ENABLE_SDR=1;      shift ;;
      --no-sdr)      ENABLE_SDR=0;      shift ;;
      --nginx)       ENABLE_NGINX=1;    shift ;;
      --no-nginx)    ENABLE_NGINX=0;    shift ;;
      --fqdn)
        [[ $# -ge 2 ]] || { err "--fqdn requires a value"; exit 1; }
        NGINX_FQDN="$2"; shift 2 ;;
      --https)       NGINX_HTTPS=1;     shift ;;
      --no-https)    NGINX_HTTPS=0;     shift ;;
      --force)       FORCE_OVERWRITE=1; shift ;;
      --livewire)    ENABLE_LIVEWIRE=1; shift ;;
      --no-livewire) ENABLE_LIVEWIRE=0; shift ;;
      --icecast)     ENABLE_ICECAST=1;  shift ;;
      --no-icecast)  ENABLE_ICECAST=0;  shift ;;
      --ndi)         ENABLE_NDI=1;      shift ;;
      --no-ndi)      ENABLE_NDI=0;      shift ;;
      --retune)      FORCE_RETUNE=1;    shift ;;
      --pi-overclock)    ENABLE_OVERCLOCK=1; shift ;;
      --no-pi-overclock) ENABLE_OVERCLOCK=0; shift ;;
      --install-dir)
        [[ $# -ge 2 ]] || { err "--install-dir requires a value"; exit 1; }
        INSTALL_ROOT="$2"; shift 2 ;;
      --debug)  DEBUG=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *)  err "Unknown option: $1"; usage; exit 1 ;;
    esac
  done
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "Missing required command: $1"; exit 1; }
}

make_rerunnable_copy_if_needed() {
  if [[ -f "${SCRIPT_PATH}" && "${SCRIPT_PATH}" != /dev/fd/* && "${SCRIPT_PATH}" != /proc/self/fd/* ]]; then
    echo "${SCRIPT_PATH}"; return 0
  fi

  SELF_COPY="$(mktemp /tmp/signalscope-installer.XXXXXX.sh)"
  chmod 700 "${SELF_COPY}"

  if [[ -r "${SCRIPT_PATH}" ]]; then
    cat "${SCRIPT_PATH}" > "${SELF_COPY}"
  else
    err "Could not read installer source for sudo re-launch."
    err "Workaround: save the script to a file and run it locally."
    exit 1
  fi
  echo "${SELF_COPY}"
}

init_sudo() {
  if [[ $EUID -ne 0 ]]; then
    need_cmd sudo
    SUDO="sudo"
  else
    SUDO=""
  fi
}

auth_sudo() {
  if [[ -n "${SUDO}" ]]; then
    info "Requesting administrator access (sudo)..."
    sudo -v
  fi
}

# ─── RTL-SDR / USB helpers ────────────────────────────────────────────────────
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
  if [[ -f /sys/module/usbcore/parameters/usbfs_memory_mb ]]; then
    echo 0 | ${SUDO} tee /sys/module/usbcore/parameters/usbfs_memory_mb > /dev/null
  fi

  local rc="/etc/rc.local"
  local marker="usbfs_memory_mb"
  if ! grep -q "${marker}" "${rc}" 2>/dev/null; then
    if [[ -f "${rc}" ]]; then
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
    ok "usbfs_memory_mb=0 persisted in ${rc}"
  else
    ok "usbfs_memory_mb already configured"
  fi
}

# ─── Raspberry Pi helpers ─────────────────────────────────────────────────────
detect_pi_model() {
  PI_MODEL_STR=""; PI_GEN=0
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
  elif [[ -f /boot/config.txt          ]]; then boot_config="/boot/config.txt"
  else
    warn "Cannot find boot config.txt — skipping overclock"
    return 1
  fi

  if grep -q "# SignalScope overclock" "${boot_config}" 2>/dev/null; then
    ok "Raspberry Pi overclock already applied"
    return 0
  fi

  local oc_block=""
  case "${PI_GEN}" in
    4)
      info "Pi 4: arm_freq=2000 MHz, over_voltage=6, gpu_freq=750 — requires heatsink"
      oc_block=$'over_voltage=6\narm_freq=2000\ngpu_freq=750'
      ;;
    3)
      info "Pi 3: arm_freq=1450 MHz, over_voltage=2, gpu_freq=500 — requires heatsink"
      oc_block=$'over_voltage=2\narm_freq=1450\ngpu_freq=500'
      ;;
    *)
      warn "Overclocking not configured for Pi ${PI_GEN} — skipping"
      return 0
      ;;
  esac

  warn "Overclocking requires adequate cooling. Ensure a heatsink is fitted."
  warn "A reboot is required to take effect."

  ${SUDO} tee -a "${boot_config}" > /dev/null <<EOF

# SignalScope overclock — applied $(date '+%Y-%m-%d')
${oc_block}
EOF
  ok "Overclock settings written — reboot to apply"
}

# ─── redsea build ─────────────────────────────────────────────────────────────
install_redsea() {
  if [[ "${ENABLE_SDR}" != "1" ]]; then return 0; fi

  if command -v redsea >/dev/null 2>&1; then
    ok "redsea already installed at $(command -v redsea)"
    return 0
  fi

  local build_root="/tmp/redsea-build.$$"
  rm -rf "${build_root}"
  git clone --depth 1 https://github.com/windytan/redsea.git "${build_root}" >/dev/null 2>&1
  meson setup "${build_root}/build" "${build_root}" --wipe >/dev/null
  meson compile -C "${build_root}/build" >/dev/null
  if ! sudo meson install -C "${build_root}/build" >/dev/null; then
    rm -rf "${build_root}"
    return 1
  fi
  rm -rf "${build_root}"

  if command -v redsea >/dev/null 2>&1; then
    ok "redsea installed"
  else
    warn "redsea install completed but binary not found in PATH"
  fi
}

# ─── Version helpers ──────────────────────────────────────────────────────────
extract_build_version() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  grep -oP '(?<=SignalScope-)\d+\.\d+\.\d+' "$file" 2>/dev/null | head -1 || true
}

version_gt() {
  python3 - <<PYEOF
a = tuple(int(x) for x in ('${1:-0.0.0}'.split('.')))
b = tuple(int(x) for x in ('${2:-0.0.0}'.split('.')))
import sys; sys.exit(0 if a > b else 1)
PYEOF
}

INSTALLED_VER=""
WINNING_VER=""
WINNING_SOURCE=""
IS_UPDATE=0

resolve_best_source() {
  local cwd installed_app local_app remote_app fetch_ok local_ver remote_ver

  cwd="$(pwd)"
  installed_app="${INSTALL_ROOT}/${APP_PY_NAME}"
  local_app="${cwd}/${APP_PY_NAME}"

  INSTALLED_VER=$(extract_build_version "${installed_app}")
  if [[ -n "${INSTALLED_VER}" ]]; then
    info "Installed : ${INSTALLED_VER}"
    IS_UPDATE=1
  fi

  local_ver=$(extract_build_version "${local_app}")
  [[ -n "${local_ver}" ]] && info "Local file: ${local_ver}"

  # Fetch from GitHub (wrapped in spinner when not debug)
  TEMP_SOURCE_DIR="$(mktemp -d /tmp/signalscope-src.XXXXXX)"
  remote_app="${TEMP_SOURCE_DIR}/${APP_PY_NAME}"
  fetch_ok=0

  _do_fetch() {
    if command -v git >/dev/null 2>&1; then
      git clone --depth 1 "${REPO_URL}" "${TEMP_SOURCE_DIR}" >/dev/null 2>&1 && { fetch_ok=1; return; } || true
    fi
    mkdir -p "${TEMP_SOURCE_DIR}"
    if curl -fsSL --max-time 30 "${RAW_BASE_URL}/${APP_PY_NAME}" -o "${remote_app}" 2>/dev/null; then
      fetch_ok=1
      mkdir -p "${TEMP_SOURCE_DIR}/static"
      for asset in signalscope_icon.ico signalscope_icon.png signalscope_logo.jpg signalscope_logo.png; do
        curl -fsSL --max-time 10 "${RAW_BASE_URL}/static/${asset}" \
          -o "${TEMP_SOURCE_DIR}/static/${asset}" 2>/dev/null || true
      done
    fi
  }

  if [[ "${DEBUG}" -eq 1 ]]; then
    step "Checking GitHub for latest version"
    _do_fetch || true
  else
    _spin_start "Checking GitHub for latest version"
    _do_fetch >> "$_LOG_FILE" 2>&1 || true
    _spin_stop 0 "Checked GitHub for latest version"
  fi

  remote_ver=""
  if [[ "${fetch_ok}" -eq 1 && -f "${remote_app}" ]]; then
    remote_ver=$(extract_build_version "${remote_app}")
    [[ -n "${remote_ver}" ]] && info "GitHub    : ${remote_ver}" \
                              || warn "Could not parse version from GitHub"
  fi

  WINNING_VER="${INSTALLED_VER:-0.0.0}"
  WINNING_SOURCE="installed"

  if [[ -n "${local_ver}" && "${local_app}" != "${installed_app}" ]]; then
    if version_gt "${local_ver}" "${WINNING_VER}" || [[ "${local_ver}" == "${WINNING_VER}" ]]; then
      WINNING_VER="${local_ver}"; WINNING_SOURCE="local"
    fi
  fi
  if [[ -n "${remote_ver}" ]] && version_gt "${remote_ver}" "${WINNING_VER}"; then
    WINNING_VER="${remote_ver}"; WINNING_SOURCE="remote"
  fi

  case "${WINNING_SOURCE}" in
    local)
      SOURCE_DIR="${cwd}"; SOURCE_APP="${local_app}"
      if [[ -n "${INSTALLED_VER}" ]]; then
        version_gt "${WINNING_VER}" "${INSTALLED_VER}" \
          && ok "Update available: ${INSTALLED_VER} → ${WINNING_VER}  (local file)" \
          || ok "Reinstalling ${WINNING_VER} from local file"
      else
        ok "Source: local file ${WINNING_VER}"
      fi
      ;;
    remote)
      SOURCE_DIR="${TEMP_SOURCE_DIR}"; SOURCE_APP="${remote_app}"
      [[ -n "${INSTALLED_VER}" ]] \
        && ok "Update available: ${INSTALLED_VER} → ${WINNING_VER}  (GitHub)" \
        || ok "Source: GitHub ${WINNING_VER}"
      ;;
    installed)
      SOURCE_DIR="${INSTALL_ROOT}"; SOURCE_APP="${installed_app}"
      ok "Already up to date: ${WINNING_VER}"
      ;;
  esac

  if [[ ! -f "${SOURCE_APP}" ]]; then
    local legacy_cwd="${cwd}/${LEGACY_APP_PY}"
    if [[ -f "${legacy_cwd}" ]]; then
      SOURCE_DIR="${cwd}"; SOURCE_APP="${legacy_cwd}"
      warn "Using legacy app file: ${SOURCE_APP}"
    elif [[ -f "${INSTALL_ROOT}/${LEGACY_APP_PY}" ]]; then
      SOURCE_DIR="${INSTALL_ROOT}"; SOURCE_APP="${INSTALL_ROOT}/${LEGACY_APP_PY}"
      warn "Using legacy installed file: ${SOURCE_APP}"
    else
      err "No application file found — cannot continue"; exit 1
    fi
  fi
}

# ─── nginx ────────────────────────────────────────────────────────────────────
configure_nginx() {
  local fqdn="$1" use_https="${2:-0}"
  local conf_file="/etc/nginx/sites-available/signalscope"
  local enabled_dir="/etc/nginx/sites-enabled"

  if [[ -z "${fqdn}" ]]; then
    ${SUDO} tee "${conf_file}" > /dev/null <<EOF
# SignalScope nginx reverse proxy (HTTP, no FQDN)
server {
    listen 80 default_server;
    server_name _;
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
        proxy_pass         http://127.0.0.1:5000;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
EOF
  else
    ${SUDO} tee "${conf_file}" > /dev/null <<EOF
# SignalScope nginx reverse proxy
server {
    listen 80;
    server_name ${fqdn};
    client_max_body_size 20m;
    location /.well-known/acme-challenge/ { root /var/www/html; }
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
  ${SUDO} systemctl enable --now nginx >/dev/null 2>&1
  ${SUDO} nginx -t 2>/dev/null && { ${SUDO} nginx -s reload 2>/dev/null || ${SUDO} systemctl restart nginx; }
  ok "nginx configured${fqdn:+ for ${fqdn}}"

  if [[ "${use_https}" == "1" && -n "${fqdn}" ]]; then
    ${SUDO} apt-get install -y certbot python3-certbot-nginx >/dev/null 2>&1
    info "Requesting Let's Encrypt certificate for ${fqdn}..."
    info "Port 80 must be reachable from the internet for the ACME challenge."
    if ${SUDO} certbot --nginx -d "${fqdn}" --non-interactive --agree-tos \
         --register-unsafely-without-email --redirect 2>&1; then
      ok "TLS certificate issued for ${fqdn}"
      ${SUDO} nginx -s reload
    else
      warn "certbot failed — retry manually: sudo certbot --nginx -d ${fqdn}"
    fi
    ${SUDO} systemctl enable --now certbot.timer 2>/dev/null \
      || ${SUDO} systemctl enable --now snap.certbot.renew.timer 2>/dev/null || true
    ok "certbot auto-renewal enabled"
  fi
}

# ─── Logrotate / watchdog / service helpers ───────────────────────────────────
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
    err "Backup: ${backup}"
    err "Logs: journalctl -fu ${SERVICE_NAME}"
  fi
}

verify_app_health() {
  local max_tries=12   # 12 × 5 s = 60 s
  local i
  echo
  printf "  ${DIM}Waiting for SignalScope to respond on port 5000...${NC}\n"
  for i in $(seq 1 "${max_tries}"); do
    sleep 5
    if curl -fsS --max-time 4 http://127.0.0.1:5000/ >/dev/null 2>&1; then
      printf "\r  ${GREEN}✔${NC}  Application is up and responding          \n"
      if [[ -n "${APP_BACKUP}" && -f "${APP_BACKUP}" ]]; then
        ${SUDO} rm -f "${APP_BACKUP}" 2>/dev/null || true
      fi
      return 0
    fi
    printf "\r  ${CYAN}◌${NC}  Starting up... (%ds / %ds max)" "$((i*5))" "$((max_tries*5))"
  done
  printf "\r  ${RED}✖${NC}  Application did not respond within 60 s     \n"
  err "Check logs: journalctl -fu ${SERVICE_NAME}"
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

if curl -fsS --max-time 8 http://127.0.0.1:5000/ >/dev/null 2>&1; then
  :
elif systemctl is-active --quiet "${APP_SERVICE}" 2>/dev/null; then
  logger -t "${LOG_TAG}" "Port 5000 unresponsive — restarting ${APP_SERVICE}"
  systemctl restart "${APP_SERVICE}"
elif systemctl is-enabled --quiet "${APP_SERVICE}" 2>/dev/null; then
  logger -t "${LOG_TAG}" "${APP_SERVICE} not running — starting"
  systemctl start "${APP_SERVICE}" || true
fi

if systemctl is-enabled --quiet "${NGINX_SERVICE}" 2>/dev/null; then
  if curl -fsk --max-time 8 https://127.0.0.1/ >/dev/null 2>&1 || \
     curl -fs  --max-time 8 http://127.0.0.1/  >/dev/null 2>&1; then
    :
  elif systemctl is-active --quiet "${NGINX_SERVICE}" 2>/dev/null; then
    logger -t "${LOG_TAG}" "nginx not responding — restarting"
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
  if command -v apt-get &>/dev/null; then
    ${SUDO} apt-get install -y linuxptp >/dev/null 2>&1 \
      || { warn "linuxptp install failed — PTP monitoring will use passive listener only"; return 0; }
  else
    warn "apt-get not available — skipping linuxptp install"
    return 0
  fi

  local ptp_iface
  ptp_iface=$(ip route get 1.1.1.1 2>/dev/null \
    | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)
  [[ -z "$ptp_iface" ]] && ptp_iface="eth0"

  ${SUDO} mkdir -p /etc/linuxptp

  ${SUDO} tee /etc/linuxptp/ptp4l.conf > /dev/null <<EOF
# ptp4l configuration — managed by SignalScope installer
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
      ok "Created ptp4l.service"
    fi
  fi

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
      ok "PTP clock monitor running (slave-only, non-disciplining) — interface: ${ptp_iface}"
    else
      warn "ptp4l failed to start — PTP traffic may not be present on ${ptp_iface}"
    fi
  else
    warn "ptp4l binary not found — PTP monitoring will use passive listener only"
  fi
}

create_service() {
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

# ─── Pre-run summary ──────────────────────────────────────────────────────────
print_plan() {
  section "READY TO INSTALL"
  local mode_str
  if [[ "${IS_UPDATE}" -eq 1 ]]; then
    mode_str="Update  ${INSTALLED_VER} → ${WINNING_VER}  (${WINNING_SOURCE})"
  elif [[ "${LEGACY_INSTALL_DETECTED}" -eq 1 ]]; then
    mode_str="Fresh install ${WINNING_VER}  (migrating from LivewireAIMonitor)"
  else
    mode_str="Fresh install ${WINNING_VER}"
  fi

  local os_str arch_str
  os_str="$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-Linux}" || echo Linux)"
  arch_str="$(dpkg --print-architecture 2>/dev/null || uname -m)"

  printf "  %-18s %s\n" "Version" "${WINNING_VER}"
  printf "  %-18s %s\n" "Mode" "$mode_str"
  printf "  %-18s %s\n" "OS" "$os_str  ($arch_str)"
  printf "  %-18s %s\n" "Install path" "${INSTALL_ROOT}"
  echo
  _pf() { local lbl="$1" ok="$2" note="$3"
    if [[ "$ok" == "1" ]]; then
      printf "  ${GREEN}✔${NC}  %-24s ${DIM}%s${NC}\n" "$lbl" "$note"
    else
      printf "  ${DIM}✗  %-24s%s${NC}\n" "$lbl" "${note:+(skipped)}"
    fi
  }
  _pf "Background service"  "${ENABLE_SERVICE:-0}" "starts at boot, auto-restarts"
  _pf "RTL-SDR dongles"     "${ENABLE_SDR:-0}"     "FM / DAB radio monitoring"
  _pf "Livewire/AES67"      "${ENABLE_LIVEWIRE:-0}" "large UDP kernel buffers"
  _pf "Icecast2"            "${ENABLE_ICECAST:-0}"  "re-streaming plugin"
  _pf "NDI support"         "${ENABLE_NDI:-0}"      "vMix Caller video preview"
  if [[ "${ENABLE_NGINX:-0}" == "1" ]]; then
    _pf "nginx proxy"       "1" "${NGINX_FQDN:-HTTP-only}"
    _pf "HTTPS (Let's Encrypt)" "${NGINX_HTTPS:-0}" "${NGINX_FQDN}"
  else
    _pf "nginx proxy"       "0" ""
  fi
  [[ -n "${PI_MODEL_STR}" ]] && _pf "Pi overclock" "${ENABLE_OVERCLOCK:-0}" "${PI_MODEL_STR}"
  rule
}

# ─── Post-run summary ─────────────────────────────────────────────────────────
print_done() {
  local url
  if [[ "${ENABLE_NGINX:-0}" == "1" && -n "${NGINX_FQDN:-}" ]]; then
    [[ "${NGINX_HTTPS:-0}" == "1" ]] && url="https://${NGINX_FQDN}" || url="http://${NGINX_FQDN}"
  else
    local myip
    myip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<server-ip>")
    url="http://${myip}:5000"
  fi

  local w=60
  local _hr; _hr="$(printf '─%.0s' $(seq 1 $w))"

  echo
  printf "  ${_hr}\n"
  if [[ "${IS_UPDATE}" -eq 1 && "${WINNING_SOURCE}" != "installed" ]]; then
    printf "  ${GREEN}${BOLD}✔  SignalScope updated to ${WINNING_VER}${NC}\n"
  else
    printf "  ${GREEN}${BOLD}✔  SignalScope ${WINNING_VER} is ready!${NC}\n"
  fi
  printf "  ${_hr}\n"
  echo
  printf "  Open in your browser:\n"
  printf "    ${BOLD}${CYAN}%s${NC}\n" "$url"
  echo
  if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    printf "  Manage the service:\n"
    printf "    sudo systemctl status %-28s${DIM}(status)${NC}\n" "${SERVICE_NAME}"
    printf "    journalctl -fu %-34s${DIM}(live logs)${NC}\n"    "${SERVICE_NAME}"
  else
    printf "  Run manually:\n"
    printf "    source %s/venv/bin/activate\n" "${INSTALL_ROOT}"
    printf "    python %s\n" "${TARGET_APP}"
  fi
  echo
  printf "  Install log: ${DIM}%s${NC}\n" "$_LOG_FILE"
  printf "  ${_hr}\n"
  echo
  if [[ "${ENABLE_OVERCLOCK:-0}" == "1" && "${PI_GEN:-0}" -ge 3 ]]; then
    warn "Raspberry Pi overclock written — reboot to apply:  sudo reboot"
    echo
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════════════════════════
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

  init_sudo

  # ── Install directory ────────────────────────────────────────────────────
  echo
  printf "  ${BOLD}Install directory${NC}\n"
  printf "  ${DIM}Where SignalScope files will be placed. The default is fine for most installs.${NC}\n"
  INSTALL_ROOT="$(ask_value "Path" "${INSTALL_ROOT}")"

  # ── Legacy detection ─────────────────────────────────────────────────────
  if detect_legacy_livewire_install; then
    echo
    warn "Old LivewireAIMonitor service detected${LEGACY_WORKDIR:+ at ${LEGACY_WORKDIR}}."
    warn "It will be stopped and your config/models migrated to ${INSTALL_ROOT}."
    IS_UPDATE=0
  fi

  # ── Version check ─────────────────────────────────────────────────────────
  echo
  resolve_best_source

  if [[ "${LEGACY_INSTALL_DETECTED}" -eq 1 ]]; then
    IS_UPDATE=0
  fi

  # ── Already current? ──────────────────────────────────────────────────────
  if [[ "${IS_UPDATE}" -eq 1 && "${WINNING_SOURCE}" == "installed" && "${FORCE_OVERWRITE}" -ne 1 ]]; then
    echo
    ok "SignalScope ${WINNING_VER} is already the latest version."
    if [[ "${INTERACTIVE}" -eq 1 ]]; then
      ask_yes_no "Reinstall / repair dependencies?" "n" || { warn "Nothing to do."; exit 0; }
    else
      exit 0
    fi
  fi

  # ── Interactive wizard — feature selection ────────────────────────────────
  if [[ "${IS_UPDATE}" -eq 1 ]]; then
    section "UPDATE OPTIONS"
    info "Updating ${INSTALLED_VER} → ${WINNING_VER}"

    if [[ -z "${ENABLE_SERVICE}" ]]; then
      local _svc_default="n"
      systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null && _svc_default="y"
      if ask_feature \
          "Background service" \
          "Reinstall/re-enable the systemd service, watchdog timer, and environment file." \
          "$_svc_default"; then
        ENABLE_SERVICE=1
      else
        ENABLE_SERVICE=0
      fi
    fi

    if [[ -z "${ENABLE_SDR}" ]]; then
      local _sdr_default="n"
      command -v rtl_fm &>/dev/null && _sdr_default="y"
      if ask_feature \
          "RTL-SDR kernel fixes" \
          "Re-apply the USB buffer and kernel module blacklist for RTL-SDR dongles." \
          "$_sdr_default"; then
        ENABLE_SDR=1
      else
        ENABLE_SDR=0
      fi
    fi

    if [[ -z "${ENABLE_LIVEWIRE}" ]]; then
      local _lw_default="n"
      grep -q "Livewire" /etc/sysctl.d/99-signalscope-network.conf 2>/dev/null && _lw_default="y"
      if ask_feature \
          "Livewire / AES67 multicast inputs" \
          "Re-apply large UDP kernel buffers for high-rate RTP multicast. Enables --retune." \
          "$_lw_default"; then
        ENABLE_LIVEWIRE=1
        FORCE_RETUNE=1
      else
        ENABLE_LIVEWIRE=0
      fi
    fi

    if [[ -z "${ENABLE_ICECAST}" ]]; then
      local _ice_default="n"
      dpkg -l icecast2 &>/dev/null && _ice_default="y"
      if ask_feature \
          "Icecast2" \
          "Install or reinstall Icecast2 for the Icecast Streaming plugin." \
          "$_ice_default"; then
        ENABLE_ICECAST=1
      else
        ENABLE_ICECAST=0
      fi
    fi

    if [[ -z "${ENABLE_NDI}" ]]; then
      local _ndi_default="n"
      "${INSTALL_ROOT}/venv/bin/python" -c "import ndi" &>/dev/null && _ndi_default="y"
      if ask_feature \
          "NDI support  (vMix Caller video preview)" \
          "Install or reinstall ndi-python for the vMix Caller plugin." \
          "$_ndi_default"; then
        ENABLE_NDI=1
      else
        ENABLE_NDI=0
      fi
    fi

  else
    # ── Fresh install wizard ────────────────────────────────────────────────
    section "SETUP OPTIONS"
    printf "  ${DIM}Answer a few questions to customise the install.${NC}\n"
    printf "  ${DIM}Press Enter to accept the default shown in brackets.${NC}\n"

    if [[ -z "${ENABLE_SERVICE}" ]]; then
      if ask_feature \
          "Background service" \
          "Starts automatically when this machine boots and restarts if it crashes." \
          "y"; then
        ENABLE_SERVICE=1
      else
        ENABLE_SERVICE=0
      fi
    fi

    if [[ -z "${ENABLE_SDR}" ]]; then
      if ask_feature \
          "RTL-SDR radio dongles" \
          "Required for FM and DAB radio monitoring with a USB dongle. Skip for IP-only setups." \
          "n"; then
        ENABLE_SDR=1
      else
        ENABLE_SDR=0
      fi
    fi

    if [[ -z "${ENABLE_LIVEWIRE}" ]]; then
      if ask_feature \
          "Livewire / AES67 multicast inputs" \
          "Raises kernel UDP receive buffers for high-rate RTP multicast streams. Skip for HTTP/unicast-only setups." \
          "n"; then
        ENABLE_LIVEWIRE=1
      else
        ENABLE_LIVEWIRE=0
      fi
    fi

    if [[ -z "${ENABLE_ICECAST}" ]]; then
      if ask_feature \
          "Icecast2" \
          "Lets SignalScope re-stream any monitored input as a live Icecast2 stream." \
          "n"; then
        ENABLE_ICECAST=1
      else
        ENABLE_ICECAST=0
      fi
    fi

    if [[ -z "${ENABLE_NDI}" ]]; then
      if ask_feature \
          "NDI support  (vMix Caller video preview)" \
          "Installs ndi-python so the vMix Caller plugin can receive NDI video directly from vMix." \
          "n"; then
        ENABLE_NDI=1
      else
        ENABLE_NDI=0
      fi
    fi
  fi

  # ── nginx / HTTPS questions (both modes) ──────────────────────────────────
  EXISTING_PROXY=0
  detect_existing_proxy_setup && EXISTING_PROXY=1 || true

  if [[ -z "${ENABLE_NGINX}" ]]; then
    if [[ "${IS_UPDATE}" -eq 1 && "${EXISTING_PROXY}" -eq 0 ]]; then
      if ask_feature \
          "nginx reverse proxy" \
          "No proxy is currently installed. nginx is recommended for HTTPS and production use." \
          "y"; then
        ENABLE_NGINX=1
      else
        ENABLE_NGINX=0
      fi
    elif [[ "${IS_UPDATE}" -ne 1 ]]; then
      if ask_feature \
          "nginx reverse proxy" \
          "Puts SignalScope behind nginx — required for HTTPS and cleaner URLs on port 80." \
          "y"; then
        ENABLE_NGINX=1
      else
        ENABLE_NGINX=0
      fi
    else
      # ── Repair / update run with existing nginx config ──────────────────
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
        warn "nginx is configured for '${_exist_fqdn}' but no TLS certificate was found."
        warn "The Let's Encrypt step most likely failed during the original install."
        _needs_repair=1
      fi
      if [[ "${_needs_repair}" -eq 1 ]]; then
        info "Existing config: ${_exist_fqdn:+FQDN ${_exist_fqdn}}"
        if ask_yes_no "Remove the broken nginx config and start fresh?" "y"; then
          ${SUDO} rm -f /etc/nginx/sites-available/signalscope
          ${SUDO} rm -f /etc/nginx/sites-enabled/signalscope
          [[ -n "${_exist_fqdn}" ]] && ${SUDO} rm -f "/etc/letsencrypt/renewal/${_exist_fqdn}.conf" 2>/dev/null || true
          ok "Existing nginx config cleared"
          EXISTING_PROXY=0; ENABLE_NGINX=1
          [[ -n "${_exist_fqdn}" ]] && NGINX_FQDN_DEFAULT="${_exist_fqdn}"
        else
          ENABLE_NGINX=0
        fi
      else
        ok "nginx appears healthy${_exist_fqdn:+ (serving ${_exist_fqdn})}."
        if ask_yes_no "Reconfigure nginx (change domain or TLS)?" "n"; then
          ${SUDO} rm -f /etc/nginx/sites-available/signalscope
          ${SUDO} rm -f /etc/nginx/sites-enabled/signalscope
          ok "Existing nginx config cleared"
          EXISTING_PROXY=0; ENABLE_NGINX=1
          [[ -n "${_exist_fqdn}" ]] && NGINX_FQDN_DEFAULT="${_exist_fqdn}"
        else
          ENABLE_NGINX=0
        fi
      fi
    fi
  fi

  if [[ "${ENABLE_NGINX}" == "1" && -z "${NGINX_FQDN}" ]]; then
    echo
    printf "  ${BOLD}Domain name for nginx${NC}\n"
    printf "  ${DIM}Your server's public hostname (e.g. signalscope.example.com).${NC}\n"
    printf "  ${DIM}Leave blank to skip HTTPS and serve plain HTTP on port 80.${NC}\n"
    NGINX_FQDN="$(ask_value "Domain" "${NGINX_FQDN_DEFAULT:-}")"
  fi

  if [[ "${ENABLE_NGINX}" == "1" && -n "${NGINX_FQDN}" && -z "${NGINX_HTTPS}" ]]; then
    if ask_feature \
        "HTTPS certificate  (Let's Encrypt)" \
        "Automatically issues and renews a free TLS certificate for ${NGINX_FQDN}. Port 80 must be open to the internet." \
        "y"; then
      NGINX_HTTPS=1
    else
      NGINX_HTTPS=0
    fi
  fi

  # ── Summary and confirm ────────────────────────────────────────────────────
  print_plan

  if [[ "${INTERACTIVE}" == "1" ]]; then
    ask_yes_no "Continue with install?" "y" || { warn "Cancelled."; exit 0; }
  fi

  # All interactive questions answered — authenticate sudo now.
  auth_sudo

  SERVICE_USER="${SUDO_USER:-$USER}"
  SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  IS_ARM=0
  case "$ARCH" in armhf|arm64|aarch64) IS_ARM=1 ;; esac

  # ── Raspberry Pi detection & optional overclock ───────────────────────────
  detect_pi_model || true
  if [[ "${PI_GEN}" -ge 3 ]]; then
    info "Detected: ${PI_MODEL_STR}"
    if [[ "${PI_GEN}" -eq 5 ]]; then
      ENABLE_OVERCLOCK=0
    else
      if [[ -z "${ENABLE_OVERCLOCK}" && "${INTERACTIVE}" -eq 1 ]]; then
        if ask_feature \
            "Raspberry Pi overclock" \
            "Boosts CPU frequency for better DAB decode and AI performance. Requires a heatsink — do not enable without adequate cooling." \
            "n"; then
          ENABLE_OVERCLOCK=1
        else
          ENABLE_OVERCLOCK=0
        fi
      fi
      if [[ "${ENABLE_OVERCLOCK}" == "1" ]]; then
        apply_pi_overclock
      fi
    fi
  fi

  # ─────────────────────────────────────────────────────────────────────────
  section "INSTALLING"
  # ─────────────────────────────────────────────────────────────────────────

  # ── System packages — fresh install only ──────────────────────────────────
  if [[ "${IS_UPDATE}" -ne 1 ]]; then
    rq "Updating package list" "${SUDO} apt-get update -y"
    rq "Installing system packages" \
      "DEBIAN_FRONTEND=noninteractive ${SUDO} apt-get install -y \
        python3 python3-venv python3-dev python3-setuptools \
        build-essential pkg-config git curl wget ca-certificates rsync \
        ffmpeg ethtool net-tools iproute2 jq \
        libffi-dev libssl-dev libportaudio2"

    if [[ "${ENABLE_SDR}" == "1" ]]; then
      rq "Installing RTL-SDR packages" \
        "DEBIAN_FRONTEND=noninteractive ${SUDO} apt-get install -y \
          rtl-sdr librtlsdr-dev welle.io \
          libsndfile1 libsndfile1-dev libliquid-dev libfftw3-dev nlohmann-json3-dev \
          meson ninja-build || true"
      ensure_rtlsdr_blacklist
      ensure_usbfs_unlimited
    fi

    if [[ "${ENABLE_ICECAST}" == "1" ]]; then
      rq_opt "Installing Icecast2" \
        "echo 'icecast2 icecast2/icecast-setup boolean false' | ${SUDO} debconf-set-selections 2>/dev/null || true; \
         DEBIAN_FRONTEND=noninteractive ${SUDO} apt-get install -y icecast2" \
        "Icecast2 install failed — install later with: sudo apt install icecast2"
    fi
  fi

  # ── Icecast2 (update / reinstall path) ───────────────────────────────────
  if [[ "${IS_UPDATE}" -eq 1 && "${ENABLE_ICECAST}" == "1" ]]; then
    rq_opt "Installing Icecast2" \
      "echo 'icecast2 icecast2/icecast-setup boolean false' | ${SUDO} debconf-set-selections 2>/dev/null || true; \
       DEBIAN_FRONTEND=noninteractive ${SUDO} apt-get install -y icecast2" \
      "Icecast2 install failed — install later with: sudo apt install icecast2"
  fi

  # ── Runtime packages (fresh + updates) ───────────────────────────────────
  if command -v apt-get &>/dev/null; then
    rq_opt "Ensuring libportaudio2 is installed" \
      "DEBIAN_FRONTEND=noninteractive ${SUDO} apt-get install -y libportaudio2" \
      "Could not install libportaudio2 (sound device input may not work)"
  fi

  if [[ "${ENABLE_SDR}" == "1" ]]; then
    ensure_rtlsdr_blacklist
    ensure_usbfs_unlimited
  fi

  # ── Directories ───────────────────────────────────────────────────────────
  ${SUDO} mkdir -p "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"
  ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" \
    "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"
  ok "Directories ready"

  if [[ "${LEGACY_INSTALL_DETECTED}" -eq 1 ]]; then
    migrate_legacy_livewire_install
  fi

  VENV_DIR="${INSTALL_ROOT}/venv"
  TARGET_APP="${INSTALL_ROOT}/${APP_PY_NAME}"

  # ── Application file ──────────────────────────────────────────────────────
  if [[ "${WINNING_SOURCE}" == "installed" && "${FORCE_OVERWRITE}" -ne 1 ]]; then
    ok "App file is current (${WINNING_VER})"
  else
    if [[ -f "${TARGET_APP}" ]]; then
      APP_BACKUP="${TARGET_APP%.py}.backup-${INSTALLED_VER:-prev}.py"
      ${SUDO} cp -a "${TARGET_APP}" "${APP_BACKUP}"
      ok "Backed up ${INSTALLED_VER:-previous} → $(basename "${APP_BACKUP}")"
    fi
    install -m 0644 "${SOURCE_APP}" "/tmp/${APP_PY_NAME}.$$"
    ${SUDO} mv "/tmp/${APP_PY_NAME}.$$" "${TARGET_APP}"
    ${SUDO} chown "${SERVICE_USER}:${SERVICE_GROUP}" "${TARGET_APP}"
    ok "Application file installed (${WINNING_VER})"
  fi

  # ── Static assets ─────────────────────────────────────────────────────────
  if [[ -d "${SOURCE_DIR}/static" ]]; then
    if [[ "${WINNING_SOURCE}" != "installed" || ! -f "${INSTALL_ROOT}/static/signalscope_icon.png" ]]; then
      ${SUDO} mkdir -p "${INSTALL_ROOT}/static"
      ${SUDO} rsync -a "${SOURCE_DIR}/static/" "${INSTALL_ROOT}/static/"
      ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/static"
      ok "Static assets updated"
    fi
  elif [[ ! -f "${INSTALL_ROOT}/static/signalscope_icon.png" ]]; then
    ${SUDO} mkdir -p "${INSTALL_ROOT}/static"
    for asset in signalscope_icon.ico signalscope_icon.png signalscope_logo.jpg signalscope_logo.png; do
      ${SUDO} curl -fsSL --max-time 10 "${RAW_BASE_URL}/static/${asset}" \
        -o "${INSTALL_ROOT}/static/${asset}" 2>/dev/null || true
    done
    ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/static"
    ok "Static assets downloaded"
  fi

  # ── plugins/ directory ────────────────────────────────────────────────────
  # Always ensure the directory exists. Copy bundled plugins from source only
  # on a fresh install or local/git source — on updates leave user-installed
  # plugins in place (users manage plugins via Settings → Plugins).
  ${SUDO} mkdir -p "${INSTALL_ROOT}/plugins"
  ${SUDO} chown "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/plugins"
  if [[ -d "${SOURCE_DIR}/plugins" && "${WINNING_SOURCE}" != "installed" && "${IS_UPDATE}" -ne 1 ]]; then
    ${SUDO} rsync -a "${SOURCE_DIR}/plugins/" "${INSTALL_ROOT}/plugins/"
    ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/plugins"
    ok "Plugins directory installed"
  fi

  # ── Python version check ──────────────────────────────────────────────────
  PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  PY_MAJ="$(python3 -c 'import sys; print(sys.version_info.major)')"
  PY_MIN="$(python3 -c 'import sys; print(sys.version_info.minor)')"
  if [[ "${PY_MAJ}" -lt 3 || ( "${PY_MAJ}" -eq 3 && "${PY_MIN}" -lt 9 ) ]]; then
    warn "Python ${PY_VER} — 3.9+ required for AI/ONNX features"
  else
    ok "Python ${PY_VER}"
  fi

  # ── Python virtual environment ────────────────────────────────────────────
  rq "Creating Python environment" "python3 -m venv '${VENV_DIR}'"

  # ── Core Python packages ──────────────────────────────────────────────────
  rq "Installing Python packages  (this may take a few minutes)" \
    "source '${VENV_DIR}/bin/activate' && \
     python -m pip install --quiet --upgrade pip wheel 'setuptools<81' && \
     python -m pip install --quiet flask waitress cheroot numpy scipy requests certifi \
       cryptography psutil sounddevice 'httpx[http2]' pyotp 'qrcode[pil]'"

  # ── Optional packages ─────────────────────────────────────────────────────
  rq_opt "Installing MP3 encoder  (lameenc)" \
    "source '${VENV_DIR}/bin/activate' && python -m pip install --quiet lameenc" \
    "lameenc not available — MP3 clip encoding will fall back to ffmpeg or WAV"

  rq_opt "Installing SNMP library  (pysnmp)" \
    "source '${VENV_DIR}/bin/activate' && python -m pip install --quiet pysnmp" \
    "pysnmp not available — Codec Monitor will use HTTP/TCP fallback for SNMP devices"

  rq_opt "Installing Zetta SOAP client  (zeep)" \
    "source '${VENV_DIR}/bin/activate' && python -m pip install --quiet zeep" \
    "zeep not available — Zetta plugin station discovery will be unavailable"

  rq_opt "Installing WebRTC stack  (aiortc — may compile on Pi)" \
    "source '${VENV_DIR}/bin/activate' && python -m pip install --quiet aiortc av" \
    "aiortc not available — IP Link will use browser-managed WebRTC"

  if [[ "${ENABLE_NDI}" == "1" ]]; then
    # ndi-python has no pre-built Linux wheels on PyPI — it requires the NDI SDK
    # to be installed separately and then a from-source build.  On Linux we check
    # whether the NDI SDK runtime is already present; if not, we skip and explain.
    if [[ "$(uname -s)" == "Linux" ]]; then
      # Check whether ndi-python is already importable (user installed manually)
      if source "${VENV_DIR}/bin/activate" && python -c "import ndi" &>/dev/null; then
        _pf "NDI Python" "already installed" ""
      else
        # NDI SDK not found — skip with guidance
        printf "  ${YELLOW}⚠${NC}  NDI Python — skipped on Linux (NDI SDK not installed)\n"
        printf "       To enable: install NDI SDK from https://ndi.tv/sdk/\n"
        printf "       then: CMAKE_ARGS=\"-DNDI_SDK_DIR=/usr/local/NDISDK\" pip install ndi-python\n"
        printf "       vMix Caller will use SRT bridge mode in the meantime.\n"
      fi
    else
      # macOS / other: attempt pip install (wheels may be available)
      rq_opt "Installing NDI Python bindings  (ndi-python)" \
        "source '${VENV_DIR}/bin/activate' && python -m pip install --quiet ndi-python" \
        "ndi-python not available — vMix Caller will use SRT bridge mode"
    fi
  fi

  rq_opt "Installing ONNX AI runtime" \
    "source '${VENV_DIR}/bin/activate' && \
     python -m pip install --quiet onnx && python -m pip install --quiet onnxruntime" \
    "onnxruntime not available — AI Monitor features require manual install"

  if [[ "${ENABLE_SDR}" == "1" ]]; then
    rq "Installing RTL-SDR Python bindings  (pyrtlsdr)" \
      "source '${VENV_DIR}/bin/activate' && python -m pip install --quiet --upgrade 'pyrtlsdr==0.2.93'"
    rq_opt "Building redsea  (RDS decoder — may take a minute)" \
      "source '${VENV_DIR}/bin/activate' && install_redsea" \
      "redsea build failed — FM RDS decoding will be unavailable"
  fi

  # ── Kernel network tuning — fresh install or --retune ────────────────────
  if [[ "${IS_UPDATE}" -ne 1 || "${FORCE_RETUNE}" -eq 1 ]]; then
    if [[ "${ENABLE_LIVEWIRE}" == "1" ]]; then
      _rmem_max=2147483647; _rmem_def=1610612736
      _wmem_max=2147483647; _wmem_def=1610612736
      _udp_rmin=3145728;    _udp_wmin=3145728
      _backlog=2250000;     _optmem=196608
    else
      _rmem_max=536870912;  _rmem_def=536870912
      _wmem_max=536870912;  _wmem_def=536870912
      _udp_rmin=1048576;    _udp_wmin=1048576
      _backlog=750000;      _optmem=65536
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
    ${SUDO} sysctl -w net.ipv4.udp_rmem_min="${_udp_rmin}" 2>/dev/null \
      && echo "net.ipv4.udp_rmem_min=${_udp_rmin}" | ${SUDO} tee -a /etc/sysctl.d/99-signalscope-network.conf >/dev/null \
      || true
    ${SUDO} sysctl -w net.ipv4.udp_wmem_min="${_udp_wmin}" 2>/dev/null \
      && echo "net.ipv4.udp_wmem_min=${_udp_wmin}" | ${SUDO} tee -a /etc/sysctl.d/99-signalscope-network.conf >/dev/null \
      || true
    if [[ "${ENABLE_LIVEWIRE}" == "1" ]]; then
      ${SUDO} sysctl -w net.ipv4.igmp_max_memberships=512 2>/dev/null \
        && echo "net.ipv4.igmp_max_memberships=512" | ${SUDO} tee -a /etc/sysctl.d/99-signalscope-network.conf >/dev/null \
        || true
    fi
    ${SUDO} sysctl --system >/dev/null || true
    ok "Network tuning applied$([[ "${ENABLE_LIVEWIRE}" == "1" ]] && echo " (Livewire/AES67 profile)" || echo "")"

    DEFAULT_IFACE="$(ip route 2>/dev/null | awk '/default/ {print $5; exit}')"
    if [[ -n "${DEFAULT_IFACE:-}" ]]; then
      ${SUDO} ethtool -K "${DEFAULT_IFACE}" gro off gso off tso off lro off >/dev/null 2>&1 || true
      ${SUDO} ethtool -G "${DEFAULT_IFACE}" rx 4096 tx 4096 >/dev/null 2>&1 || true
    fi

    PYBIN="$(readlink -f "$(command -v python3)")"
    [[ -f "${PYBIN}" ]] && ${SUDO} setcap cap_net_bind_service=+ep "${PYBIN}" || true

    getent group plugdev >/dev/null 2>&1 && ${SUDO} usermod -aG plugdev "${SERVICE_USER}" || true
  fi

  # ── Environment file (always written — needed by service unit) ────────────
  if [[ ! -f "/etc/default/${SERVICE_NAME}" || "${ENABLE_SERVICE}" == "1" ]]; then
    ${SUDO} tee /etc/default/${SERVICE_NAME} > /dev/null <<EOF
SIGNALSCOPE_INSTALL_DIR=${INSTALL_ROOT}
SIGNALSCOPE_DATA_DIR=${DATA_ROOT_DEFAULT}
SIGNALSCOPE_LOG_DIR=${LOG_ROOT_DEFAULT}
EOF
  fi

  # ── Service install / reinstall ───────────────────────────────────────────
  # create_service runs on: fresh install with ENABLE_SERVICE=1, OR any run
  # (fresh or update) where the user explicitly asked for ENABLE_SERVICE=1.
  if [[ "${ENABLE_SERVICE}" == "1" ]]; then
    create_service
  fi

  # ── Patch existing service file (usbfs fix on updates) ───────────────────
  local _svc_file="/etc/systemd/system/${SERVICE_NAME}.service"
  if [[ -f "${_svc_file}" ]] && ! grep -q "usbfs_memory_mb" "${_svc_file}" 2>/dev/null; then
    ${SUDO} sed -i \
      '/^ExecStart=/i ExecStartPre=+\/bin\/sh -c '"'"'echo 0 > \/sys\/module\/usbcore\/parameters\/usbfs_memory_mb 2>\/dev\/null || true'"'" \
      "${_svc_file}"
    ${SUDO} systemctl daemon-reload
    ok "Service file patched with USB buffer fix"
  fi

  # ── PTP clock monitor ─────────────────────────────────────────────────────
  rq "Configuring PTP clock monitor  (linuxptp)" "setup_ptp"

  # ── nginx ─────────────────────────────────────────────────────────────────
  if [[ "${ENABLE_NGINX}" == "1" ]]; then
    rq "Configuring nginx${NGINX_FQDN:+ for ${NGINX_FQDN}}" \
      "DEBIAN_FRONTEND=noninteractive ${SUDO} apt-get install -y nginx >/dev/null 2>&1; \
       configure_nginx '${NGINX_FQDN:-}' '${NGINX_HTTPS:-0}'"
  fi

  # ── Patch nginx client_max_body_size on existing configs ─────────────────
  _ng_conf="/etc/nginx/sites-available/signalscope"
  if [[ -f "${_ng_conf}" ]] && ! grep -q "client_max_body_size" "${_ng_conf}" 2>/dev/null; then
    ${SUDO} sed -i '/^[[:space:]]*server[[:space:]]*{/a\    client_max_body_size 20m;' "${_ng_conf}"
    if ${SUDO} nginx -t >/dev/null 2>&1; then
      ${SUDO} nginx -s reload 2>/dev/null || ${SUDO} systemctl reload nginx 2>/dev/null || true
      ok "nginx: added client_max_body_size 20m"
    else
      ${SUDO} sed -i '/client_max_body_size 20m;/d' "${_ng_conf}"
    fi
  fi

  # ── Log rotation ──────────────────────────────────────────────────────────
  write_logrotate
  ok "Log rotation configured (daily, 14-day retention)"

  # ── Watchdog — always written/updated when service is present ────────────
  local _svc_enabled=0
  systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null && _svc_enabled=1 || true
  if [[ "${_svc_enabled}" -eq 1 ]]; then
    write_watchdog
    ${SUDO} chmod +x /usr/local/bin/${SERVICE_NAME}-watchdog.sh
    ${SUDO} systemctl daemon-reload
    ${SUDO} systemctl enable --now "${SERVICE_NAME}-watchdog.timer" 2>/dev/null || true
    ok "Watchdog enabled"
  fi

  # ── Restart / start service ───────────────────────────────────────────────
  # create_service already did enable --now when ENABLE_SERVICE=1.
  # For updates where we did NOT call create_service, restart if running.
  if [[ "${ENABLE_SERVICE}" != "1" && "${_svc_enabled}" -eq 1 ]]; then
    if [[ "${WINNING_SOURCE}" != "installed" ]]; then
      # New code was deployed — restart to pick it up
      if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        rq "Restarting service to apply update" "${SUDO} systemctl restart '${SERVICE_NAME}'"
      else
        rq "Starting service" "${SUDO} systemctl start '${SERVICE_NAME}'"
      fi
    elif ! systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
      # Service enabled but not running (e.g. after a crash) — start it
      rq "Starting service" "${SUDO} systemctl start '${SERVICE_NAME}'"
    fi
  fi

  # ── Health check ──────────────────────────────────────────────────────────
  if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    verify_app_health || true
  fi

  # ── Done! ─────────────────────────────────────────────────────────────────
  print_done
}

main "$@"
