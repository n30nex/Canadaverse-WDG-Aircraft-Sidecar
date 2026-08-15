import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen


MODULE_PATH = Path(__file__).parents[1] / "src" / "sidecar.py"
SPEC = importlib.util.spec_from_file_location("sidecar", MODULE_PATH)
sidecar = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
import sys
sys.modules[SPEC.name] = sidecar
SPEC.loader.exec_module(sidecar)


def sbs(message_type, icao="C0FFEE", callsign="", alt="", speed="", heading="", lat="", lon=""):
    fields = [
        "MSG", str(message_type), "", "", icao, "", "", "", "", "",
        callsign, alt, speed, heading, lat, lon, "", "", "", "", "", "",
    ]
    return ",".join(fields)


class SidecarTests(unittest.TestCase):
    def test_tracker_merges_sbs_fields_and_rejects_missing_position(self):
        tracker = sidecar.Tracker()
        self.assertIsNone(tracker.feed(sbs(1, callsign="ACA123"), now=100))
        self.assertIsNone(tracker.feed(sbs(4, speed="450", heading="271"), now=101))
        aircraft = tracker.feed(
            sbs(3, alt="35000", lat="43.5501", lon="-80.2497"), now=102
        )
        self.assertEqual(aircraft.icao, "C0FFEE")
        self.assertEqual(aircraft.callsign, "ACA123")
        self.assertEqual((aircraft.alt_ft, aircraft.speed_kt, aircraft.heading), (35000, 450, 271))
        self.assertIsNone(tracker.feed(sbs(3, icao="BAD", lat="91", lon="0"), now=103))

    def test_hmac_envelope_matches_watchdogsgo_contract(self):
        key = "a" * 64
        nonce = "0011223344556677"
        data = {
            "networks": [],
            "aircraft": [{"icao": "C0FFEE", "lat": 43.5, "lon": -80.2}],
            "meshcore_nodes": [],
        }
        envelope = json.loads(sidecar.sign_payload(key, data, nonce))
        expected = hmac.new(
            key.encode(),
            (nonce + envelope["data"]).encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(envelope["sig"], expected)
        self.assertEqual(json.loads(base64.b64decode(envelope["data"])), data)

    def test_public_wdg_profile_is_aggregated_and_redacted(self):
        profile = {
            "ok": True,
            "username": "private-user",
            "user_id": 42,
            "gang": "Royal City Recon",
            "gang_id": 581,
            "gang_role": "member",
            "total": 16515,
            "wifi": 6740,
            "ble": 9739,
            "mesh": 22,
            "aircraft": 14,
            "recent_7d": 16480,
            "recent_today": 3,
            "reinforce_total": 1002,
            "badges": ["plane_spotter", "gang_member"],
            "devices": [
                {"device_name": "private-phone", "networks": 100},
                {
                    "device_name": "adsb",
                    "aircraft": 14,
                    "uploads": 13,
                    "last_upload": "2026-08-15 06:18:57+00",
                },
            ],
            "recent_captures": [
                {
                    "lat": 43.42123456,
                    "lng": -80.33456789,
                    "ap_count": 4,
                    "defender_gang": "Example Team",
                    "when": "2026-08-15 06:00:00+00",
                },
                {
                    "lat": 43.422,
                    "lng": -80.333,
                    "ap_count": 2,
                    "defender_gang": "Example Team",
                    "when": "2026-08-15 06:05:00+00",
                },
            ],
        }
        public = sidecar.public_wdg_profile(profile)
        encoded = json.dumps(public)
        self.assertEqual(public["team"]["name"], "Royal City Recon")
        self.assertEqual(public["stats"]["aircraft"], 14)
        self.assertEqual(public["adsb"]["uploads"], 13)
        self.assertEqual(public["adsb"]["last_upload"], "2026-08-15T06:18:57+00:00")
        self.assertEqual(public["activity_grid"]["cells"][0]["events"], 2)
        self.assertEqual(public["activity_grid"]["cells"][0]["aps"], 6)
        self.assertNotIn("private-user", encoded)
        self.assertNotIn("private-phone", encoded)
        self.assertNotIn("43.42123456", encoded)
        self.assertNotIn("user_id", encoded)

    def test_store_deduplicates_by_icao_and_persists_upload_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = sidecar.Store(Path(directory) / "aircraft.sqlite3")
            first = sidecar.Aircraft(
                "C0FFEE", 100, 100, "", 43.5, -80.2, 30000, 400, 90
            )
            richer = sidecar.Aircraft(
                "C0FFEE", 200, 220, "ACA123", 43.6, -80.3, 31000, 410, 95
            )
            store.observe(first)
            store.observe(richer)
            pending = store.pending(10)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["first_seen"], "100")
            self.assertEqual(pending[0]["callsign"], "ACA123")
            store.mark_uploaded(
                ["C0FFEE"],
                {"aircraft_imported": 1, "aircraft_already_seen": 0},
            )
            self.assertEqual(store.pending(10), [])
            self.assertEqual(store.counts(), {"total": 1, "pending": 0, "uploaded": 1})
            self.assertEqual(
                store.upload_stats(),
                {"batches": 1, "sent": 1, "imported": 1, "already_seen": 0},
            )
            recent = store.recent()
            self.assertEqual((recent[0]["icao"], recent[0]["callsign"]), ("C0FFEE", "ACA123"))
            state = sidecar.RuntimeState(Path(directory) / "status.json")
            state.set(auth_ok=True, messages=42)
            dashboard = sidecar.dashboard_payload(store, state)
            self.assertEqual(dashboard["counts"]["uploaded"], 1)
            self.assertEqual(dashboard["uploads"]["imported"], 1)
            self.assertEqual(dashboard["runtime"]["messages"], 42)

    def test_dashboard_serves_health_page_and_json(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            store = sidecar.Store(Path(directory) / "aircraft.sqlite3")
            state = sidecar.RuntimeState(Path(directory) / "status.json")
            state.set(dump1090_running=True, sbs_connected=True, auth_ok=True)
            stop = threading.Event()
            with patch.dict(os.environ, {"WEB_PORT": str(port)}):
                thread = threading.Thread(
                    target=sidecar.web_loop, args=(store, state, stop), daemon=True
                )
                thread.start()
                for _ in range(20):
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/healthz") as response:
                            self.assertEqual(json.load(response), {"ok": True})
                        break
                    except OSError:
                        time.sleep(0.05)
                else:
                    self.fail("dashboard did not start")
                with urlopen(f"http://127.0.0.1:{port}/api/dashboard") as response:
                    self.assertEqual(json.load(response)["counts"]["total"], 0)
                with urlopen(f"http://127.0.0.1:{port}/") as response:
                    self.assertIn(b"Canadaverse WDG Airspace", response.read())
                stop.set()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())


class PackagingTests(unittest.TestCase):
    def test_setup_grants_secret_to_only_the_container_group(self):
        root = MODULE_PATH.parents[1]
        compose = (root / "compose.yaml").read_text()
        setup = (root / "setup.sh").read_text()
        self.assertIn('${WDGWARS_GID:?Run ./setup.sh to create the secret group}', compose)
        self.assertIn('secret_group="wdgwars-aircraft"', setup)
        self.assertIn('chgrp "$secret_group" .secrets/wdgwars_api_key', setup)
        self.assertIn('chmod 0640 .secrets/wdgwars_api_key', setup)


if __name__ == "__main__":
    unittest.main()
