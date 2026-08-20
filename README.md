# WDG Aircraft Sidecar

Turn one compatible RTL-SDR into a headless WDG Wars aircraft collector on
Windows or Linux.

Plug in the receiver, enter your WDG API key, and leave it running. There is no
web UI and no public network port.

## What it does

- runs an ADS-B decoder and the WDG uploader together;
- records each unique positioned ICAO aircraft in a persistent SQLite database;
- sends signed `aircraft` batches to WDG Wars;
- retries safely after network failures;
- records whether WDG imported an aircraft or had already credited it;
- runs unprivileged with a read-only filesystem and a narrowly readable API-key
  secret.

It is receive-only. It does not transmit radio or collect Wi-Fi, Bluetooth, or
MeshCore data.

## Windows 10/11 quick start

1. Download `WDG-Aircraft-Sidecar-Windows-x64-v1.1.0.zip` from the
   [v1.1.0 release](https://github.com/n30nex/WDG-Aircraft-Sidecar/releases/tag/v1.1.0).
2. Extract the entire ZIP and plug in the RTL-SDR.
3. Double-click `Start-WDG-Aircraft-Sidecar.cmd`.
4. Enter the 64-character WDG Wars API key when prompted. Input is hidden.

The Windows package is portable: it needs neither Docker nor a separate Python
installation. If the decoder cannot open a new receiver, install WinUSB for its
`Bulk-In, Interface 0` with [Zadig](https://zadig.akeo.ie/), reconnect it, and
start again. The included `README-WINDOWS.txt` gives guarded step-by-step driver
instructions.

Double-click `Status.cmd` to see received, pending, and WDG-confirmed aircraft
counts. Data and the key stay under
`%LOCALAPPDATA%\Canadaverse\WDG-Aircraft-Sidecar` for that Windows account.

The bundled decoder has one listener only: SBS data on `127.0.0.1:30003`. It is
not reachable from the LAN and does not include the upstream decoder web UI.

## Linux requirements

- ARM64 or AMD64 Linux, including Raspberry Pi OS or Debian;
- Docker with the Compose plugin;
- `lsusb` and `udevadm`;
- one RTL2832U-based RTL-SDR supported by `librtlsdr`;
- a 1090 MHz antenna;
- a 64-character WDG Wars API key.

The zero-configuration path targets the common USB ID `0bda:2838`, used by
many Nooelec, RTL-SDR Blog, and generic RTL2832U receivers.

## Linux install

```bash
git clone --depth 1 https://github.com/n30nex/WDG-Aircraft-Sidecar.git
cd Canadaverse-WDG-Aircraft-Sidecar
./setup.sh
```

The script finds the receiver and its serial number, prompts invisibly for the
API key, installs a narrow USB permission rule, pulls the multi-architecture
image, and waits until the decoder is healthy.

For an uncommon but `librtlsdr`-compatible USB ID:

```bash
SDR_VENDOR_ID=vvvv SDR_PRODUCT_ID=pppp ./setup.sh
```

If more than one matching receiver is connected, select one explicitly:

```bash
SDR_SERIAL=your_serial ./setup.sh
```

All three values can be supplied together. Run `lsusb` to find the four-digit
vendor and product IDs.

## Check it

```bash
sudo docker compose ps
sudo docker compose logs -f aircraft-sidecar
sudo docker compose exec aircraft-sidecar python3 /app/sidecar.py --status
```

Useful status fields:

- `aircraft.total`: unique positioned ICAOs stored locally;
- `aircraft.pending`: waiting for a confirmed WDG response;
- `aircraft.uploaded`: confirmed by WDG;
- `last_upload.imported`: newly credited aircraft in the last batch;
- `last_upload.already_seen`: aircraft WDG had already credited.

Export the local log:

```bash
sudo docker compose exec aircraft-sidecar \
  python3 /app/sidecar.py --export-csv > aircraft.csv
```

## Configuration

`setup.sh` writes receiver settings to `.env` and stores the API key in
`.secrets/wdgwars_api_key`. Both paths are ignored by Git. The key is not
baked into the image, printed, or included in status output.

Optional `.env` settings:

```dotenv
DUMP1090_GAIN=
DUMP1090_PPM=
UPLOAD_INTERVAL_SECONDS=60
UPLOAD_BATCH_SIZE=100
UPLOAD_ENABLED=true
```

Leave gain and PPM blank initially. Set `UPLOAD_ENABLED=false` for a local
logging-only test.

## Update or stop

```bash
sudo docker compose pull
sudo docker compose up -d
```

```bash
sudo docker compose down
```

The named `aircraft-data` volume survives normal updates and stops. Running
`docker compose down -v` also deletes the local aircraft database.

## Data and retries

Only aircraft with a valid ICAO and non-zero latitude/longitude enter the upload
queue. A lost HTTP response leaves a batch pending. Retrying is safe because WDG
reports previously credited aircraft as `aircraft_already_seen`.

## Proven contract and upstream work

- Aircraft parsing follows
  [LOCOSP/WatchDogsGo](https://github.com/LOCOSP/WatchDogsGo), inspected at
  commit `6412603d6fa7c8f2e6fe76873531d83c61adc63d` (MIT).
- Payload signing follows
  [Canadaverse WDG Mesh Sidecar](https://github.com/n30nex/Canadaverse-WDG-Mesh-Sidecar),
  inspected at commit `96f5e6a659cdff38ca36936979942f15f38ddfab` (MIT).
- The image pins FlightAware `dump1090` commit
  `0339a57b89cd6e61856cbb13ae342c31ae7be5ac`.
- The Windows package builds
  [gvanem/Dump1090](https://github.com/gvanem/Dump1090) commit
  `252cef736d24e146545aecf7f316c289ef82b3b4` from source. A small published
  patch removes its RAW/HTTP listeners and binds SBS to loopback. Complete
  corresponding decoder source is attached to the release.
- The Windows runtime is the checksum-pinned official CPython 3.13.15
  embeddable x64 package.

This unofficial community project is not affiliated with or endorsed by WDG
Wars, WatchDogsGo, FlightAware, or any RTL-SDR manufacturer.

## License

MIT
