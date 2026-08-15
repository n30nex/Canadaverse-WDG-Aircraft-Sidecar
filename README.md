# Canadaverse WDG Aircraft Sidecar

Passive, headless ADS-B aircraft logging and WDG Wars upload for a Raspberry Pi
and RTL-SDR. It is the aircraft companion to
[Canadaverse WDG Mesh Sidecar](https://github.com/n30nex/Canadaverse-WDG-Mesh-Sidecar):
the Biscuit Pro handles 2.4/5 GHz Wi-Fi, Heltec handles live MeshCore adverts,
and this container handles 1090 MHz aircraft.

## What it does

- Runs the current FlightAware `dump1090` decoder inside one Docker container.
- Reads SBS/BaseStation aircraft messages on loopback only; no ports are exposed.
- Logs each unique positioned ICAO aircraft in persistent SQLite storage.
- Uploads batches using WDG Wars' `aircraft` schema and HMAC-SHA256 envelope.
- Records confirmed `aircraft_imported` and `aircraft_already_seen` results.
- Serves a public read-only Royal City Recon dashboard with a dark interactive
  map, coarse recent-activity grid, live aircraft, verified collection totals,
  badges, profile progress, live receiver-rate meters, and persistent WDG
  upload statistics.
- Selects one exact RTL-SDR by USB vendor, product and serial; startup fails if
  the identity is absent or ambiguous.
- Receives only. It does not transmit radio, scan Wi-Fi, read Biscuit data, or
  submit MeshCore records.

The default receiver profile matches the unit proven on the Canadaverse Pi 5:
Nooelec NESDR SMArt v5, USB `0bda:2838`, serial `00000001`.

## Install on the Pi

Requirements: Raspberry Pi OS/Debian, Docker with Compose, one 1090 MHz-capable
RTL-SDR, a suitable 1090 MHz antenna, and a 64-character WDG Wars API key.

```bash
git clone https://github.com/n30nex/Canadaverse-WDG-Aircraft-Sidecar.git
cd Canadaverse-WDG-Aircraft-Sidecar
./setup.sh
```

The setup script:

1. verifies exactly one matching SDR and its serial number;
2. grants only that receiver to the host `plugdev` group;
3. prompts invisibly for the API key and stores it in a git-ignored, group-read-only
   file outside the image, accessible only to its dedicated host group and the container;
4. binds the dashboard to the Pi's detected LAN address on port `8092`;
5. pulls the ARM64/AMD64 image, starts Compose, and waits for a healthy decoder.

Open `http://PI_LAN_IP:8092` to see the Royal City Recon operations view. It
combines local positioned aircraft and upload acknowledgements with a sanitized,
five-minute WDG profile snapshot. The public API excludes the account name,
user ID, other device names, credentials, raw capture coordinates, SSIDs, and
BSSIDs. Recent capture locations are aggregated into roughly 1 km activity
cells and are explicitly not presented as territory ownership. The WDG totals
belong to the linked member profile; the API does not claim they are gang-wide.
The same scoped snapshot also shows reinforcement tiers, credits and bounties,
member rank when reported, and the rolling new-AP allowance without exposing
the account identity or precise capture locations.

The port binds only to the detected LAN address, not every host interface, and
does not conflict with the existing Pi services on ports 8080 or 8090. The
container has a read-only root filesystem, drops all Linux capabilities, and
runs as an unprivileged user.

## Check it

```bash
sudo docker compose ps
sudo docker compose logs -f aircraft-sidecar
sudo docker compose exec aircraft-sidecar python3 /app/sidecar.py --status
```

Export the local aircraft log as CSV:

```bash
sudo docker compose exec aircraft-sidecar \
  python3 /app/sidecar.py --export-csv > aircraft.csv
```

The status output separates:

- `total`: unique positioned ICAOs stored locally;
- `pending`: waiting for a confirmed WDG Wars response;
- `uploaded`: confirmed by the API;
- `last_upload.imported`: newly credited by WDG Wars;
- `last_upload.already_seen`: already credited to the same user.

## Update or stop

```bash
sudo docker compose pull
sudo docker compose up -d
```

```bash
sudo docker compose down
```

Stopping preserves the named-volume aircraft database. `docker compose down -v`
also deletes that log and should only be used intentionally.

## Receiver tuning

`dump1090` defaults to its highest supported tuner gain. If field testing shows
overload or a known oscillator offset, set `DUMP1090_GAIN` or `DUMP1090_PPM` in
`.env`, then run `sudo docker compose up -d` again. Leave both blank initially.

The SMArt v5 has no always-on bias tee. Do not enable or assume antenna power;
use a passive antenna unless an external powered LNA is deliberately installed.

For a local logging-only test, set `UPLOAD_ENABLED=false` in `.env`. Normal
operation should leave it at `true` so confirmed aircraft reach WDG Wars.

## Data and retry behavior

Only aircraft with a valid ICAO and non-zero latitude/longitude enter the upload
queue. Local uniqueness matches the WDG Wars per-user `(user, ICAO)` credit
model. A lost HTTP response leaves the batch pending; a later retry is safe
because the server reports previously credited aircraft as
`aircraft_already_seen` rather than double-crediting them.

The persistent database is stored in Docker volume `aircraft-data`. The WDG
Wars API key is never baked into the image, committed, printed, or included in
status output.

## Proven contract and upstream work

- Aircraft field parsing and upload behavior follow
  [LOCOSP/WatchDogsGo](https://github.com/LOCOSP/WatchDogsGo), inspected at
  commit `6412603d6fa7c8f2e6fe76873531d83c61adc63d` (MIT).
- Payload signing matches the tested contract in
  [Canadaverse-WDG-Mesh-Sidecar](https://github.com/n30nex/Canadaverse-WDG-Mesh-Sidecar),
  inspected at commit `96f5e6a659cdff38ca36936979942f15f38ddfab` (MIT).
- The image pins FlightAware `dump1090` commit
  `0339a57b89cd6e61856cbb13ae342c31ae7be5ac`.

This community project is unofficial and is not affiliated with or endorsed by
WDG Wars, WatchDogsGo, FlightAware, Nooelec, or Biscuit Shop.

## License

MIT
