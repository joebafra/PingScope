#!/usr/bin/env bash
# PingScope installer for Ubuntu / Debian servers.
# Run from the PingScope directory:  sudo ./install.sh
set -euo pipefail

cd "$(dirname "$0")"
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Please run as root: sudo ./install.sh"; exit 1; }

bold "PingScope installer"
echo

# 1. Docker
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  read -rp "Docker (with the compose plugin) is not installed. Install it now? [Y/n] " a
  if [[ "${a:-Y}" =~ ^[Yy]$ ]]; then
    curl -fsSL https://get.docker.com | sh
  else
    echo "PingScope needs Docker. See docs/INSTALL.md for installing without Docker."; exit 1
  fi
fi
ok "Docker $(docker --version | awk '{print $3}' | tr -d ,)"

# 2. Domain and HTTPS
old_domain=""; old_email=""
if [ -f .env ]; then
  old_domain=$(grep -E '^PINGSCOPE_DOMAIN=' .env | cut -d= -f2- || true)
  old_email=$(grep -E '^ACME_EMAIL=' .env | cut -d= -f2- || true)
fi
echo
echo "Enter the hostname for PingScope, e.g. ping.example.nl."
echo "Its DNS A record (and AAAA for IPv6) must point to this server for the Let's Encrypt certificate."
echo "Leave empty to run without HTTPS on port 8080."
read -rp "Hostname [${old_domain}]: " domain
domain=${domain:-$old_domain}

email=""
if [ -n "$domain" ]; then
  read -rp "Email for Let's Encrypt notices [${old_email}]: " email
  email=${email:-$old_email}

  # DNS sanity check
  pub4=$(curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)
  pub6=$(curl -6 -fsS --max-time 5 https://api64.ipify.org 2>/dev/null || true)
  dns4=$(getent ahostsv4 "${domain%%,*}" 2>/dev/null | awk 'NR==1{print $1}' || true)
  dns6=$(getent ahostsv6 "${domain%%,*}" 2>/dev/null | awk 'NR==1{print $1}' || true)
  if [ -n "$pub4" ] && [ "$dns4" != "$pub4" ]; then
    warn "${domain%%,*} resolves to '${dns4:-nothing}' but this server's IPv4 is $pub4."
    warn "Fix the DNS record first, otherwise Let's Encrypt can't issue a certificate."
    read -rp "Continue anyway? [y/N] " a; [[ "${a:-N}" =~ ^[Yy]$ ]] || exit 1
  else
    ok "DNS: ${domain%%,*} → ${dns4:-?}${dns6:+, $dns6}"
  fi
  [ -n "$pub6" ] && [ -z "$dns6" ] && warn "This server has IPv6 ($pub6) but ${domain%%,*} has no AAAA record. Optional, but recommended."
fi

if [ -n "$domain" ]; then
  profiles="https"; listen="127.0.0.1"
else
  profiles=""; listen="0.0.0.0"
fi

cat > .env <<EOF
PINGSCOPE_DOMAIN=${domain}
ACME_EMAIL=${email}
COMPOSE_PROFILES=${profiles}
PINGSCOPE_LISTEN=${listen}
PINGSCOPE_PORT=8080
TZ=${TZ:-Europe/Amsterdam}
EOF
ok "Wrote .env"

# 3. Connectivity checks
if ping -6 -c1 -W2 2001:4860:4860::8888 >/dev/null 2>&1; then ok "IPv6 connectivity works"
else warn "This server can't reach the internet over IPv6; IPv6 targets will show as down."; fi
command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active" && {
  if [ -n "$domain" ]; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ok "Firewall (ufw): opened ports 80 and 443"
  else ufw allow 8080/tcp >/dev/null; ok "Firewall (ufw): opened port 8080"; fi
}

# 4. Start
mkdir -p data
echo
bold "Building and starting PingScope…"
docker compose up -d --build
[ -z "$domain" ] && docker rm -f pingscope-caddy >/dev/null 2>&1 || true

# 5. Automatic updates from GitHub
echo
read -rp "Install new versions from GitHub automatically (checked every 5 minutes)? [Y/n] " a
if [[ "${a:-Y}" =~ ^[Yy]$ ]]; then
  if GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes" git ls-remote origin -q >/dev/null 2>&1; then
    sed "s#/opt/pingscope#$PWD#g" deploy/pingscope-deploy.service > /etc/systemd/system/pingscope-deploy.service
    cp deploy/pingscope-deploy.timer /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now pingscope-deploy.timer >/dev/null 2>&1
    ok "Automatic updates enabled (log: journalctl -u pingscope-deploy)"
  else
    warn "This server can't read the GitHub repository without a password prompt."
    warn "Clone the public repository over HTTPS (https://github.com/joebafra/PingScope.git), or set up"
    warn "an SSH deploy key for a private fork (docs/INSTALL.md, section 2.1), then run ./install.sh again."
  fi
fi

# 6. Setup code
echo
session=""
for _ in $(seq 1 60); do
  session=$(curl -fsS --max-time 2 http://127.0.0.1:8080/api/session 2>/dev/null || true)
  [ -n "$session" ] && break
  sleep 1
done
code=""
if echo "$session" | grep -q '"setup_required":true'; then
  code=$(docker logs pingscope 2>&1 | grep -o 'setup code: [A-F0-9]*' | tail -1 | awk '{print $3}' || true)
fi
[ -z "$session" ] && warn "PingScope did not answer yet; check: docker logs pingscope"
url="http://$(hostname -I | awk '{print $1}'):8080"
[ -n "$domain" ] && url="https://${domain%%,*}"
bold "PingScope is running: $url"
if [ -n "$code" ]; then
  echo "Open $url/admin and create your admin account with setup code: $code"
else
  echo "Log in at $url/admin"
fi
[ -n "$domain" ] && echo "(The first HTTPS request can take a few seconds while the certificate is issued.)"
