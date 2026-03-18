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
ENABLE_NGINX=""
NGINX_FQDN=""
NGINX_HTTPS=""
FORCE_OVERWRITE=0
INSTALL_ROOT="${INSTALL_ROOT_DEFAULT}"

SOURCE_DIR=""
SOURCE_APP=""
TEMP_SOURCE_DIR=""
SELF_COPY=""
SCRIPT_PATH="${BASH_SOURCE[0]:-}"

# Version comparison state (populated by resolve_best_source)
INSTALLED_VER=""
SOURCE_VER=""
REMOTE_VER=""
WINNING_VER=""
WINNING_SOURCE=""   # "installed" | "local" | "remote"
IS_UPDATE=0         # 1 when an existing install is detected

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
${APP_NAME} installer / updater

The script auto-detects whether this is a fresh install or an update:
  - Compares installed version, local file version, and latest GitHub version.
  - The highest version wins and is installed.
  - On update: only the app file, static assets, and new Python deps are updated.
  - On fresh install: full system dep install, venv creation, service setup, nginx.

Interactive:
  /bin/bash <(curl -fsSL https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh)

Non-interactive:
  curl -fsSL https://raw.githubusercontent.com/itconor/SignalScope/main/install_signalscope.sh | bash -s -- --service --sdr

Options:
  --service                 Install and enable systemd service
  --no-service              Skip systemd service installation
  --sdr                     Install RTL-SDR tooling, pyrtlsdr, and redsea build deps
  --no-sdr                  Skip SDR support
  --nginx                   Install and configure nginx reverse proxy
  --no-nginx                Skip nginx setup
  --fqdn <hostname>         Fully-qualified domain name for nginx vhost and TLS cert
  --https                   Request a Let's Encrypt certificate via certbot (requires --fqdn)
  --no-https                Skip Let's Encrypt even if --fqdn is set
  --install-dir <path>      Install application under this path (default: ${INSTALL_ROOT_DEFAULT})
  --force                   Force overwrite even if installed version is current
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
    case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
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

# Extract "X.Y.Z" from the BUILD line in a signalscope.py file.
# Prints the version string or nothing if not found.
extract_build_version() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  grep -oP '(?<=SignalScope-)\d+\.\d+\.\d+' "$file" 2>/dev/null | head -1 || true
}

# Returns 0 (true) if version $1 > version $2 (both "X.Y.Z").
# Empty string is treated as 0.0.0.
version_gt() {
  python3 - <<PYEOF
a = tuple(int(x) for x in ('${1:-0.0.0}'.split('.')))
b = tuple(int(x) for x in ('${2:-0.0.0}'.split('.')))
import sys; sys.exit(0 if a > b else 1)
PYEOF
}

