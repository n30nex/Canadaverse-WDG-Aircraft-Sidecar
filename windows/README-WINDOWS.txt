WDG AIRCRAFT SIDECAR FOR WINDOWS 10/11 (64-BIT)
================================================

This portable release needs no Docker and no separate Python installation.
It is receive-only and has no web interface or public network port.

QUICK START
-----------
1. Extract the entire ZIP to a normal folder.
2. Plug in one RTL2832U-compatible RTL-SDR and a 1090 MHz antenna.
3. Double-click Start-WDG-Aircraft-Sidecar.cmd.
4. On first launch, paste your 64-character WDG Wars API key. Input is hidden.
5. Leave the window open. It reports each batch confirmed by WDG Wars.

Double-click Status.cmd at any time to see received, pending, and confirmed
aircraft totals. Use Configure-WDG-API-Key.cmd to replace the saved key.

FIRST-TIME RTL-SDR DRIVER SETUP
-------------------------------
If the decoder cannot start, close other SDR programs first. If this RTL-SDR
has not been used with SDR software on Windows:

1. Download Zadig only from https://zadig.akeo.ie/
2. Open Zadig and choose Options > List All Devices.
3. Select the RTL-SDR "Bulk-In, Interface 0" device. Do not select a keyboard,
   mouse, or unrelated USB device.
4. Choose WinUSB and click Install Driver, then unplug and reconnect the SDR.
5. Run Start-WDG-Aircraft-Sidecar.cmd again.

WHAT SUCCESS LOOKS LIKE
-----------------------
- The main window says the decoder is running and the SBS feed connects.
- "messages" rises in Status.cmd when aircraft transmissions are received.
- "aircraft.pending" means a positioned aircraft awaits a confirmed response.
- "aircraft.uploaded" means WDG Wars confirmed the submission.
- The main window prints imported and already-seen counts after each batch.

Aircraft without a decoded position are heard but are not uploaded. Reception
depends on antenna, placement, line of sight, and nearby air traffic.

DATA AND PRIVACY
----------------
The key, local SQLite log, and status file are stored only for your Windows
account under:

  %LOCALAPPDATA%\Canadaverse\WDG-Aircraft-Sidecar

The decoder listens only on 127.0.0.1:30003 so other computers cannot connect.
The API key is never printed or included in this release.

ADVANCED: MULTIPLE SDRS
-----------------------
Device 0 is used by default. To select another device for one launch, open
Command Prompt in this folder and run:

  set SDR_DEVICE=1
  Start-WDG-Aircraft-Sidecar.cmd

Support: https://github.com/n30nex/Canadaverse-WDG-Aircraft-Sidecar/issues
