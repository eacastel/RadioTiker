#!/usr/bin/env bash
set -euo pipefail

# One-time host bootstrap for RadioTiker vNext on Ubuntu.
# Run as root:
#   sudo bash infra/scripts/bootstrap_vnext_host.sh admin@radio.tiker.es

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash $0 <certbot-email>" >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <certbot-email>" >&2
  exit 2
fi

CERTBOT_EMAIL="$1"
APP_USER="eacastel"
REPO_ROOT="/home/${APP_USER}/RadioTiker-vnext"
NGINX_SITE_SRC="${REPO_ROOT}/infra/nginx/next.radio.tiker.es"
NGINX_SITE_DST="/etc/nginx/sites-available/next.radio.tiker.es"
VNEXT_SERVICE_SRC="${REPO_ROOT}/infra/systemd/rt-streamer-vnext.service"
VNEXT_SERVICE_DST="/etc/systemd/system/rt-streamer-vnext.service"
PROVISION_SCRIPT="${REPO_ROOT}/infra/scripts/provision_rtunnel_key.sh"
PROVISION_SCRIPT_INSTALLED="/usr/local/sbin/radiotiker-provision-rtunnel-key"
SUDOERS_FILE="/etc/sudoers.d/radiotiker-vnext-rtunnel"
SSHD_DROPIN="/etc/ssh/sshd_config.d/radiotiker-rtunnel.conf"

apt-get update
apt-get install -y nginx certbot python3-certbot-nginx openssh-server

# rtunnel account (locked shell)
if ! id rtunnel >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin rtunnel
fi
install -d -m 700 -o rtunnel -g rtunnel /home/rtunnel/.ssh
touch /home/rtunnel/.ssh/authorized_keys
chown rtunnel:rtunnel /home/rtunnel/.ssh/authorized_keys
chmod 600 /home/rtunnel/.ssh/authorized_keys

# sshd restrictions for tunnel account
cat > "${SSHD_DROPIN}" <<'EOF'
Match User rtunnel
    AllowTcpForwarding remote
    GatewayPorts no
    X11Forwarding no
    PermitTTY no
    PermitUserRC no
EOF
sshd -t
systemctl restart ssh || systemctl restart sshd

# nginx vhost for next.radio.tiker.es
install -m 644 "${NGINX_SITE_SRC}" "${NGINX_SITE_DST}"
ln -sf "${NGINX_SITE_DST}" /etc/nginx/sites-enabled/next.radio.tiker.es
nginx -t
systemctl reload nginx

# TLS certificate for next.radio.tiker.es
certbot --nginx -d next.radio.tiker.es --non-interactive --agree-tos -m "${CERTBOT_EMAIL}" --redirect

# vnext api service
install -m 644 "${VNEXT_SERVICE_SRC}" "${VNEXT_SERVICE_DST}"
systemctl daemon-reload
systemctl enable --now rt-streamer-vnext.service

# Allow app user to provision rtunnel keys non-interactively (single script only).
# Install a root-owned runtime copy outside the git repo to avoid permission churn in workspace files.
install -m 750 -o root -g root "${PROVISION_SCRIPT}" "${PROVISION_SCRIPT_INSTALLED}"
cat > "${SUDOERS_FILE}" <<EOF
${APP_USER} ALL=(root) NOPASSWD: ${PROVISION_SCRIPT_INSTALLED}
EOF
chmod 440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}"

echo "Bootstrap complete."
echo "Verify:"
echo "  systemctl status rt-streamer-vnext.service --no-pager"
echo "  curl -I https://next.radio.tiker.es/streamer/api/health"
