#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage:
  ./setup-nginx-https.sh <duckdns-domain> <certificate-email>

Example:
  ./setup-nginx-https.sh placepick.duckdns.org admin@example.com

The application is expected to be available on 127.0.0.1:8001.
Router TCP ports 80 and 443 must point to this server.
EOF
}

if [ "$#" -ne 2 ]; then
  usage >&2
  exit 2
fi

DOMAIN=$1
CERT_EMAIL=$2
BACKEND_PORT=8001

case "$DOMAIN" in
  *[!A-Za-z0-9.-]* | .* | *..* | *.)
    echo "Invalid domain: $DOMAIN" >&2
    exit 2
    ;;
esac

case "$CERT_EMAIL" in
  *@*.*) ;;
  *)
    echo "Invalid email: $CERT_EMAIL" >&2
    exit 2
    ;;
esac

if [ "$(uname -s)" != "Linux" ] || ! command -v apt-get >/dev/null 2>&1; then
  echo "This script supports Ubuntu/Debian Linux." >&2
  exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
  SUDO=''
elif command -v sudo >/dev/null 2>&1; then
  SUDO='sudo'
else
  echo "Run as root or install sudo." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required to start the application." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "The Docker Compose plugin is required." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Cannot access the Docker daemon." >&2
  exit 1
fi

echo "Domain:      $DOMAIN"
echo "Application: http://127.0.0.1:$BACKEND_PORT"

if command -v getent >/dev/null 2>&1; then
  RESOLVED_IPS=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)
  if [ -n "$RESOLVED_IPS" ]; then
    echo "DNS IPv4:   $RESOLVED_IPS"
  else
    echo "Warning: $DOMAIN does not resolve to an IPv4 address yet." >&2
  fi
fi

echo "Installing Nginx, Certbot, and curl..."
$SUDO apt-get update
$SUDO apt-get install -y nginx certbot python3-certbot-nginx curl

echo "Starting Placepick on port $BACKEND_PORT..."
docker compose up -d --build

echo "Waiting for Placepick..."
ATTEMPT=1
while [ "$ATTEMPT" -le 20 ]; do
  if command -v curl >/dev/null 2>&1 && curl --fail --silent --max-time 5 \
    "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
    break
  fi
  if [ "$ATTEMPT" -eq 20 ]; then
    echo "Placepick did not become ready on port $BACKEND_PORT." >&2
    docker compose logs --tail=100 app >&2
    exit 1
  fi
  sleep 3
  ATTEMPT=$((ATTEMPT + 1))
done

SITE_AVAILABLE="/etc/nginx/sites-available/$DOMAIN"
SITE_ENABLED="/etc/nginx/sites-enabled/$DOMAIN"

$SUDO tee "$SITE_AVAILABLE" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

$SUDO ln -sfn "$SITE_AVAILABLE" "$SITE_ENABLED"
$SUDO nginx -t
$SUDO systemctl enable --now nginx
$SUDO systemctl reload nginx

if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q '^Status: active'; then
  $SUDO ufw allow 'Nginx Full'
fi

echo "Requesting the Let's Encrypt certificate..."
$SUDO certbot --nginx \
  --domain "$DOMAIN" \
  --email "$CERT_EMAIL" \
  --agree-tos \
  --no-eff-email \
  --non-interactive \
  --redirect

$SUDO nginx -t
$SUDO systemctl reload nginx
$SUDO certbot renew --dry-run

echo "Checking HTTPS..."
curl --fail --silent --show-error --max-time 15 "https://$DOMAIN/" >/dev/null

cat <<EOF

HTTPS is ready:
  https://$DOMAIN

If you change .env, restart the app:
  docker compose up -d --force-recreate app
EOF
