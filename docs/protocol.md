# H27P3 protocol notes

Investigation date: 2026-10-24. These notes describe the evidence behind the
allowlisted implementation, not a general protocol for other KTC monitors.

## Identity and transport

The tested display identifies itself as EDID manufacturer `SKG`, product
`10098` (`0x2772`), model `H27P3`. Its OSD was reported as `EU-1.0.1`; a vendor
string query returns the more precise spelling `EUR-1.0.1`, build `KSDA25040451`.
The app requires those strings. Bus and connector numbers are discovered rather
than hardcoded.

DDC/CI uses Linux I2C address `0x37`; EDID is read at `0x50`. A host frame is
`51 (80 | payload_length) payload checksum`, with the checksum computed by XOR
over preceding frame bytes starting at `6E`. A monitor reply starts with `6E`;
XOR over the complete reply starting at `50` must be zero. The normal null
response is `6E 80 BE`. Reads may retry this response after a transition.

Ordinary Get VCP payloads are `01 code`; Set VCP payloads are
`03 code value_hi value_lo`. A successful Get reply payload is
`02 00 code type max_hi max_lo current_hi current_lo`. The backend validates
the feature, length, checksum, reported maximum, and permitted current value.
Acknowledgment alone is not treated as proof of a successful write.

The Windows app's native backend uses DDC. Static inspection did not identify
a usable HID/WinUSB alternative. Names such as `setUsbUpData` also led to DDC
commands. The implemented controls do not need a separate USB connection.

## How the codes were identified

1. Compare EDID, timings, and raw Get VCP replies while manually changing the
   OSD mode. The probe queried all 256 feature numbers, including numbers
   ddcutil normally treats as write-only or tables. There were 85 successful
   replies; this did not imply 85 functional controls.
2. Statically unpack the official Electron-based Monitor Control Center 1.0.8.
   Its family-0 mapping and UI option lists supply labels and enumerations:
   `CROSSHAIR = 216` (`D8`), `FPS_DISPLAY = 228` (`E4`), `CLOCK = 175` (`AF`),
   `OVER_DIRVER = 224` (`E0`). Model-specific configuration can disable controls
   even when a generic mapping exists.
3. Inspect the official downloadable firmware's dispatch and value conversion
   handlers. Overdrive subtracts one, black equalizer divides by five, and low
   blue light divides by 25. These details determine valid wire values.
4. Read each exposed control on the installed monitor. Test selected writes
   with readback and restoration, and separately verify mode changes through
   EDID and the desktop's active timing.

No vendor executable, downloaded disassembler script, or firmware image was
executed. The downloadable image is **V1.0.2A / KSDA25060131**, which is not the
installed EU build. Static findings guide testing; they do not establish that
every code works on another revision. Firmware was not flashed or patched.

## Exposed VCP settings

All codes below are hexadecimal. The authoritative machine-readable ranges and
dependencies are in [`backend/control.py`](../backend/control.py).

| Control | Code | Allowed values |
| --- | --- | --- |
| Brightness | `10` | 0–100; dynamic contrast off |
| Contrast | `12` | 0–100 |
| Sharpness | `87` | 0–10 |
| Dynamic contrast | `F3` | 0 off, 1 on |
| HDR | `E9` | 0 off, 1 auto |
| Aspect ratio | `DB` | 0 full, 1 16:9, 2 4:3 |
| Crosshair | `D8` | 0 off, 1–6 styles |
| Refresh-rate counter | `E4` | 0 off, 1 on |
| Countdown timer | `AF` | 0 off; 1/2/3/4/5 = 15/30/45/60/90 minutes |
| Black equalizer | `2E` | 0–100 in steps of 5; neutral 50 |
| Overdrive | `E0` | 1 off, 2 standard, 3 advanced, 4 ultra fast, 5 auto |
| Adaptive Sync | `CF` | 0 off, 1 on |
| Color space | `F1` | 0 Native, 1 sRGB, 2 Adobe RGB, 3 DCI-P3, 4 BT.2020 |
| Gamma | `72` | 0 off; 1/2/3/4 = 1.8/2.0/2.2/2.4 |
| Color temperature | `14` | 1 normal, 2 warm, 3 cool, 4 user |
| Red / green / blue gain | `16` / `18` / `1A` | 0–100; User temperature |
| Low blue light | `FB` | 0–100 in steps of 25 |
| Headphone volume | `63` | 0–100 |
| Mute | `8D` | 0 off, 1 on |
| OSD horizontal / vertical position | `90` / `AD` | 0–100 |
| OSD timeout | `3F` | 15, 30, 45, 60 seconds |
| OSD transparency | `A7` | 0–5 |
| OSD color | `BE` | 0 orange, 1 blue, 2 red, 3 green |
| Power indicator | `EA` | 0 off, 1 on |
| Red hue / saturation | `9B` / `59` | 0–100; neutral 50 |
| Green hue / saturation | `9D` / `5B` | 0–100; neutral 50 |
| Blue hue / saturation | `9F` / `5D` | 0–100; neutral 50 |
| Cyan hue / saturation | `9E` / `5C` | 0–100; neutral 50 |
| Magenta hue / saturation | `A0` / `5E` | 0–100; neutral 50 |
| Yellow hue / saturation | `9C` / `5A` | 0–100; neutral 50 |

Gamma, temperature, gain, blue light, and six-axis adjustments require Native
color space in the app. `F1` and `BE` report a maximum of 5, but the exposed
enumerations are narrower. In particular, the inspected OSD-color handler
clamps to 3. The backend does not expose the additional reported values.

