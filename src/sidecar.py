#!/usr/bin/env python3
"""Passive ADS-B logger and WDG Wars uploader for one RTL-SDR receiver."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import csv
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


VERSION = "1.1.0"
DEFAULT_UPLOAD_URL = "https://wdgwars.pl/api/upload/"
DEFAULT_ME_URL = "https://wdgwars.pl/api/me"
ICAO_RE = re.compile(r"^[0-9A-F]{6}$")
API_KEY_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


def runtime_data_dir(platform_name: str | None = None) -> Path:
    configured = os.getenv("DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    platform_name = platform_name or os.name
    if platform_name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "Canadaverse" / "WDG-Aircraft-Sidecar"
    return Path("/data")


def api_key_file(platform_name: str | None = None) -> Path:
    configured = os.getenv("WDGWARS_API_KEY_FILE", "").strip()
    if configured:
        return Path(configured)
    platform_name = platform_name or os.name
    if platform_name == "nt":
        return runtime_data_dir(platform_name) / "wdgwars_api_key"
    return Path("/run/secrets/wdgwars_api_key")


def application_root(script_path: Path | None = None) -> Path:
    return (script_path or Path(__file__)).resolve().parent.parent


def windows_dump1090_command(root: Path | None = None) -> list[str]:
    root = root or application_root()
    device = os.getenv("SDR_DEVICE", "0").strip() or "0"
    if len(device) > 128 or any(character in device for character in "\r\n\0"):
        raise RuntimeError("SDR_DEVICE contains unsupported characters")
    return [
        str(root / "decoder" / "dump1090.exe"),
        "--config",
        str(root / "decoder" / "wdg-dump1090.cfg"),
        "--device",
        device,
        "--net",
    ]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass
class Aircraft:
    icao: str
    first_seen: int
    last_seen: int
    callsign: str = ""
    lat: float | None = None
    lon: float | None = None
    alt_ft: int = 0
    speed_kt: int = 0
    heading: int = 0

    @property
    def has_position(self) -> bool:
        return (
            self.lat is not None
            and self.lon is not None
            and -90 <= self.lat <= 90
            and -180 <= self.lon <= 180
            and (self.lat != 0 or self.lon != 0)
        )


class Tracker:
    """Merge SBS messages because callsign, position and speed arrive separately."""

    def __init__(self) -> None:
        self.aircraft: dict[str, Aircraft] = {}

    def feed(self, line: str, now: int | None = None) -> Aircraft | None:
        parts = line.split(",")
        if len(parts) < 11 or parts[0] != "MSG":
            return None
        icao = parts[4].strip().upper()
        if not ICAO_RE.fullmatch(icao):
            return None

        timestamp = now if now is not None else int(time.time())
        aircraft = self.aircraft.setdefault(
            icao, Aircraft(icao=icao, first_seen=timestamp, last_seen=timestamp)
        )
        aircraft.last_seen = timestamp
        message_type = parts[1].strip()

        try:
            if message_type == "1":
                callsign = parts[10].strip()[:16]
                if callsign:
                    aircraft.callsign = callsign
            elif message_type == "3" and len(parts) > 15:
                lat_text, lon_text = parts[14].strip(), parts[15].strip()
                if lat_text and lon_text:
                    lat, lon = float(lat_text), float(lon_text)
                    if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
                        aircraft.lat, aircraft.lon = lat, lon
                if parts[11].strip():
                    aircraft.alt_ft = int(float(parts[11]))
                if parts[12].strip():
                    aircraft.speed_kt = int(float(parts[12]))
                if parts[13].strip():
                    aircraft.heading = int(float(parts[13])) % 360
            elif message_type == "4" and len(parts) > 13:
                if parts[12].strip():
                    aircraft.speed_kt = int(float(parts[12]))
                if parts[13].strip():
                    aircraft.heading = int(float(parts[13])) % 360
        except (ValueError, IndexError):
            return None

        return aircraft if aircraft.has_position else None


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS aircraft (
                    icao TEXT PRIMARY KEY,
                    callsign TEXT NOT NULL,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    alt_ft INTEGER NOT NULL,
                    speed_kt INTEGER NOT NULL,
                    heading INTEGER NOT NULL,
                    upload_status TEXT NOT NULL DEFAULT 'pending',
                    uploaded_at INTEGER,
                    server_result TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def session(self):
        db = self.connect()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def observe(self, aircraft: Aircraft) -> None:
        if not aircraft.has_position:
            return
        with self.session() as db:
            db.execute(
                """
                INSERT INTO aircraft (
                    icao, callsign, first_seen, last_seen, lat, lon,
                    alt_ft, speed_kt, heading
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(icao) DO UPDATE SET
                    callsign = CASE WHEN excluded.callsign != ''
                                    THEN excluded.callsign ELSE aircraft.callsign END,
                    last_seen = MAX(aircraft.last_seen, excluded.last_seen),
                    lat = excluded.lat,
                    lon = excluded.lon,
                    alt_ft = excluded.alt_ft,
                    speed_kt = excluded.speed_kt,
                    heading = excluded.heading
                """,
                (
                    aircraft.icao,
                    aircraft.callsign,
                    aircraft.first_seen,
                    aircraft.last_seen,
                    aircraft.lat,
                    aircraft.lon,
                    aircraft.alt_ft,
                    aircraft.speed_kt,
                    aircraft.heading,
                ),
            )

    def pending(self, limit: int) -> list[dict]:
        with self.session() as db:
            rows = db.execute(
                """
                SELECT icao, callsign, first_seen, lat, lon,
                       alt_ft, speed_kt, heading
                FROM aircraft
                WHERE upload_status = 'pending'
                ORDER BY first_seen, icao
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "icao": row["icao"],
                "callsign": row["callsign"],
                "lat": row["lat"],
                "lon": row["lon"],
                "alt_ft": row["alt_ft"],
                "speed_kt": row["speed_kt"],
                "heading": row["heading"],
                "first_seen": str(row["first_seen"]),
                "type": "ADSB",
            }
            for row in rows
        ]

    def mark_uploaded(self, icaos: list[str], result: dict) -> None:
        if not icaos:
            return
        summary = json.dumps(
            {
                "aircraft_imported": result.get("aircraft_imported", 0),
                "aircraft_already_seen": result.get("aircraft_already_seen", 0),
            },
            separators=(",", ":"),
        )
        placeholders = ",".join("?" for _ in icaos)
        with self.session() as db:
            db.execute(
                f"""
                UPDATE aircraft
                SET upload_status = 'uploaded', uploaded_at = ?, server_result = ?
                WHERE icao IN ({placeholders})
                """,
                [int(time.time()), summary, *icaos],
            )

    def counts(self) -> dict[str, int]:
        with self.session() as db:
            rows = db.execute(
                "SELECT upload_status, COUNT(*) AS n FROM aircraft GROUP BY upload_status"
            ).fetchall()
        counts = {"total": 0, "pending": 0, "uploaded": 0}
        for row in rows:
            counts[row["upload_status"]] = row["n"]
            counts["total"] += row["n"]
        return counts

    def export_csv(self, output) -> None:
        fields = [
            "icao", "callsign", "first_seen", "last_seen", "lat", "lon",
            "alt_ft", "speed_kt", "heading", "upload_status", "uploaded_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        with self.session() as db:
            for row in db.execute(
                f"SELECT {','.join(fields)} FROM aircraft ORDER BY first_seen, icao"
            ):
                writer.writerow(dict(row))


class RuntimeState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.data = {
            "version": VERSION,
            "started_at": utc_now(),
            "dump1090_running": False,
            "sbs_connected": False,
            "messages": 0,
            "auth_ok": None,
            "upload_state": "starting",
            "last_aircraft": None,
            "last_upload": None,
            "last_error": None,
        }

    def set(self, **values) -> None:
        with self.lock:
            self.data.update(values)

    def increment(self, name: str, amount: int = 1) -> None:
        with self.lock:
            self.data[name] = int(self.data.get(name, 0)) + amount

    def flush(self, counts: dict[str, int]) -> None:
        with self.lock:
            snapshot = dict(self.data)
        snapshot["aircraft"] = counts
        snapshot["written_at"] = utc_now()
        snapshot["written_at_epoch"] = int(time.time())
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def sign_payload(api_key: str, data: dict, nonce: str | None = None) -> bytes:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    data_b64 = base64.b64encode(raw).decode("ascii")
    nonce = nonce or secrets.token_hex(8)
    signature = hmac.new(
        api_key.encode("utf-8"),
        (nonce + data_b64).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return json.dumps(
        {"data": data_b64, "nonce": nonce, "sig": signature},
        separators=(",", ":"),
    ).encode("utf-8")


def load_api_key() -> str:
    key_file = api_key_file()
    try:
        key = key_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read WDG Wars API key file: {key_file}") from exc
    if not API_KEY_RE.fullmatch(key):
        raise RuntimeError("WDG Wars API key must be exactly 64 hexadecimal characters")
    return key


def request_json(url: str, api_key: str, body: bytes | None = None, timeout: int = 30) -> dict:
    request = Request(url, data=body, method="POST" if body is not None else "GET")
    request.add_header("Accept", "application/json")
    runtime = "Windows-native" if os.name == "nt" else "Docker"
    request.add_header(
        "User-Agent",
        f"Canadaverse-WDG-Aircraft/{VERSION} ({runtime}; Python/{sys.version_info.major}.{sys.version_info.minor})",
    )
    request.add_header("X-API-Key", api_key)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def configure_api_key(platform_name: str | None = None) -> int:
    platform_name = platform_name or os.name
    if platform_name != "nt":
        raise RuntimeError("interactive API-key setup is provided by setup.sh on Linux")
    key = getpass.getpass("Paste your 64-character WDG Wars API key: ").strip()
    if not API_KEY_RE.fullmatch(key):
        raise RuntimeError("WDG Wars API key must be exactly 64 hexadecimal characters")
    try:
        profile = request_json(os.getenv("WDGWARS_ME_URL", DEFAULT_ME_URL), key, timeout=15)
    except HTTPError as exc:
        raise RuntimeError(f"WDG Wars rejected the API key (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not verify the API key with WDG Wars: {exc}") from exc
    if not profile.get("ok"):
        raise RuntimeError(
            f"WDG Wars rejected the API key: {profile.get('error', 'unknown error')}"
        )

    destination = api_key_file(platform_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp")
    try:
        temporary.write_text(key, encoding="utf-8")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print("API key authenticated and saved for this Windows account.")
    return 0


def upload_loop(store: Store, state: RuntimeState, stop: threading.Event) -> None:
    if os.getenv("UPLOAD_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        state.set(upload_state="disabled", auth_ok=None)
        stop.wait()
        return
    upload_url = os.getenv("WDGWARS_API_URL", DEFAULT_UPLOAD_URL)
    me_url = os.getenv("WDGWARS_ME_URL", DEFAULT_ME_URL)
    interval = env_int("UPLOAD_INTERVAL_SECONDS", 60, 15, 3600)
    batch_size = env_int("UPLOAD_BATCH_SIZE", 100, 1, 500)
    backoff = interval
    authenticated_at = 0.0

    while not stop.is_set():
        try:
            api_key = load_api_key()
            if time.time() - authenticated_at > 3600:
                profile = request_json(me_url, api_key, timeout=15)
                if not profile.get("ok"):
                    raise RuntimeError(f"WDG Wars authentication failed: {profile.get('error', 'unknown error')}")
                authenticated_at = time.time()
                state.set(auth_ok=True, upload_state="ready", last_error=None)

            aircraft = store.pending(batch_size)
            if not aircraft:
                state.set(upload_state="ready")
                stop.wait(10)
                continue

            payload = sign_payload(
                api_key,
                {"networks": [], "aircraft": aircraft, "meshcore_nodes": []},
            )
            state.set(upload_state=f"uploading {len(aircraft)} aircraft")
            result = request_json(upload_url, api_key, payload, timeout=90)
            store.mark_uploaded([item["icao"] for item in aircraft], result)
            imported = int(result.get("aircraft_imported", 0))
            seen = int(result.get("aircraft_already_seen", 0))
            state.set(
                auth_ok=True,
                upload_state="ready",
                last_upload={
                    "at": utc_now(),
                    "sent": len(aircraft),
                    "imported": imported,
                    "already_seen": seen,
                },
                last_error=None,
            )
            print(
                f"WDG Wars accepted batch: sent={len(aircraft)} imported={imported} already_seen={seen}",
                flush=True,
            )
            backoff = interval
            stop.wait(interval)
        except HTTPError as exc:
            state.set(
                auth_ok=False if exc.code in (401, 403) else None,
                upload_state=f"HTTP {exc.code}",
                last_error=f"WDG Wars HTTP {exc.code}",
            )
            print(f"WDG Wars upload error: HTTP {exc.code}", file=sys.stderr, flush=True)
            stop.wait(backoff)
            backoff = min(backoff * 2, 900)
        except (URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            state.set(upload_state="waiting to retry", last_error=str(exc))
            print(f"WDG Wars upload unavailable: {exc}", file=sys.stderr, flush=True)
            stop.wait(backoff)
            backoff = min(backoff * 2, 900)


def matching_sdrs(vendor: str, product: str, serial_number: str) -> list[Path]:
    matches = []
    for device in Path("/sys/bus/usb/devices").glob("*"):
        try:
            if (device / "idVendor").read_text().strip().lower() != vendor.lower():
                continue
            if (device / "idProduct").read_text().strip().lower() != product.lower():
                continue
            if serial_number and (device / "serial").read_text().strip() != serial_number:
                continue
            matches.append(device)
        except OSError:
            continue
    return matches


def start_windows_dump1090() -> subprocess.Popen:
    root = application_root()
    command = windows_dump1090_command(root)
    decoder = Path(command[0])
    config = Path(command[2])
    if not decoder.is_file() or not config.is_file():
        raise RuntimeError("the bundled Windows aircraft decoder is missing")
    print("Starting the Windows RTL-SDR decoder on local port 30003...", flush=True)
    process = subprocess.Popen(
        command,
        cwd=decoder.parent,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return process
    raise RuntimeError(
        "the RTL-SDR decoder could not start. Close other SDR programs and, if this "
        "is the first use on Windows, install the WinUSB driver with Zadig for the "
        "RTL-SDR Bulk-In Interface 0."
    )


def start_dump1090() -> subprocess.Popen:
    if os.name == "nt":
        return start_windows_dump1090()

    vendor = os.getenv("SDR_VENDOR_ID", "0bda")
    product = os.getenv("SDR_PRODUCT_ID", "2838")
    serial_number = os.getenv("SDR_SERIAL", "").strip()
    matches = matching_sdrs(vendor, product, serial_number)
    if len(matches) != 1:
        identity = f"{vendor}:{product}" + (f" serial {serial_number}" if serial_number else "")
        raise RuntimeError(
            f"expected exactly one SDR {identity}; found {len(matches)}"
        )

    port = env_int("SBS_PORT", 30003, 1024, 65535)
    selector = serial_number or "0"
    command = [
        os.getenv("DUMP1090_BIN", "/usr/local/bin/dump1090"),
        "--device-type", "rtlsdr",
        "--device", selector,
        "--net-bind-address", "127.0.0.1",
        "--net-sbs-port", str(port),
        "--quiet",
    ]
    gain = os.getenv("DUMP1090_GAIN", "").strip()
    ppm = os.getenv("DUMP1090_PPM", "").strip()
    if gain:
        float(gain)
        command.extend(["--gain", gain])
    if ppm:
        int(ppm)
        command.extend(["--ppm", ppm])
    print(
        f"Starting dump1090 for SDR {vendor}:{product} device {selector} on 127.0.0.1:{port}",
        flush=True,
    )
    return subprocess.Popen(command)


def sbs_loop(store: Store, state: RuntimeState, stop: threading.Event) -> None:
    host = "127.0.0.1"
    port = env_int("SBS_PORT", 30003, 1024, 65535)
    tracker = Tracker()
    persisted_at: dict[str, int] = {}
    persisted_callsign: dict[str, str] = {}

    while not stop.is_set():
        try:
            with socket.create_connection((host, port), timeout=5) as sock:
                sock.settimeout(2)
                state.set(sbs_connected=True, last_error=None)
                buffer = ""
                while not stop.is_set():
                    try:
                        chunk = sock.recv(8192)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buffer += chunk.decode("ascii", errors="ignore")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        state.increment("messages")
                        aircraft = tracker.feed(line.strip())
                        if not aircraft:
                            continue
                        now = int(time.time())
                        should_persist = (
                            now - persisted_at.get(aircraft.icao, 0) >= 30
                            or (aircraft.callsign and not persisted_callsign.get(aircraft.icao))
                        )
                        if should_persist:
                            store.observe(aircraft)
                            persisted_at[aircraft.icao] = now
                            persisted_callsign[aircraft.icao] = aircraft.callsign
                            state.set(
                                last_aircraft={
                                    "at": utc_now(),
                                    "icao": aircraft.icao,
                                    "callsign": aircraft.callsign,
                                    "lat": aircraft.lat,
                                    "lon": aircraft.lon,
                                }
                            )
                state.set(sbs_connected=False)
        except OSError as exc:
            state.set(sbs_connected=False, last_error=f"SBS connection: {exc}")
            stop.wait(3)


def run() -> int:
    if os.getenv("UPLOAD_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}:
        load_api_key()
    data_dir = runtime_data_dir()
    store = Store(data_dir / "aircraft.sqlite3")
    state = RuntimeState(data_dir / "status.json")
    stop = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    process = start_dump1090()
    state.set(dump1090_running=True)
    if os.name == "nt":
        print(f"Logging aircraft in {data_dir}", flush=True)
        print("Leave this window open. Press Ctrl+C to stop.", flush=True)
    threads = [
        threading.Thread(target=sbs_loop, args=(store, state, stop), daemon=True),
        threading.Thread(target=upload_loop, args=(store, state, stop), daemon=True),
    ]
    for thread in threads:
        thread.start()

    exit_code = 0
    try:
        while not stop.wait(5):
            if process.poll() is not None:
                decoder_error = f"dump1090 exited with code {process.returncode}"
                if os.name == "nt":
                    decoder_error += (
                        "; close other SDR programs and verify that WinUSB is installed "
                        "for the RTL-SDR Bulk-In Interface 0"
                    )
                state.set(
                    dump1090_running=False,
                    sbs_connected=False,
                    last_error=decoder_error,
                )
                print(f"Decoder error: {decoder_error}", file=sys.stderr, flush=True)
                exit_code = process.returncode or 1
                break
            state.flush(store.counts())
    finally:
        stop.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        state.set(dump1090_running=False, sbs_connected=False)
        state.flush(store.counts())
    return exit_code


def healthcheck(data_dir: Path) -> int:
    try:
        status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
        fresh = time.time() - int(status["written_at_epoch"]) < 90
        healthy = fresh and status.get("dump1090_running") and status.get("sbs_connected")
        return 0 if healthy else 1
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()
    data_dir = runtime_data_dir()

    if args.version:
        print(VERSION)
        return 0
    if args.healthcheck:
        return healthcheck(data_dir)
    if args.configure:
        return configure_api_key()
    if args.status:
        status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
        status["aircraft"] = Store(data_dir / "aircraft.sqlite3").counts()
        print(json.dumps(status, indent=2))
        return 0
    if args.export_csv:
        Store(data_dir / "aircraft.sqlite3").export_csv(sys.stdout)
        return 0
    return run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