# Determine the best source file by comparing installed, local CWD, and remote
# GitHub versions.  Sets SOURCE_DIR, SOURCE_APP, INSTALLED_VER, SOURCE_VER,
# REMOTE_VER, WINNING_VER, WINNING_SOURCE, and IS_UPDATE.
resolve_best_source() {
  local cwd installed_app local_app remote_app fetch_ok

  cwd="$(pwd)"
  installed_app="${INSTALL_ROOT}/${APP_PY_NAME}"
  local_app="${cwd}/${APP_PY_NAME}"

  # ── 1. Installed version ─────────────────────────────────────────────────
  INSTALLED_VER=$(extract_build_version "${installed_app}")
  if [[ -n "${INSTALLED_VER}" ]]; then
    info "Installed version : ${INSTALLED_VER}  (${installed_app})"
    IS_UPDATE=1
  else
    info "No existing installation found at ${installed_app}"
  fi

  # ── 2. Local source version (current directory) ──────────────────────────
  SOURCE_VER=$(extract_build_version "${local_app}")
  [[ -n "${SOURCE_VER}" ]] && info "Local file version: ${SOURCE_VER}  (${local_app})"

  # ── 3. Remote version from GitHub ────────────────────────────────────────
  step "Checking remote version on GitHub"
  TEMP_SOURCE_DIR="$(mktemp -d /tmp/signalscope-src.XXXXXX)"
  remote_app="${TEMP_SOURCE_DIR}/${APP_PY_NAME}"
  fetch_ok=0

  if command -v git >/dev/null 2>&1; then
    if git clone --depth 1 "${REPO_URL}" "${TEMP_SOURCE_DIR}" >/dev/null 2>&1; then
      fetch_ok=1
    else
      warn "git clone failed, falling back to direct download"
    fi
  fi

  if [[ "${fetch_ok}" -eq 0 ]]; then
    if curl -fsSL --max-time 30 "${RAW_BASE_URL}/${APP_PY_NAME}" -o "${remote_app}" 2>/dev/null; then
      fetch_ok=1
    else
      warn "Could not fetch remote file from GitHub"
    fi
  fi

  REMOTE_VER=""
  if [[ "${fetch_ok}" -eq 1 && -f "${remote_app}" ]]; then
    REMOTE_VER=$(extract_build_version "${remote_app}")
    [[ -n "${REMOTE_VER}" ]] && info "Remote version     : ${REMOTE_VER}  (GitHub)" \
                              || warn "Could not parse version from remote file"
  fi

  # ── 4. Pick the winner (highest version) ─────────────────────────────────
  WINNING_VER="${INSTALLED_VER:-0.0.0}"
  WINNING_SOURCE="installed"

  if [[ -n "${SOURCE_VER}" ]] && version_gt "${SOURCE_VER}" "${WINNING_VER}"; then
    WINNING_VER="${SOURCE_VER}"
    WINNING_SOURCE="local"
  fi

  if [[ -n "${REMOTE_VER}" ]] && version_gt "${REMOTE_VER}" "${WINNING_VER}"; then
    WINNING_VER="${REMOTE_VER}"
    WINNING_SOURCE="remote"
  fi

  # ── 5. Set SOURCE_DIR / SOURCE_APP ───────────────────────────────────────
  case "${WINNING_SOURCE}" in
    local)
      SOURCE_DIR="${cwd}"
      SOURCE_APP="${local_app}"
      if [[ -n "${INSTALLED_VER}" ]]; then
        ok "Update available: ${INSTALLED_VER} → ${WINNING_VER}  (local file wins)"
      else
        ok "Source: local file ${WINNING_VER}"
      fi
      ;;
    remote)
      SOURCE_DIR="${TEMP_SOURCE_DIR}"
      SOURCE_APP="${remote_app}"
      if [[ -n "${INSTALLED_VER}" ]]; then
        ok "Update available: ${INSTALLED_VER} → ${WINNING_VER}  (GitHub wins)"
      else
        ok "Source: GitHub ${WINNING_VER}"
      fi
      ;;
    installed)
      SOURCE_DIR="${INSTALL_ROOT}"
      SOURCE_APP="${installed_app}"
      ok "Already up to date: ${WINNING_VER}"
      ;;
  esac

  # Legacy filename fallback (installed or local)
  if [[ ! -f "${SOURCE_APP}" ]]; then
    local legacy_cwd="${cwd}/${LEGACY_APP_PY}"
    local legacy_inst="${INSTALL_ROOT}/${LEGACY_APP_PY}"
    if [[ -f "${legacy_cwd}" ]]; then
      SOURCE_DIR="${cwd}"
      SOURCE_APP="${legacy_cwd}"
      warn "Using legacy app file: ${SOURCE_APP}"
    elif [[ -f "${legacy_inst}" ]]; then
      SOURCE_DIR="${INSTALL_ROOT}"
      SOURCE_APP="${legacy_inst}"
      warn "Using legacy installed app file: ${SOURCE_APP}"
    else
      err "No application file found — cannot continue"
      exit 1
    fi
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

