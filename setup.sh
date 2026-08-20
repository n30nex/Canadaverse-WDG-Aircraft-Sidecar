#!/usr/bin/env bash
set -euo pipefail

VID="${SDR_VENDOR_ID:-0bda}"
PID="${SDR_PRODUCT_ID:-2838}"
EXPECTED_SERIAL="${SDR_SERIAL:-}"

[[ "$VID" =~ ^[0-9A-Fa-f]{4}$ && "$PID" =~ ^[0-9A-Fa-f]{4}$ ]] || {
  echo "SDR_VENDOR_ID and SDR_PRODUCT_ID must each be four hexadecimal characters." >&2
  exit 1
}
[[ -z "$EXPECTED_SERIAL" || "$EXPECTED_SERIAL" =~ ^[A-Za-z0-9._:-]{1,64}$ ]] || {
  echo "SDR_SERIAL contains unsupported characters." >&2
  exit 1
}

for command in docker getent lsusb udevadm; do
  command -v "$command" >/dev/null || {
    echo "Missing required command: $command" >&2
    exit 1
  }
done
docker compose version >/dev/null

mapfile -t candidates < <(lsusb -d "${VID}:${PID}")
matches=()
serials=()
for entry in "${candidates[@]}"; do
  bus="$(awk '{print $2}' <<<"$entry")"
  device="$(tr -d ':' <<<"$(awk '{print $4}' <<<"$entry")")"
  candidate="/dev/bus/usb/${bus}/${device}"
  candidate_serial="$(udevadm info --query=property --name="$candidate" | sed -n 's/^ID_SERIAL_SHORT=//p')"
  if [[ -z "$EXPECTED_SERIAL" || "$candidate_serial" == "$EXPECTED_SERIAL" ]]; then
    matches+=("$candidate")
    serials+=("$candidate_serial")
  fi
done

if [[ ${#matches[@]} -ne 1 ]]; then
  identity="${VID}:${PID}"
  [[ -n "$EXPECTED_SERIAL" ]] && identity+=" serial ${EXPECTED_SERIAL}"
  echo "Expected exactly one RTL-SDR ${identity}; found ${#matches[@]}. Nothing changed." >&2
  echo "Run lsusb to find the device IDs. For an uncommon compatible tuner, run:" >&2
  echo "  SDR_VENDOR_ID=vvvv SDR_PRODUCT_ID=pppp ./setup.sh" >&2
  echo "If multiple matching tuners are attached, also set SDR_SERIAL." >&2
  exit 1
fi

node="${matches[0]}"
serial="${serials[0]}"
model="$(udevadm info --query=property --name="$node" | sed -n 's/^ID_MODEL=//p')"
[[ -z "$serial" || "$serial" =~ ^[A-Za-z0-9._:-]{1,64}$ ]] || {
  echo "The SDR serial contains unsupported characters. Nothing changed." >&2
  exit 1
}

if ! getent group plugdev >/dev/null; then
  sudo groupadd --system plugdev
fi
plugdev_gid="$(getent group plugdev | cut -d: -f3)"
[[ -n "$plugdev_gid" ]] || {
  echo "Could not create or read the plugdev group." >&2
  exit 1
}

secret_group="wdgwars-aircraft"
if ! getent group "$secret_group" >/dev/null; then
  sudo groupadd --system "$secret_group"
fi
secret_gid="$(getent group "$secret_group" | cut -d: -f3)"
[[ -n "$secret_gid" ]] || {
  echo "Could not create the WDG Wars secret group." >&2
  exit 1
}

umask 077
mkdir -p .secrets
if [[ ! -s .secrets/wdgwars_api_key ]]; then
  read -r -s -p "WDG Wars 64-character API key: " api_key
  echo
  [[ "$api_key" =~ ^[0-9A-Fa-f]{64}$ ]] || {
    echo "Invalid API key; nothing started." >&2
    exit 1
  }
  printf '%s' "$api_key" > .secrets/wdgwars_api_key
else
  api_key="$(<.secrets/wdgwars_api_key)"
  [[ "$api_key" =~ ^[0-9A-Fa-f]{64}$ ]] || {
    echo "Saved API key is invalid; nothing changed." >&2
    exit 1
  }
fi
unset api_key
sudo chgrp "$secret_group" .secrets/wdgwars_api_key
sudo chmod 0640 .secrets/wdgwars_api_key

serial_rule=""
[[ -n "$serial" ]] && serial_rule=", ATTR{serial}==\"${serial}\""
rule="SUBSYSTEM==\"usb\", ATTR{idVendor}==\"${VID}\", ATTR{idProduct}==\"${PID}\"${serial_rule}, MODE=\"0660\", GROUP=\"plugdev\""
printf '%s\n' "$rule" | sudo tee /etc/udev/rules.d/99-wdgwars-aircraft-sidecar.rules >/dev/null
sudo udevadm control --reload-rules
sudo chgrp plugdev "$node"
sudo chmod 0660 "$node"

[[ -f .env ]] || cp .env.example .env
set_env() {
  local name="$1" value="$2"
  if grep -q "^${name}=" .env; then
    sed -i "s#^${name}=.*#${name}=${value}#" .env
  else
    printf '%s=%s\n' "$name" "$value" >> .env
  fi
}
set_env SDR_VENDOR_ID "$VID"
set_env SDR_PRODUCT_ID "$PID"
set_env SDR_SERIAL "$serial"
set_env RTLSDR_GID "$plugdev_gid"
set_env WDGWARS_GID "$secret_gid"
sed -i '/^WEB_BIND_IP=/d;/^WEB_PORT=/d;/^WDGWARS_PROFILE_INTERVAL_SECONDS=/d' .env
chmod 0600 .env

selector="${serial:-device 0}"
echo "Starting WDG Aircraft Sidecar with ${model:-RTL-SDR} (${VID}:${PID}, ${selector})"
sudo docker compose pull
sudo docker compose up -d

health="starting"
for _ in {1..24}; do
  container_id="$(sudo docker compose ps -q aircraft-sidecar)"
  if [[ -n "$container_id" ]]; then
    health="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' "$container_id" 2>/dev/null || true)"
  fi
  [[ "$health" == "healthy" || "$health" == "unhealthy" ]] && break
  sleep 5
done

sudo docker compose ps
if [[ "$health" != "healthy" ]]; then
  echo "Sidecar did not become healthy. Recent logs:" >&2
  sudo docker compose logs --tail=40 aircraft-sidecar >&2
  exit 1
fi

auth_ok="$(sudo docker compose exec -T aircraft-sidecar python3 -c \
  'import json; print(json.load(open("/data/status.json")).get("auth_ok"))' \
  2>/dev/null || true)"
if [[ "$auth_ok" != "True" ]]; then
  echo "Receiver is healthy, but WDG API authentication has not succeeded." >&2
  echo "Check the saved API key and internet connection, then run ./setup.sh again." >&2
  sudo docker compose logs --tail=40 aircraft-sidecar >&2
  exit 1
fi

echo "Ready. Aircraft are being logged and submitted to WDG Wars."
echo "Check progress with: sudo docker compose exec aircraft-sidecar python3 /app/sidecar.py --status"
