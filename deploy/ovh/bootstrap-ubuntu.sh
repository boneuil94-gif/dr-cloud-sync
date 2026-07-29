#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then echo "Exécuter avec sudo." >&2; exit 1; fi
. /etc/os-release
if [[ "${ID}" != ubuntu || "${VERSION_ID}" != 24.04 ]]; then echo "Ubuntu 24.04 requis." >&2; exit 1; fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg ufw fail2ban unattended-upgrades apt-transport-https debian-keyring debian-archive-keyring

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin caddy

# Autoriser SSH AVANT d'activer UFW. Ne change ni le port, ni l'authentification SSH.
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment SSH
ufw allow 80/tcp comment HTTP
ufw allow 443/tcp comment HTTPS
ufw --force enable

cat >/etc/fail2ban/jail.d/drcloud-sshd.local <<'EOF'
[sshd]
enabled = true
port = ssh
backend = systemd
maxretry = 5
findtime = 10m
bantime = 15m
EOF
systemctl enable --now fail2ban docker

# Correctifs automatiques, sans redémarrage automatique imprévisible.
cat >/etc/apt/apt.conf.d/52drcloud-unattended <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
EOF
dpkg-reconfigure -f noninteractive unattended-upgrades
install -d -m 0750 -o root -g docker /var/backups/drcloud

echo "Préparation terminée. 8080 n'est pas autorisé par UFW et l'application le lie à 127.0.0.1."
echo "Vérifier dans une seconde session SSH avant tout durcissement SSH ultérieur."