init_sudo() {
  if [[ $EUID -ne 0 ]]; then
    need_cmd sudo
    info "Requesting sudo access..."
    sudo -v
    SUDO="sudo"
  else
    SUDO=""
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


ensure_rtlsdr_blacklist() {
  if [[ "${ENABLE_SDR}" != "1" ]]; then
    return 0
  fi

  step "Configuring RTL-SDR blacklist"
  ${SUDO} mkdir -p /etc/modprobe.d
  echo 'blacklist dvb_usb_rtl28xxu' | ${SUDO} tee /etc/modprobe.d/rtlsdr.conf > /dev/null
  ${SUDO} modprobe -r dvb_usb_rtl28xxu >/dev/null 2>&1 || true

  if command -v update-initramfs >/dev/null 2>&1; then
    ${SUDO} update-initramfs -u >/dev/null 2>&1 || warn "update-initramfs failed; blacklist file was still written"
  else
    warn "update-initramfs not found; blacklist file was written but initramfs was not rebuilt"
  fi

  ok "RTL-SDR blacklist applied (replug any connected dongles)"
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
ExecStart=${VENV_DIR}/bin/python ${TARGET_APP}
Restart=always
RestartSec=5
CPUSchedulingPolicy=rr
CPUSchedulingPriority=20
Nice=-10
AmbientCapabilities=CAP_SYS_NICE
LimitRTPRIO=95
LimitNICE=-20

[Install]
WantedBy=multi-user.target
EOF

  ${SUDO} tee /usr/local/bin/${SERVICE_NAME}-watchdog.sh > /dev/null <<'EOF'
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

configure_nginx() {
  local fqdn="$1"
  local use_https="$2"
  local conf_dir="/etc/nginx/sites-available"
  local enabled_dir="/etc/nginx/sites-enabled"
  local conf_file="${conf_dir}/signalscope"

  step "Installing nginx"
  ${SUDO} apt-get install -y nginx

  step "Writing nginx vhost configuration"
  ${SUDO} tee "${conf_file}" > /dev/null <<EOF
# SignalScope nginx reverse proxy
# Generated by install_signalscope.sh

server {
    listen 80;
    server_name ${fqdn};

    # Needed for certbot ACME challenge
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${fqdn};

    # Certificates — filled in by certbot or manually
    ssl_certificate     /etc/letsencrypt/live/${fqdn}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${fqdn}/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

    # Forward real client IP and protocol to the app
    proxy_set_header Host              \$host;
    proxy_set_header X-Real-IP         \$remote_addr;
    proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;

    # Live audio streaming endpoint — disable buffering so audio flows immediately
    location /stream/ {
        proxy_pass             http://127.0.0.1:5000;
        proxy_buffering        off;
        proxy_cache            off;
        proxy_read_timeout     3600s;
        proxy_send_timeout     3600s;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # All other requests
    location / {
        proxy_pass             http://127.0.0.1:5000;
        proxy_read_timeout     120s;
        proxy_send_timeout     120s;
        proxy_buffering        on;
        proxy_buffer_size      16k;
        proxy_buffers          8 16k;
    }
}
EOF

  # Enable the site
  ${SUDO} ln -sf "${conf_file}" "${enabled_dir}/signalscope"
  ${SUDO} rm -f "${enabled_dir}/default" || true

  # Test config before reloading
  if ! ${SUDO} nginx -t 2>/dev/null; then
    # If cert doesn't exist yet the SSL server block will fail — write an HTTP-only
    # placeholder so nginx starts, then certbot will patch it later.
    warn "nginx config test failed (SSL cert likely not present yet) — writing HTTP-only placeholder"
    ${SUDO} tee "${conf_file}" > /dev/null <<EOF
# SignalScope nginx reverse proxy (HTTP-only, pre-TLS)
server {
    listen 80;
    server_name ${fqdn};

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location /stream/ {
        proxy_pass             http://127.0.0.1:5000;
        proxy_buffering        off;
        proxy_cache            off;
        proxy_read_timeout     3600s;
        proxy_send_timeout     3600s;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        proxy_pass             http://127.0.0.1:5000;
        proxy_read_timeout     120s;
        proxy_send_timeout     120s;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  fi

  ${SUDO} systemctl enable --now nginx
  ${SUDO} nginx -s reload 2>/dev/null || ${SUDO} systemctl restart nginx
  ok "nginx configured for ${fqdn}"

  if [[ "${use_https}" == "1" ]]; then
    step "Installing certbot"
    ${SUDO} apt-get install -y certbot python3-certbot-nginx

    step "Requesting Let's Encrypt certificate for ${fqdn}"
    info "Port 80 must be reachable from the internet for the ACME HTTP-01 challenge."
    if ${SUDO} certbot --nginx -d "${fqdn}" --non-interactive --agree-tos \
         --register-unsafely-without-email --redirect 2>&1; then
      ok "TLS certificate issued for ${fqdn}"
      ${SUDO} nginx -s reload
    else
      warn "certbot failed. You can retry manually:"
      warn "  sudo certbot --nginx -d ${fqdn}"
    fi

    # Set up auto-renewal timer
    ${SUDO} systemctl enable --now certbot.timer 2>/dev/null || \
      ${SUDO} systemctl enable --now snap.certbot.renew.timer 2>/dev/null || true
    ok "certbot auto-renewal enabled"
  fi
}

main() {
  print_banner
  parse_args "$@"

  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    case "${ID:-}${ID_LIKE:-}" in
      *debian*|*ubuntu*) ;;
      *) err "This installer requires a Debian/Ubuntu-based system (detected: ${PRETTY_NAME:-unknown})."; exit 1 ;;
    esac
  else
    warn "Cannot detect OS — this installer is designed for Debian/Ubuntu systems."
  fi

  need_cmd bash
  need_cmd python3
  need_cmd curl

  if [[ $EUID -eq 0 && -z "${SUDO_USER:-}" ]]; then
    warn "Running directly as root."
  fi

  init_sudo

  INSTALL_ROOT="$(ask_value "Install directory" "${INSTALL_ROOT}")"

  # ── Version check: compare installed / local / remote and pick the winner ──
  echo
  resolve_best_source

  # ── Decide mode based on what we found ──────────────────────────────────────
  if [[ "${IS_UPDATE}" -eq 1 && "${WINNING_SOURCE}" == "installed" && "${FORCE_OVERWRITE}" -ne 1 ]]; then
    echo
    ok "${APP_NAME} ${WINNING_VER} is already the latest version."
    if [[ "${INTERACTIVE}" -eq 1 ]]; then
      if ! ask_yes_no "Reinstall/repair dependencies anyway?" "n"; then
        warn "Nothing to do."
        exit 0
      fi
    else
      # Non-interactive with no --force: nothing to do
      exit 0
    fi
  fi

  # ── Interactive prompts — tailor to update vs fresh install ─────────────────
  if [[ "${IS_UPDATE}" -eq 1 ]]; then
    info "Update mode: ${INSTALLED_VER} → ${WINNING_VER}"
    # In update mode default service/SDR/nginx to "skip" (already configured)
    if [[ -z "${ENABLE_SERVICE}" ]]; then ENABLE_SERVICE=0; fi
    if [[ -z "${ENABLE_SDR}" ]];     then ENABLE_SDR=0;     fi
    if [[ -z "${ENABLE_NGINX}" ]];   then ENABLE_NGINX=0;   fi
  else
    info "Fresh install mode"

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

    if [[ -z "${ENABLE_NGINX}" ]]; then
      if ask_yes_no "Install nginx as a reverse proxy (recommended for production)?" "y"; then
        ENABLE_NGINX=1
      else
        ENABLE_NGINX=0
      fi
    fi

    if [[ "${ENABLE_NGINX}" == "1" ]]; then
      if [[ -z "${NGINX_FQDN}" ]]; then
        NGINX_FQDN="$(ask_value "Enter FQDN for nginx vhost (e.g. signalscope.example.com), or leave blank for HTTP-only" "")"
      fi
      if [[ -n "${NGINX_FQDN}" && -z "${NGINX_HTTPS}" ]]; then
        if ask_yes_no "Request a Let's Encrypt TLS certificate for ${NGINX_FQDN}?" "y"; then
          NGINX_HTTPS=1
        else
          NGINX_HTTPS=0
        fi
      fi
    fi
  fi

  echo
  info "OS: $(. /etc/os-release && echo "${PRETTY_NAME:-Linux}")"
  info "Arch: $(dpkg --print-architecture 2>/dev/null || uname -m)"
  info "Install dir: ${INSTALL_ROOT}"
  info "Mode: $([[ "${IS_UPDATE}" -eq 1 ]] && echo "update ${INSTALLED_VER} → ${WINNING_VER}" || echo "fresh install ${WINNING_VER}")"
  if [[ "${IS_UPDATE}" -ne 1 ]]; then
    info "Install service: $([[ "${ENABLE_SERVICE}" == "1" ]] && echo yes || echo no)"
    info "Install SDR: $([[ "${ENABLE_SDR}" == "1" ]] && echo yes || echo no)"
    info "Install nginx: $([[ "${ENABLE_NGINX}" == "1" ]] && echo yes || echo no)"
    if [[ "${ENABLE_NGINX}" == "1" && -n "${NGINX_FQDN}" ]]; then
      info "nginx FQDN: ${NGINX_FQDN}"
      info "Let's Encrypt TLS: $([[ "${NGINX_HTTPS}" == "1" ]] && echo yes || echo no)"
    fi
  fi
  if [[ "${INTERACTIVE}" == "1" ]]; then
    echo
    local _confirm_prompt="Continue with $([[ "${IS_UPDATE}" -eq 1 ]] && echo update || echo installation)?"
    if ! ask_yes_no "${_confirm_prompt}" "y"; then
      warn "Cancelled."
      exit 0
    fi
  fi

  SERVICE_USER="${SUDO_USER:-$USER}"
  SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  IS_ARM=0
  case "$ARCH" in
    armhf|arm64|aarch64) IS_ARM=1 ;;
  esac

  # ── System packages — only on fresh install (already present on update) ─────
  if [[ "${IS_UPDATE}" -ne 1 ]]; then
    step "Installing apt packages"
    export DEBIAN_FRONTEND=noninteractive
    ${SUDO} apt-get update -y
    ${SUDO} apt-get install -y \
      python3 python3-venv python3-dev python3-setuptools \
      build-essential pkg-config git curl wget ca-certificates rsync \
      ffmpeg ethtool net-tools iproute2 jq \
      libffi-dev libssl-dev

    if [[ "${ENABLE_SDR}" == "1" ]]; then
      step "Installing SDR apt packages"
      ${SUDO} apt-get install -y \
        rtl-sdr librtlsdr-dev welle.io \
        libsndfile1 libsndfile1-dev libliquid-dev libfftw3-dev nlohmann-json3-dev \
        meson ninja-build || true
      ensure_rtlsdr_blacklist
    fi
  fi

  # ── Directories ──────────────────────────────────────────────────────────────
  step "Preparing directories"
  ${SUDO} mkdir -p "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"
  ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}" "${DATA_ROOT_DEFAULT}" "${LOG_ROOT_DEFAULT}"

  VENV_DIR="${INSTALL_ROOT}/venv"
  TARGET_APP="${INSTALL_ROOT}/${APP_PY_NAME}"

  # ── Application file ─────────────────────────────────────────────────────────
  step "Installing application files"
  if [[ "${WINNING_SOURCE}" == "installed" && "${FORCE_OVERWRITE}" -ne 1 ]]; then
    info "App file is current (${WINNING_VER}), skipping copy."
  else
    need_cmd install
    install -m 0644 "${SOURCE_APP}" "/tmp/${APP_PY_NAME}.$$"
    ${SUDO} mv "/tmp/${APP_PY_NAME}.$$" "${TARGET_APP}"
    ${SUDO} chown "${SERVICE_USER}:${SERVICE_GROUP}" "${TARGET_APP}"
    ok "Installed ${TARGET_APP} (${WINNING_VER})"
  fi

  # ── Static assets ────────────────────────────────────────────────────────────
  if [[ -d "${SOURCE_DIR}/static" && "${WINNING_SOURCE}" != "installed" ]]; then
    ${SUDO} mkdir -p "${INSTALL_ROOT}/static"
    ${SUDO} rsync -a --delete "${SOURCE_DIR}/static/" "${INSTALL_ROOT}/static/"
    ${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}/static"
    ok "Updated static assets"
  elif [[ "${WINNING_SOURCE}" != "installed" ]]; then
    warn "No static/ directory found in source tree; skipping static asset copy."
  fi

  # ── requirements.txt ────────────────────────────────────────────────────────
  # Copy requirements.txt from source if present (and source is newer)
  if [[ "${WINNING_SOURCE}" != "installed" && -f "${SOURCE_DIR}/requirements.txt" ]]; then
    ${SUDO} install -m 0644 "${SOURCE_DIR}/requirements.txt" "${INSTALL_ROOT}/requirements.txt"
    ok "Updated requirements.txt"
  fi

  # ── Python version check ─────────────────────────────────────────────────────
  step "Checking Python version"
  PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  PY_MAJ="$(python3 -c 'import sys; print(sys.version_info.major)')"
  PY_MIN="$(python3 -c 'import sys; print(sys.version_info.minor)')"
  if [[ "${PY_MAJ}" -lt 3 || ( "${PY_MAJ}" -eq 3 && "${PY_MIN}" -lt 9 ) ]]; then
    warn "Python ${PY_VER} detected — Python 3.9+ is required. Proceeding, but onnxruntime may not install correctly."
  else
    ok "Python ${PY_VER}"
  fi

  # ── Virtual environment ───────────────────────────────────────────────────────
  step "Creating/updating Python virtual environment"
  python3 -m venv "${VENV_DIR}"
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"

  # ── Python dependencies ───────────────────────────────────────────────────────
  # On update: install from requirements.txt to pick up any new deps only.
  # pip is idempotent — already-satisfied deps are skipped quickly.
  step "Installing/updating Python packages"
  python -m pip install --upgrade pip wheel "setuptools<81"

  if [[ -f "${INSTALL_ROOT}/requirements.txt" ]]; then
    python -m pip install -r "${INSTALL_ROOT}/requirements.txt"
  else
    python -m pip install \
      flask waitress cheroot numpy scipy requests certifi cryptography
  fi

  step "Installing/checking ONNX stack"
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

  # ── System tuning — only on fresh install ────────────────────────────────────
  if [[ "${IS_UPDATE}" -ne 1 ]]; then
    step "Applying network tuning"
    ${SUDO} tee /etc/sysctl.d/99-signalscope-network.conf > /dev/null <<'EOF'
net.core.rmem_max=536870912
net.core.rmem_default=536870912
net.core.wmem_max=536870912
net.core.wmem_default=536870912
net.ipv4.udp_rmem_min=1048576
net.ipv4.udp_wmem_min=1048576
net.core.netdev_max_backlog=750000
net.core.optmem_max=65536
EOF
    ${SUDO} sysctl --system >/dev/null || true

    DEFAULT_IFACE="$(ip route 2>/dev/null | awk '/default/ {print $5; exit}')"
    if [[ -n "${DEFAULT_IFACE:-}" ]]; then
      info "Applying best-effort NIC tuning to ${DEFAULT_IFACE}"
      ${SUDO} ethtool -K "${DEFAULT_IFACE}" gro off gso off tso off lro off >/dev/null 2>&1 || true
      ${SUDO} ethtool -G "${DEFAULT_IFACE}" rx 4096 tx 4096 >/dev/null 2>&1 || true
    fi

    step "Setting Python low-port capability"
    PYBIN="$(readlink -f "$(command -v python3)")"
    if [[ -f "${PYBIN}" ]]; then
      ${SUDO} setcap cap_net_bind_service=+ep "${PYBIN}" || true
    fi

    if getent group plugdev >/dev/null 2>&1; then
      ${SUDO} usermod -aG plugdev "${SERVICE_USER}" || true
    fi

    step "Writing environment file"
    ${SUDO} tee /etc/default/${SERVICE_NAME} > /dev/null <<EOF
SIGNALSCOPE_INSTALL_DIR=${INSTALL_ROOT}
SIGNALSCOPE_DATA_DIR=${DATA_ROOT_DEFAULT}
SIGNALSCOPE_LOG_DIR=${LOG_ROOT_DEFAULT}
EOF

    if [[ "${ENABLE_SERVICE}" == "1" ]]; then
      create_service
    fi

    if [[ "${ENABLE_NGINX}" == "1" ]]; then
      if [[ -n "${NGINX_FQDN}" ]]; then
        configure_nginx "${NGINX_FQDN}" "${NGINX_HTTPS:-0}"
      else
        warn "nginx requested but no FQDN provided — skipping nginx configuration."
        warn "Run the installer again with --nginx --fqdn <your-hostname> to configure nginx."
      fi
    fi
  fi

  # ── Restart service if it was running (update mode) ──────────────────────────
  if [[ "${IS_UPDATE}" -eq 1 && "${WINNING_SOURCE}" != "installed" ]]; then
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
      step "Restarting ${SERVICE_NAME} to apply update"
      ${SUDO} systemctl restart "${SERVICE_NAME}"
      ok "Service restarted"
    elif systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
      step "Starting ${SERVICE_NAME}"
      ${SUDO} systemctl start "${SERVICE_NAME}"
      ok "Service started"
    fi
  fi

  # ── Summary ───────────────────────────────────────────────────────────────────
  echo
  if [[ "${IS_UPDATE}" -eq 1 && "${WINNING_SOURCE}" != "installed" ]]; then
    ok "Update complete: ${INSTALLED_VER} → ${WINNING_VER}"
  elif [[ "${IS_UPDATE}" -eq 1 ]]; then
    ok "Dependencies refreshed (${WINNING_VER})"
  else
    ok "Installation complete"
  fi
  echo "Installed app: ${TARGET_APP}"
  echo "Virtualenv: ${VENV_DIR}"
  echo "Data dir: ${DATA_ROOT_DEFAULT}"
  echo "Log dir: ${LOG_ROOT_DEFAULT}"
  if [[ "${IS_UPDATE}" -ne 1 ]]; then
    if [[ "${ENABLE_SERVICE}" == "1" ]]; then
      echo "Service enabled: ${SERVICE_NAME}"
      echo "Status: systemctl status ${SERVICE_NAME}"
      echo "Logs: journalctl -fu ${SERVICE_NAME}"
    else
      echo "Run manually with:"
      echo "  source \"${VENV_DIR}/bin/activate\" && python \"${TARGET_APP}\""
    fi
  fi
  echo
  if [[ "${ENABLE_NGINX}" == "1" && -n "${NGINX_FQDN}" ]]; then
    if [[ "${NGINX_HTTPS:-0}" == "1" ]]; then
      echo "Open: https://${NGINX_FQDN}"
    else
      echo "Open: http://${NGINX_FQDN}"
    fi
  else
    echo "Open: http://<server-ip>:5000"
  fi
}

main "$@"
