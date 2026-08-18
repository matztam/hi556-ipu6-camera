# Hi556 / Intel IPU6 Camera on Linux

Gets the built-in Hi556 camera working on Linux laptops with an Intel
IPU6 image processor (Raptor Lake-P / Alder Lake generation — several
Dell and Lenovo models use this combo), including:

- a fix for the camera not being detected at all (firmware
  authentication failure)
- an on-demand privacy pipeline, so the sensor/camera LED is only active
  while an app is actually using the camera
- factory colour calibration, extracted from the vendor's Windows camera
  driver, to fix the washed-out/uncalibrated colours of the default
  Linux software ISP

Diagnosed and developed on a Dell Latitude 7440 (Debian GNU/Linux
sid/trixie, kernel 7.0.13+deb14-amd64); should generalize to similar
hardware, see [How this works and troubleshooting](#how-this-works-and-troubleshooting)
for what's device-specific vs. general.

## Contents

- [Is this for you?](#is-this-for-you)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [How this works and troubleshooting](#how-this-works-and-troubleshooting)
  - [Camera not detected at all: IPU6 firmware authentication failure](#camera-not-detected-at-all-ipu6-firmware-authentication-failure)
  - [Software stack: libcamera SoftISP instead of Intel HAL](#software-stack-libcamera-softisp-instead-of-intel-hal)
  - [On-demand privacy pipeline architecture](#on-demand-privacy-pipeline-architecture)
  - [Colour calibration details](#colour-calibration-details)
  - [Known limitations](#known-limitations)
  - [Useful debug commands](#useful-debug-commands)
- [License](#license)

## Is this for you?

Check whether your camera is affected before going further:

```bash
cam --list          # from libcamera-tools
media-ctl -d /dev/media0 -p
```

If `cam --list` reports something other than a working sensor (e.g. "No
sensor found for /dev/media0"), and `dmesg` shows an IPU6-related driver
for your camera (`intel_ipu6`, `intel_ipu6_isys`, `hi556` — check `dmesg
| grep -i ipu6`), this project is likely relevant. If `cam --list`
already shows your camera working but the colours look off, you
probably only need [colour calibration](#colour-calibration-details) —
that's Installation step 3 by itself, skip steps 1, 2, and 4 unless you
also want the on-demand privacy pipeline.

This targets the **Hi556** sensor specifically for the calibration step
(sections below and `tools/`); the firmware and privacy-pipeline parts
apply to any sensor behind an Intel IPU6 with the same symptoms.

## Requirements

`libcamera` needs a version recent enough to include SoftISP support for
IPU6 sensors — this was developed against `libcamera0.7`/`libcamera-ipa`
0.7.2 from Debian unstable; older packaged versions (e.g. from
stable/testing) may not have IPU6 SoftISP support yet.

| Package                     | Purpose                                    |
|------------------------------|---------------------------------------------|
| `libcamera0.7`               | Core libcamera library                      |
| `libcamera-ipa`               | Simple pipeline handler + SoftISP           |
| `libcamera-tools`             | `cam` CLI, useful for testing               |
| `libcamera-v4l2`              | V4L2 compatibility layer                    |
| `gstreamer1.0-libcamera`      | `libcamerasrc` GStreamer element            |
| `gstreamer1.0-plugins-base`   | `videotestsrc` (placeholder pipeline)       |
| `gstreamer1.0-plugins-good`   | `videocrop`, `v4l2sink`                     |
| `gstreamer1.0-tools`          | `gst-launch-1.0`, used by the watcher script |
| `v4l2loopback-dkms`           | Kernel module for the virtual camera device |
| `v4l2loopback-utils`          | `v4l2loopback-ctl` and friends              |
| `v4l-utils`                   | `v4l2-ctl`, `media-ctl` (debugging)         |
| `p7zip-full`                  | Unpacking the Dell driver installer (calibration step) |
| `python3-yaml`                | Used by `tools/parse_aiqb.py` (calibration step) |

```bash
sudo apt install libcamera0.7 libcamera-ipa libcamera-tools libcamera-v4l2 \
  gstreamer1.0-libcamera gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-tools v4l2loopback-dkms v4l2loopback-utils v4l-utils \
  p7zip-full python3-yaml
```

A kernel with working `intel_ipu6`, `intel_ipu6_isys`, `hi556`, and IVSC
(`mei_vsc`, `ivsc_csi`) drivers is required — these have been mainline
for a while (this was developed on 7.0.13); no out-of-tree
`ipu6-drivers` module is needed.

## Installation

1. **Fix camera detection, if needed.** If `cam --list` doesn't detect
   the sensor, see
   [Camera not detected at all](#camera-not-detected-at-all-ipu6-firmware-authentication-failure)
   below — on affected Dell hardware, a BIOS update resolved this.
   Confirm with `cam --list` before continuing.

2. **Configure the virtual camera device:**

   ```bash
   sudo cp etc-modprobe.d-v4l2loopback.conf /etc/modprobe.d/v4l2loopback.conf
   echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf
   sudo modprobe -r v4l2loopback  # ok if this errors — fine if the module wasn't loaded yet
   sudo modprobe v4l2loopback
   ```

3. **Install colour tuning.** Either the real factory calibration
   (recommended, see [Colour calibration details](#colour-calibration-details)
   for how to obtain it) or the hand-estimated fallback:

   ```bash
   # Real calibration, once you have hi556.yaml (see "Colour calibration
   # details" above for how to generate it):
   sudo cp hi556.yaml /usr/share/libcamera/ipa/simple/hi556.yaml
   # ...or the fallback, no extra steps needed:
   sudo cp hi556.default.yaml /usr/share/libcamera/ipa/simple/hi556.yaml
   ```

4. **Install the watcher script and systemd service:**

   ```bash
   mkdir -p ~/.local/bin
   cp libcamera-loopback-watch.sh ~/.local/bin/
   chmod +x ~/.local/bin/libcamera-loopback-watch.sh
   mkdir -p ~/.config/systemd/user
   cp systemd/libcamera-loopback.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now libcamera-loopback.service
   ```

## Usage

Select **"libcamera-cam"** as the camera in your video app (Webex, Zoom,
Chrome, Cheese, …). The camera sensor and its LED stay off until an app
actually opens the device; a black placeholder frame is shown until then
so apps that only check for a camera once at startup still find one. See
[On-demand privacy pipeline architecture](#on-demand-privacy-pipeline-architecture)
for the mechanism.

## How this works and troubleshooting

### Camera not detected at all: IPU6 firmware authentication failure

If `libcamera` (`cam --list`) reports **"No sensor found for
/dev/media0"** and `media-ctl -d /dev/media0 -p` shows only IPU6
CSI2/ISYS capture entities but no sensor entity, check `dmesg` for:

```
intel-ipu6 0000:00:05.0: IPU6 in secure mode
intel-ipu6 0000:00:05.0: Sending BOOT_LOAD to CSE
vsc-tp spi-INTC1009:00: wakeup firmware failed ret: -110
intel-ipu6 0000:00:05.0: Unexpected magic number 0x15
intel-ipu6 0000:00:05.0: FW authentication failed(-110)
```

The IPU6 driver loads its firmware through the Intel CSE (Converged
Security Engine / Management Engine) in a secure handshake. This
handshake can fail because **IVSC** (Intel Visual Sensing Controller —
the chip that on modern business laptops drives the camera privacy
shutter/presence detection and owns the CSI-2 link by default) itself
fails a GPIO/timing race during its own firmware wakeup (`vsc-tp ...
wakeup firmware failed ret: -110`). Without a successful IVSC bring-up,
the IPU6 CSE authentication never completes — the camera hardware stays
completely inactive regardless of any software configuration.

This is a known, but not always well-triaged, class of IVSC timing
issue (related bug reports: RH Bugzilla #2316918, #2324683, GitHub
`intel/ipu6-drivers` #306, #391, #171, #407 — check whether any matches
your exact symptom before assuming the fix below applies).

**Fix that resolved this on a Dell Latitude 7440:** a full BIOS update
to the latest available version, via `fwupd`/`fwupdmgr` (usually
preinstalled on modern distros; check your vendor's site for a bootable
BIOS update image if `fwupd` doesn't support your model):

```bash
sudo fwupdmgr refresh --force
sudo fwupdmgr update
```

...followed by a reboot. Success looks like this in `dmesg`:

```
intel-ipu6 0000:00:05.0: Sending BOOT_LOAD to CSE
intel-ipu6 0000:00:05.0: Sending AUTHENTICATE_RUN to CSE
intel-ipu6 0000:00:05.0: CSE authenticate_run done
intel-ipu6 0000:00:05.0: Found supported sensor INT3537:00
intel-ipu6 0000:00:05.0: Connected 1 cameras
```

**If a BIOS update doesn't help**, other things worth trying, based on
the linked bug reports: a full cold boot (not a warm reboot/suspend —
pull the power, wait 30s), or reloading the driver modules in order
(`mei_vsc_hw` → `mei_vsc` → `intel_ipu6` → `intel_ipu6_isys` →
`ivsc_csi`).

**Unlikely to help**, per the same bug reports: Secure Boot, TPM/PTT
settings, alternative firmware blob sources — no documented connection
to this failure mode.

### Software stack: libcamera SoftISP instead of Intel HAL

Intel's proprietary camera stack (`ipu6-camera-hal`, `icamerasrc`) isn't
packaged for Debian and would require a source build with several
pitfalls. This project uses the regular `libcamera` package's **Simple
pipeline handler + SoftISP** instead — works natively with Hi556, no
proprietary blobs needed, no custom build required once `libcamera` is
recent enough (see [Requirements](#requirements)).

Image-quality trade-off vs. Intel's proprietary ISP: no lens-shading
correction, simpler debayer/AWB/AGC. Good enough for video calls.

### On-demand privacy pipeline architecture

**Goal:** the sensor/camera LED should only be active while an app is
actually using the camera — not run continuously in the background.

```
                    ┌─────────────────────┐
   Idle:            │  videotestsrc black  │──▶ /dev/video42
                    └─────────────────────┘

   While actively in use (Webex/Zoom/etc. holds /dev/video42 open):

                    ┌──────────────────────────────────┐
                    │ libcamerasrc → videocrop → scale  │──▶ /dev/video42
                    │ (real Hi556 feed via IPU6)         │
                    └──────────────────────────────────┘
```

A watcher script (`libcamera-loopback-watch.sh`, runs as a systemd user
service) polls every 0.3s via `fuser` whether an *external*
process (not its own producer) holds `/dev/video42` open, and switches
between the placeholder and the real camera feed accordingly (with a
~3s grace period to avoid flapping on short gaps).

**Why a placeholder is needed:** Some apps (e.g. Webex) enumerate
available cameras only once at startup and discard devices that don't
provide a valid video format at that moment. Without a continuous
placeholder, the camera wouldn't show up in the app's device list at
all.

**Why `exclusive_caps=0` is needed for the loopback device:** With
`exclusive_caps=1` (the default), the loopback device refuses any
consumer open as long as no producer is actively writing — this creates
a chicken-and-egg deadlock with the trigger model above. With
`exclusive_caps=0`, the device reports valid formats even while idle.

The loopback device is configured in
[`etc-modprobe.d-v4l2loopback.conf`](etc-modprobe.d-v4l2loopback.conf)
as `/dev/video42`, named `libcamera-cam`. Both the device number and
name are arbitrary — adjust the config file and `DEVICE=` in
`libcamera-loopback-watch.sh` together if you want something different.

### Colour calibration details

Without calibration data for the specific Hi556 sensor, the SoftISP
pipeline only has generic "uncalibrated" values (strong green/cyan
cast, undersaturated colours — red in particular can look
orange/washed out).

**Solution:** Intel's proprietary IPU6 camera stack stores factory
colour calibration data as binary `.aiqb` files, typically keyed to the
specific camera module part number. These files ship as part of the
laptop vendor's official Windows camera driver.

**Neither the vendor's `.aiqb` calibration file nor a `hi556.yaml`
generated from it are part of this repo.** Both are derived from
proprietary binary data (subject to the vendor's/Intel's licensing
terms — e.g. Dell's package includes an "Intel Limited Distribution
(Commercial Use)" PDF) and are not redistributed here. Instead, there's
a script that automates the extraction for anyone who owns the driver
themselves:

1. Download your laptop vendor's camera driver for Windows — for Dell,
   this is "Intel 2D Imaging/MCU/Visual Sensing Controller Driver for
   Camera", from the official support site for your exact model
   (support site → drivers & downloads → enter service tag).
2. Run the extraction script (Dell-specific as written — it expects a
   Dell DUP installer; on Lenovo/other vendors you'll need to unpack
   the driver package yourself, e.g. with `7z x` or `innoextract`, and
   run `tools/parse_aiqb.py` directly against the resulting
   `HI556_*.aiqb` file):

   ```bash
   ./tools/extract_calibration.sh /path/to/Intel-2D-Imaging-..._WIN64_....EXE
   ```

   The script (`tools/extract_calibration.sh`) unpacks the Dell DUP
   wrapper with `7z`, automatically locates the matching
   `HI556_<module-id>_ADL.aiqb`, and runs
   [`tools/parse_aiqb.py`](tools/parse_aiqb.py) against it. That parser
   script comes from a not-yet-merged libcamera patch series by Javier
   Tia ([Patchwork #26761](https://patchwork.libcamera.org/patch/26761/),
   reviewed by Hans de Goede/Stefan Klug), and converts the binary AIQB
   structure (format reverse-engineered from `ipu6-camera-hal` headers)
   into a libcamera Simple IPA YAML — multi-stage colour correction
   matrices and AWB gains across several colour temperatures.

   On a Dell Latitude 7440, the driver contained four different Hi556
   module variants (`1BG502T3`, `1BG502TG`, `1BG508T3`, `CJFLE25`) —
   **all with identical colour calibration values**, so the specific
   module ID turned out to be irrelevant there. The script picks the
   first one it finds automatically; if your driver has different
   variants with genuinely different values, you may need to identify
   your exact module ID and adjust the script's file selection.

3. The result ends up as `hi556.yaml` in the repo root (excluded via
   `.gitignore`, never committed). Install it as shown in
   [Installation](#installation).

If you don't have access to the vendor driver,
[`hi556.default.yaml`](hi556.default.yaml) is a hand-estimated fallback
(derived from photo comparisons, not a real calibration) — usable for
video calls, inaccurate for saturated colours.

### Known limitations

- No autofocus/advanced AE algorithms like Intel's proprietary ISP has
  (SoftISP is deliberately simple)
- `videocrop` values in `libcamera-loopback-watch.sh`
  (256/256/160/160, assuming a 1280×720 delivery size) are hardcoded to
  a moderate zoom level (~20% tighter framing); adjust if needed
- Trigger detection is based on `fuser` polling (no native
  v4l2loopback open event); apps that open the device for only
  milliseconds (e.g. `ffmpeg -frames:v 1`) can be missed by the
  watcher. Real video-call apps with preview/retry behaviour (Webex
  etc.) trigger reliably.
- `videotestsrc` needs `is-live=true` in the placeholder pipeline —
  without it, `sync=false` on the sink lets the source push frames as
  fast as possible instead of respecting the declared framerate,
  pegging a CPU core even while idle. Already fixed in
  `libcamera-loopback-watch.sh`; worth knowing if you adapt the
  pipeline.

### Useful debug commands

```bash
# Check camera detection
cam --list

# Inspect the media graph
media-ctl -d /dev/media0 -p

# Watcher status
systemctl --user status libcamera-loopback.service
journalctl --user -u libcamera-loopback.service -f

# Who currently holds the loopback device open?
fuser -v /dev/video42

# Grab a single test frame
ffmpeg -f v4l2 -i /dev/video42 -frames:v 1 -update 1 test.png

# Check IPU6/IVSC firmware status after boot
sudo dmesg | grep -i -E "ipu6|ivsc|vsc-tp|hi556"
```

## License

GPL-2.0-or-later, see [`LICENSE`](LICENSE) — matching the license of
the included [`tools/parse_aiqb.py`](tools/parse_aiqb.py). Excluded
from this is the vendor's `.aiqb` binary file itself, which is not part
of this repo (see [Colour calibration details](#colour-calibration-details)
above).
