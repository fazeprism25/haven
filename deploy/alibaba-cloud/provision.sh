#!/usr/bin/env bash
# Provisions a fresh Ubuntu 22.04 Alibaba Cloud instance (Simple Application
# Server or ECS -- same OS, same steps either way) to run Haven's existing
# FastAPI server behind nginx, unmodified.
#
# Run as root (or via sudo) on the instance itself:
#   sudo bash provision.sh
#
# Idempotent: safe to re-run after a `git pull` to pick up new commits, or
# to fix a failed step -- every stage checks before it creates/overwrites.
set -euo pipefail

HAVEN_REPO_URL="${HAVEN_REPO_URL:-https://github.com/fazeprism25/haven.git}"
HAVEN_HOME="/opt/haven"
HAVEN_USER="haven"
SERVICE_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Run this as root (sudo bash provision.sh)." >&2
  exit 1
fi

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip git nginx apache2-utils ufw certbot python3-certbot-nginx

echo "==> Creating the '${HAVEN_USER}' system user"
if ! id -u "${HAVEN_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "${HAVEN_HOME}" --shell /usr/sbin/nologin "${HAVEN_USER}"
fi

echo "==> Fetching Haven"
if [[ -d "${HAVEN_HOME}/.git" ]]; then
  sudo -u "${HAVEN_USER}" git -C "${HAVEN_HOME}" pull --ff-only
else
  # Home dir already exists (created by useradd) and must be empty for clone.
  find "${HAVEN_HOME}" -mindepth 1 -delete
  sudo -u "${HAVEN_USER}" git clone --depth 1 "${HAVEN_REPO_URL}" "${HAVEN_HOME}"
fi

echo "==> Creating the virtualenv and installing dependencies"
if [[ ! -d "${HAVEN_HOME}/.venv" ]]; then
  sudo -u "${HAVEN_USER}" python3 -m venv "${HAVEN_HOME}/.venv"
fi
sudo -u "${HAVEN_USER}" "${HAVEN_HOME}/.venv/bin/pip" install --upgrade pip -q
sudo -u "${HAVEN_USER}" "${HAVEN_HOME}/.venv/bin/pip" install -q -r "${HAVEN_HOME}/obsidian/server/requirements.txt"

echo "==> Preparing config/ and haven_data/"
sudo -u "${HAVEN_USER}" mkdir -p "${HAVEN_HOME}/haven_data"
if [[ ! -f "${HAVEN_HOME}/config/manager_ai.env" ]]; then
  sudo -u "${HAVEN_USER}" cp "${HAVEN_HOME}/config/manager_ai.env.example" "${HAVEN_HOME}/config/manager_ai.env"
  echo "    NOTE: set MANAGER_AI_API_KEY in ${HAVEN_HOME}/config/manager_ai.env"
  echo "    (real 'Remember'/extraction calls raise a clear error until you do -- demo endpoints work either way)"
fi

echo "==> Installing the systemd service"
install -m 644 "${SERVICE_SRC}/haven.service" /etc/systemd/system/haven.service
systemctl daemon-reload
systemctl enable haven
systemctl restart haven

echo "==> Configuring nginx"
install -m 644 "${SERVICE_SRC}/nginx.haven.conf" /etc/nginx/sites-available/haven.conf
ln -sf /etc/nginx/sites-available/haven.conf /etc/nginx/sites-enabled/haven.conf
rm -f /etc/nginx/sites-enabled/default

if [[ ! -f /etc/nginx/haven.htpasswd ]]; then
  echo "==> Setting up HTTP Basic Auth (protects everything except /api/v1/health)"
  read -rp "    Basic auth username [haven]: " BASIC_AUTH_USER
  BASIC_AUTH_USER="${BASIC_AUTH_USER:-haven}"
  htpasswd -c /etc/nginx/haven.htpasswd "${BASIC_AUTH_USER}"
fi

nginx -t
systemctl reload nginx

echo "==> Configuring the firewall (ufw)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Done"
echo "    Service status:  systemctl status haven"
echo "    Public health check (no auth):  curl http://<this-instance-public-ip>/api/v1/health"
echo "    Dashboard (basic auth required): http://<this-instance-public-ip>/dashboard"
echo "    If you have a domain pointed here: sudo certbot --nginx -d your.domain.com"
