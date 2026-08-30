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
            "reinforce": {"2": 573, "3": 429},
            "credits": {"balance": 42, "lifetime_earned": 91, "bounties_completed": 3},
            "your_rank": {"today": 5, "week": 12, "all_time": None, "top_n": 50},
            "new_ap_limit": {"used": 13514, "remaining": 486486, "cap": 500000, "window": "24h_rolling"},
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
        self.assertEqual(public["scope"], "linked_profile")
        self.assertEqual(public["reinforce"], {"level_2": 573, "level_3": 429})
        self.assertEqual(public["credits"]["bounties_completed"], 3)
        self.assertEqual(public["rank"], {"today": 5, "week": 12, "all_time": None, "top_n": 50})
        self.assertEqual(public["new_ap_limit"]["remaining"], 486486)
        self.assertEqual(public["adsb"]["uploads"], 13)
        self.assertEqual(public["adsb"]["last_upload"], "2026-08-15T06:18:57+00:00")
        self.assertEqual(public["activity_grid"]["cells"][0]["events"], 2)
        self.assertEqual(public["activity_grid"]["cells"][0]["aps"], 6)
        self.assertNotIn("private-user", encoded)
        self.assertNotIn("private-phone", encoded)
        self.assertNotIn("43.42123456", encoded)
        self.assertNotIn("user_id", encoded)

    def test_public_wdg_territory_matches_game_grid_and_redacts_users(self):
        territory = {
            "ok": True,
            "grid_lat": 0.02,
            "grid_lng": 0.02,
            "grid_through": "2026-08-15T15:21:17Z",
            "gangs": {
                "581": {"name": "Royal City Recon", "color": "#01c7fc", "members": 2},
                "292": {"name": "Other Team", "color": "not-a-color", "members": 4},
            },
            "cells": [
                {"lat": 43.4, "lng": -80.38, "gang_id": 581, "count": 2414, "users": 2, "relay": 1, "towers": 8, "user_id": 991},
                {"lat": 43.42, "lng": -80.36, "gang_id": 292, "count": 63, "users": 1, "relay": 0, "towers": 0, "user_id": 992},
                {"lat": 999, "lng": 999, "gang_id": 581, "count": 1},
            ],
        }
        public = sidecar.public_wdg_territory(territory, 581)
        encoded = json.dumps(public)
        self.assertEqual((public["grid_lat"], public["grid_lon"]), (0.02, 0.02))
        self.assertEqual(public["grid_through"], "2026-08-15T15:21:17+00:00")
        self.assertEqual(public["team_cells"], 1)
        self.assertEqual(len(public["cells"]), 2)
        self.assertEqual(public["cells"][0]["aps"], 2414)
        self.assertTrue(public["cells"][0]["ours"])
        self.assertEqual(public["teams"]["581"]["color"], "#01c7fc")
        self.assertEqual(public["teams"]["292"]["color"], "#6b7280")
        self.assertNotIn("user_id", encoded)
        self.assertNotIn("991", encoded)

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
            state.set(auth_ok=True, messages=42, wdg={"team": {"id": 581}})
            with patch.object(store, "recent", wraps=store.recent) as recent:
                dashboard = sidecar.dashboard_payload(store, state)
                recent.assert_called_once_with(120)
            self.assertEqual(dashboard["counts"]["uploaded"], 1)
            self.assertEqual(dashboard["uploads"]["imported"], 1)
            self.assertEqual(dashboard["runtime"]["messages"], 42)
            self.assertTrue(dashboard["context_included"])
            compact = sidecar.dashboard_payload(store, state, include_context=False)
            self.assertFalse(compact["context_included"])
            self.assertNotIn("wdg", compact["runtime"])

    def test_runtime_health_requires_fresh_sbs_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            state = sidecar.RuntimeState(Path(directory) / "status.json")
            started = state.snapshot()["started_at_epoch"]
            state.set(dump1090_running=True, sbs_connected=True, web_running=True)
            self.assertTrue(sidecar.runtime_healthy(state.snapshot(), now=started + 299))
            self.assertFalse(sidecar.runtime_healthy(state.snapshot(), now=started + 300))
            state.set(last_message_epoch=started + 300)
            self.assertFalse(sidecar.runtime_healthy(state.snapshot(), now=started + 301))
            state.set(last_sbs_activity_epoch=started + 300)
            self.assertTrue(sidecar.runtime_healthy(state.snapshot(), now=started + 301))

    def test_store_sets_sqlite_busy_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            store = sidecar.Store(Path(directory) / "aircraft.sqlite3")
            with store.session() as db:
                self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

    def test_sbs_loop_ignores_non_message_keepalives(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            store = sidecar.Store(Path(directory) / "aircraft.sqlite3")
            state = sidecar.RuntimeState(Path(directory) / "status.json")
            stop = threading.Event()

            def send_lines():
                connection, _ = server.accept()
                with connection:
                    payload = ("\r\nkeepalive\r\n" + sbs(1) + "\r\n").encode()
                    connection.sendall(payload)
                    stop.wait(1)

            sender = threading.Thread(target=send_lines, daemon=True)
            sender.start()
            with patch.dict(os.environ, {"SBS_PORT": str(port)}):
                worker = threading.Thread(
                    target=sidecar.sbs_loop, args=(store, state, stop), daemon=True
                )
                worker.start()
                for _ in range(20):
                    if state.snapshot()["messages"] == 1:
                        break
                    time.sleep(0.05)
                stop.set()
                worker.join(timeout=2)
            sender.join(timeout=2)
            runtime = state.snapshot()
            self.assertEqual(runtime["messages"], 1)
            self.assertIsNotNone(runtime["last_sbs_activity_at"])
            self.assertIsNotNone(runtime["last_message_at"])

    def test_sbs_keepalive_keeps_transport_fresh_without_counting_aircraft(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            store = sidecar.Store(Path(directory) / "aircraft.sqlite3")
            state = sidecar.RuntimeState(Path(directory) / "status.json")
            stop = threading.Event()

            def send_keepalive():
                connection, _ = server.accept()
                with connection:
                    connection.sendall(b"\r\nkeepalive\r\n")
                    stop.wait(1)

            sender = threading.Thread(target=send_keepalive, daemon=True)
            sender.start()
            with patch.dict(os.environ, {"SBS_PORT": str(port)}):
                worker = threading.Thread(
                    target=sidecar.sbs_loop, args=(store, state, stop), daemon=True
                )
                worker.start()
                for _ in range(20):
                    if state.snapshot()["last_sbs_activity_at"]:
                        break
                    time.sleep(0.05)
                stop.set()
                worker.join(timeout=2)
            sender.join(timeout=2)
            runtime = state.snapshot()
            self.assertEqual(runtime["messages"], 0)
            self.assertIsNone(runtime["last_message_at"])
            self.assertIsNotNone(runtime["last_sbs_activity_at"])
            self.assertTrue(sidecar.sbs_stream_fresh(runtime))

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
                    self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                with urlopen(f"http://127.0.0.1:{port}/api/dashboard?compact=1") as response:
                    payload = json.load(response)
                    self.assertFalse(payload["context_included"])
                    self.assertNotIn("wdg", payload["runtime"])
                with urlopen(f"http://127.0.0.1:{port}/") as response:
                    self.assertIn(b"Canadaverse WDG Airspace", response.read())
                stop.set()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())


class PackagingTests(unittest.TestCase):
    def test_dashboard_refresh_is_bounded_and_non_overlapping(self):
        dashboard = (MODULE_PATH.parent / "dashboard.html").read_text()
        self.assertIn("const MAP_AIRCRAFT_LIMIT = 120", dashboard)
        self.assertIn("const CLUSTER_CELL_PX = 72", dashboard)
        self.assertIn('class="aircraft-cluster', dashboard)
        self.assertIn('id="meter-rate"', dashboard)
        self.assertIn('data-layer="territory"', dashboard)
        self.assertIn("L.rectangle", dashboard)
        self.assertIn("territorySignature", dashboard)
        self.assertIn("previousMessageSample", dashboard)
        self.assertIn("renderProfileProgress", dashboard)
        self.assertIn("/api/dashboard?compact=1", dashboard)
        self.assertIn("new AbortController()", dashboard)
        self.assertIn('id="aircraft-search"', dashboard)
        self.assertIn("runtime.last_message_at", dashboard)
        self.assertIn("if (refreshInFlight) return", dashboard)
        self.assertIn('document.addEventListener("visibilitychange"', dashboard)
        self.assertNotIn("setInterval(refresh", dashboard)

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
