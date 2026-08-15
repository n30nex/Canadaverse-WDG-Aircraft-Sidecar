import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


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
            store.mark_uploaded(["C0FFEE"], {"aircraft_imported": 1})
            self.assertEqual(store.pending(10), [])
            self.assertEqual(store.counts(), {"total": 1, "pending": 0, "uploaded": 1})


if __name__ == "__main__":
    unittest.main()
