import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


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
    def test_windows_paths_and_decoder_command_are_portable(self):
        with tempfile.TemporaryDirectory() as directory:
            local_app_data = Path(directory) / "Local"
            package_root = Path(directory) / "Package"
            with patch.dict(
                sidecar.os.environ,
                {"LOCALAPPDATA": str(local_app_data)},
                clear=True,
            ):
                self.assertEqual(
                    sidecar.runtime_data_dir("nt"),
                    local_app_data / "Canadaverse" / "WDG-Aircraft-Sidecar",
                )
                self.assertEqual(
                    sidecar.api_key_file("nt"),
                    local_app_data
                    / "Canadaverse"
                    / "WDG-Aircraft-Sidecar"
                    / "wdgwars_api_key",
                )
                self.assertEqual(
                    sidecar.windows_dump1090_command(package_root),
                    [
                        str(package_root / "decoder" / "dump1090.exe"),
                        "--config",
                        str(package_root / "decoder" / "wdg-dump1090.cfg"),
                        "--device",
                        "0",
                        "--net",
                    ],
                )

    def test_windows_configuration_authenticates_before_saving_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "key"
            with (
                patch.dict(
                    sidecar.os.environ,
                    {"WDGWARS_API_KEY_FILE": str(secret)},
                    clear=True,
                ),
                patch.object(sidecar.getpass, "getpass", return_value="a" * 64),
                patch.object(sidecar, "request_json", return_value={"ok": True}),
            ):
                self.assertEqual(sidecar.configure_api_key("nt"), 0)
            self.assertEqual(secret.read_text(), "a" * 64)

    def test_tracker_merges_sbs_fields_and_rejects_missing_position(self):
        tracker = sidecar.Tracker()
        self.assertIsNone(tracker.feed(sbs(1, callsign="ACA123"), now=100))
        self.assertIsNone(tracker.feed(sbs(4, speed="450", heading="271"), now=101))
        aircraft = tracker.feed(
            sbs(3, alt="35000", lat="43.5501", lon="-80.2497"), now=102
        )
        self.assertEqual(aircraft.icao, "C0FFEE")
        self.assertEqual(aircraft.callsign, "ACA123")
        self.assertEqual(
            (aircraft.alt_ft, aircraft.speed_kt, aircraft.heading),
            (35000, 450, 271),
        )
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
            self.assertEqual(
                store.counts(), {"total": 1, "pending": 0, "uploaded": 1}
            )

    def test_receiver_matching_uses_serial_only_when_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, serial in (("one", "ABC123"), ("two", "XYZ789")):
                device = root / name
                device.mkdir()
                (device / "idVendor").write_text("0bda")
                (device / "idProduct").write_text("2838")
                (device / "serial").write_text(serial)
            with patch.object(sidecar, "Path", lambda _path: root):
                self.assertEqual(len(sidecar.matching_sdrs("0bda", "2838", "")), 2)
                selected = sidecar.matching_sdrs("0bda", "2838", "XYZ789")
                self.assertEqual([item.name for item in selected], ["two"])

    def test_healthcheck_is_headless(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "status.json").write_text(json.dumps({
                "written_at_epoch": int(time.time()),
                "dump1090_running": True,
                "sbs_connected": True,
                "web_running": False,
            }))
            self.assertEqual(sidecar.healthcheck(data_dir), 0)


class PackagingTests(unittest.TestCase):
    def test_public_package_has_no_web_ui_or_inbound_port(self):
        root = MODULE_PATH.parents[1]
        compose = (root / "compose.yaml").read_text()
        dockerfile = (root / "Dockerfile").read_text()
        source = MODULE_PATH.read_text()
        readme = (root / "README.md").read_text()
        self.assertNotIn("\n    ports:", compose)
        self.assertNotIn("WEB_PORT", compose)
        self.assertNotIn("dashboard.html", dockerfile)
        self.assertNotIn("ThreadingHTTPServer", source)
        self.assertNotIn("web_loop", source)
        self.assertNotIn("Royal City Recon", readme)
        self.assertFalse((root / "src" / "dashboard.html").exists())

    def test_setup_auto_detects_serial_and_protects_secret(self):
        root = MODULE_PATH.parents[1]
        setup = (root / "setup.sh").read_text()
        compose = (root / "compose.yaml").read_text()
        self.assertIn('EXPECTED_SERIAL="${SDR_SERIAL:-}"', setup)
        self.assertIn('candidate_serial="$(udevadm info', setup)
        self.assertNotIn("00000001", setup)
        self.assertIn('if [[ "$auth_ok" != "True" ]]', setup)
        self.assertIn("WDG API authentication has not succeeded", setup)
        self.assertIn('${WDGWARS_GID:?Run ./setup.sh to create the secret group}', compose)
        self.assertIn('secret_group="wdgwars-aircraft"', setup)
        self.assertIn('chgrp "$secret_group" .secrets/wdgwars_api_key', setup)
        self.assertIn('chmod 0640 .secrets/wdgwars_api_key', setup)

    def test_windows_package_is_native_headless_and_reproducible(self):
        root = MODULE_PATH.parents[1]
        windows = root / "windows"
        required = [
            "Start-WDG-Aircraft-Sidecar.cmd",
            "Configure-WDG-API-Key.cmd",
            "Status.cmd",
            "README-WINDOWS.txt",
            "THIRD-PARTY-NOTICES.txt",
            "Build-Windows-Package.ps1",
            "Test-Windows-Package.ps1",
            "dump1090-sbs-only.patch",
            "wdg-dump1090.cfg",
        ]
        for name in required:
            self.assertTrue((windows / name).is_file(), name)

        decoder_patch = (windows / "dump1090-sbs-only.patch").read_text()
        config = (windows / "wdg-dump1090.cfg").read_text()
        workflow = (root / ".github" / "workflows" / "ci-release.yml").read_text()
        start_script = (windows / "Start-WDG-Aircraft-Sidecar.cmd").read_text()
        self.assertIn("WDG_SBS_ONLY", decoder_patch)
        self.assertIn("127.0.0.1", decoder_patch)
        self.assertIn("/MT", decoder_patch)
        self.assertIn("aircrafts = NUL", config)
        self.assertIn("web-page = NUL", config)
        self.assertIn("windows-2022", workflow)
        self.assertIn("252cef736d24e146545aecf7f316c289ef82b3b4", workflow)
        self.assertNotIn("WDGWARS_API_KEY=", start_script)


if __name__ == "__main__":
    unittest.main()