Crosshair, timer, and refresh counter can retain stale nonzero preferences
after another overlay replaces them. Enabling one first clears competing
selections, then reasserts the selected overlay even if its readback already
matches. Stored selection does not prove which overlay is visible.

All 39 controls read successfully. Live write/readback coverage includes
crosshair 0–6, counter on/off, timer 15-minute on/off, black equalizer 50→55→50,
brightness 30→31→30, overdrive 1→2→1, transparency 1→2→1, and OSD color 3→2→3.
Complete ranges and visible effects remain unverified. Six-axis writes were not
swept because the inspected handlers can copy changes across picture profiles.

## Hardware mode switching

The manual OSD transition changed timing-related VCP replies (`22`, `32`, `7C`,
`AC`, `AE`), but none identified a writable mode selector. Generic app code `E5`
did not switch this monitor. `FA` and `FF` were misleading: they echoed the low
byte of the preceding query rather than an independent mode state.

Comparing stored preferences located two mode bytes:

| Mode | EEPROM `009B` | EEPROM `00BE` | EDID serial descriptor |
| --- | --- | --- | --- |
| Work: 5120×2880 / 60 Hz | `01` | `00` | `0000000000000` |
| Game: 2560×1440 / 120 Hz | `02` | `01` | `0000000000001` |

These serial strings are firmware mode markers, not a unique device identifier.
EEPROM offsets are separate from VCP numbers: for example, VCP `BE` controls
OSD color while EEPROM offset `00BE` stores the hardware mode.

After identity/build checks and a durable snapshot of the first 256 preference
bytes, [`h27p3-mode.py`](../backend/h27p3-mode.py) uses this sequence:

| Vendor payload inside DDC framing | Purpose / expected reply payload |
| --- | --- |
| `03 7B 39 3B` | Read firmware string: `EUR-1.0.1` plus NUL |
| `03 7B 39 3A` | Read build string: `KSDA25040451` plus NUL |
| `C0 11 01` | Confirm factory access inactive: `C1 11 00` |
| `C0 11 03` | Temporarily enable preference writes: `C1 11 03` |
| `C0 02 01 00 9B vv` | Write companion mode byte: `C1 02 01` |
| `C0 02 01 00 BE vv` | Write mode byte: `C1 02 01` |
| `C0 09 10 00 90`, `C0 09 10 00 B0` | Read affected 16-byte blocks: `C1 09` plus data |
| `C0 11 00` | Clear factory access: `C1 11 00` |
| `CC FB` | Watchdog restart; normal null reply or interrupted response |

These are vendor payloads, not `ddcutil setvcp` arguments. The EEPROM-write
unlock temporarily sets active brightness to 60. Restart reloads the stored
brightness and mode. The inspected watchdog handler writes a timeout register;
it does not erase/program firmware. Ordinary VCP power-off was unsuitable
because its save path could overwrite the externally changed preferences.

The backend verifies the stored pair before restart, waits for the target EDID,
then compares preferences. Changes are allowed only at the two mode bytes and
the observed counter region `0089`–`008C`. Both live transition tests changed
exactly the two mode bytes, with brightness and the factory flag preserved.
The display returned after about five seconds; the full verified operation can
take about 30 seconds.

Requests for the already-active mode do not unlock, write preferences, or restart.
Before a restart is attempted, failures trigger an attempt to restore just the
two original mode bytes, clear factory access, and restart. After a restart has
been attempted, the code reports failed verification rather than replaying a
whole snapshot. No firmware recovery mechanism is provided.

## Unresolved features

Night Vision/Eagle Eye remain unmapped. Other product families reuse command
numbers, so their labels cannot be applied to H27P3 without evidence. Crosshair
color/position candidates `D7`, `3D`, `D5`, `D4`, and `D3` returned unsupported.
USB/KVM, language, input selection, and picture presets are not enabled in the
app. A successful Get reply alone does not establish valid Set values or actual
hardware support.

## Static-analysis references

The [official H27P3 download page](https://shop.ktcplay.com/download_center/firmware/2230)
supplied MCC 1.0.8 and the V1.0.2A firmware archive. Vendor files are not included
in this repository. The firmware's main body is raw DEFLATE at file offset
`0x33880`: 668,411 compressed bytes expand to 1,699,840 bytes, mapped at
`0x203800`. It uses AEON R2 variable-length instructions. A locally written
decoder read an [instruction-description table](https://github.com/niklas389/dough_es07d03_firmware/blob/main/tools/aeon/isa_r2.json)
as data.

Addresses below belong to that downloaded image, not a dump of the installed
firmware:

| Handler | Address |
| --- | --- |
| Set VCP dispatch | `28A684` |
| Crosshair / refresh counter / timer | `28DF3A` / `28DE8A` / `28DE4A` |
| Black equalizer / overdrive | `28AEB1` / `28B160` |
| OSD transparency / color | `28DB50` / `28D700` |
| Low blue light | `28E244` |
| Inferred OSD hardware-mode callback | `286926` |
| EDID-switch routine | `282023` |
| `CC FB` handler / watchdog helper | `28F693` / `28EE16` |

| Artifact | SHA-256 |
| --- | --- |
| H27P3 MCC 1.0.8 installer | `7b80a0b81fb7ca9ac4658281db5386de5f96a660e2163788bfe6dd08f1b3881a` |
| H27P3 firmware ZIP | `96ba33d9a85a41d5e7c7d909854f937f3d729256e12f5e398423071e8d84bf30` |
| `MERGE H27P3.bin` | `3476da89c2d45cdbc6f14ba8c9df7232874ecbf8f775845664744b3f5ad0c4e0` |
| Decompressed body | `6a0e22e46d51bfbb8700b274e2c7cc3f5b12eef3c60805091fff3c2cec83a00c` |
