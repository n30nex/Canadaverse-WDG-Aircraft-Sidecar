#!/usr/bin/env bash
set -euo pipefail

VID="${SDR_VENDOR_ID:-0bda}"
PID="${SDR_PRODUCT_ID:-2838}"
EXPECTED_SERIAL="${SDR_SERIAL:-00000001}"

for command in docker ip lsusb udevadm; do
  command -v "$command" >/dev/null || { echo "Missing required command: $command" >&2; exit 1; }
done
docker compose version >/dev/null

mapfile -t matches < <(lsusb -d "${VID}:${PID}")
if [[ ${#matches[@]} -ne 1 ]]; then
  echo "Expected exactly one SDR ${VID}:${PID}; found ${#matches[@]}. Nothing changed." >&2
  exit 1
fi

bus="$(awk '{print $2}' <<<"${matches[0]}")"
device="$(tr -d ':' <<<"$(awk '{print $4}' <<<"${matches[0]}")")"
node="/dev/bus/usb/${bus}/${device}"
serial="$(udevadm info --query=property --name="$node" | sed -n 's/^ID_SERIAL_SHORT=//p')"
model="$(udevadm info --query=property --name="$node" | sed -n 's/^ID_MODEL=//p')"
if [[ "$serial" != "$EXPECTED_SERIAL" ]]; then
  echo "Found ${model:-RTL-SDR}, but serial is '$serial' instead of '$EXPECTED_SERIAL'. Nothing changed." >&2
  exit 1
fi

plugdev_gid="$(getent group plugdev | cut -d: -f3)"
[[ -n "$plugdev_gid" ]] || { echo "The host has no plugdev group." >&2; exit 1; }
secret_group="wdgwars-aircraft"
if ! getent group "$secret_group" >/dev/null; then
  sudo groupadd --system "$secret_group"
fi
secret_gid="$(getent group "$secret_group" | cut -d: -f3)"
[[ -n "$secret_gid" ]] || { echo "Could not create the WDG Wars secret group." >&2; exit 1; }
lan_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([^ ]*\).*/\1/p' | head -1)"
[[ "$lan_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Could not detect the LAN IPv4 address." >&2; exit 1; }

umask 077
mkdir -p .secrets
if [[ ! -s .secrets/wdgwars_api_key ]]; then
  read -r -s -p "WDG Wars 64-character API key: " api_key
  echo
  [[ "$api_key" =~ ^[0-9A-Fa-f]{64}$ ]] || { echo "Invalid API key; nothing started." >&2; exit 1; }
  printf '%s' "$api_key" > .secrets/wdgwars_api_key
else
  api_key="$(<.secrets/wdgwars_api_key)"
  [[ "$api_key" =~ ^[0-9A-Fa-f]{64}$ ]] || { echo "Saved API key is invalid; nothing changed." >&2; exit 1; }
fi
unset api_key
sudo chgrp "$secret_group" .secrets/wdgwars_api_key
sudo chmod 0640 .secrets/wdgwars_api_key

rule="SUBSYSTEM==\"usb\", ATTR{idVendor}==\"${VID}\", ATTR{idProduct}==\"${PID}\", ATTR{serial}==\"${serial}\", MODE=\"0660\", GROUP=\"plugdev\""
printf '%s\n' "$rule" | sudo tee /etc/udev/rules.d/99-wdgwars-aircraft-sidecar.rules >/dev/null
sudo udevadm control --reload-rules
sudo chgrp plugdev "$node"
sudo chmod 0660 "$node"

if [[ ! -f .env ]]; then
  sed \
    -e "s/^SDR_VENDOR_ID=.*/SDR_VENDOR_ID=${VID}/" \
    -e "s/^SDR_PRODUCT_ID=.*/SDR_PRODUCT_ID=${PID}/" \
    -e "s/^SDR_SERIAL=.*/SDR_SERIAL=${serial}/" \
    -e "s/^RTLSDR_GID=.*/RTLSDR_GID=${plugdev_gid}/" \
    -e "s/^WDGWARS_GID=.*/WDGWARS_GID=${secret_gid}/" \
    -e "s/^WEB_BIND_IP=.*/WEB_BIND_IP=${lan_ip}/" \
    .env.example > .env
  chmod 0600 .env
fi
if grep -q '^WDGWARS_GID=' .env; then
  sed -i "s/^WDGWARS_GID=.*/WDGWARS_GID=${secret_gid}/" .env
else
  printf 'WDGWARS_GID=%s\n' "$secret_gid" >> .env
fi
chmod 0600 .env
grep -q '^WEB_BIND_IP=' .env || printf 'WEB_BIND_IP=%s\n' "$lan_ip" >> .env
grep -q '^WEB_PORT=' .env || printf 'WEB_PORT=8092\n' >> .env

echo "Starting Canadaverse WDG Aircraft Sidecar with ${model:-RTL-SDR} (${VID}:${PID}, serial ${serial})"
sudo docker compose pull
sudo docker compose up -d

for _ in {1..24}; do
  container_id="$(sudo docker compose ps -q aircraft-sidecar)"
  health="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' "$container_id" 2>/dev/null || true)"
  [[ "$health" == "healthy" ]] && break
  [[ "$health" == "unhealthy" ]] && break
  sleep 5
done

sudo docker compose ps
if [[ "$health" != "healthy" ]]; then
  echo "Sidecar did not become healthy. Recent logs:" >&2
  sudo docker compose logs --tail=40 aircraft-sidecar >&2
  exit 1
fi
web_bind_ip="$(sed -n 's/^WEB_BIND_IP=//p' .env | tail -1)"
web_port="$(sed -n 's/^WEB_PORT=//p' .env | tail -1)"
echo "Ready. Aircraft are being logged and submitted to WDG Wars."
echo "Dashboard: http://${web_bind_ip}:${web_port}"
