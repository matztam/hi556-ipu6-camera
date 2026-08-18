# Hi556 / Intel IPU6 Camera on Linux (Dell Latitude 7440)

Documentation of how the built-in camera on this laptop (Dell Latitude 7440,
Raptor Lake-P, Intel IPU6 image processor, Hi556 sensor) was brought up on
Debian Linux — including the firmware fix, an on-demand privacy pipeline,
and colour calibration extracted from the Windows driver.

System: Debian GNU/Linux sid/trixie, kernel 7.0.13+deb14-amd64.

## Contents

- [Requirements](#requirements)
- [1. Initial problem](#1-initial-problem)
- [2. Root cause: IPU6 firmware authentication failure](#2-root-cause-ipu6-firmware-authentication-failure)
- [3. Fix: Dell BIOS update](#3-fix-dell-bios-update)
- [4. Software stack: libcamera SoftISP instead of Intel HAL](#4-software-stack-libcamera-softisp-instead-of-intel-hal)
- [5. On-demand privacy pipeline (v4l2loopback)](#5-on-demand-privacy-pipeline-v4l2loopback)
- [6. Colour calibration from the Dell Windows driver](#6-colour-calibration-from-the-dell-windows-driver)
- [7. Installation / reproduction](#7-installation--reproduction)
- [8. Known limitations](#8-known-limitations)
- [9. Useful debug commands](#9-useful-debug-commands)

## Requirements

Package versions actually used and verified on this system (Debian
sid/trixie). Older versions may or may not work — in particular,
`libcamera` needs a version recent enough to include SoftISP support for
IPU6 sensors (available since roughly 0.3.x; 0.7.x is what's confirmed
here).

| Package                      | Minimum version tested | Purpose                                    |
|-------------------------------|------------------------|---------------------------------------------|
| `libcamera0.7`                | 0.7.2-1                | Core libcamera library                      |
| `libcamera-ipa`                | 0.7.2-1                | Simple pipeline handler + SoftISP           |
| `libcamera-tools`              | 0.7.2-1                | `cam` CLI, useful for testing               |
| `libcamera-v4l2`               | 0.7.2-1                | V4L2 compatibility layer                    |
| `gstreamer1.0-libcamera`       | 0.7.2-1                | `libcamerasrc` GStreamer element            |
| `gstreamer1.0-plugins-base`    | 1.28.6-2                | `videotestsrc` (placeholder pipeline)       |
| `gstreamer1.0-plugins-good`    | 1.28.4-1                | `videocrop`, `v4l2sink`                     |
| `v4l2loopback-dkms`            | 0.15.4-1                | Kernel module for the virtual camera device |
| `v4l2loopback-utils`           | 0.15.4-1                | `v4l2loopback-ctl` and friends              |
| `v4l-utils`                    | 1.32.0-5                | `v4l2-ctl`, `media-ctl` (debugging)         |
| `p7zip-full`                   | 16.02+transitional.1    | Unpacking the Dell driver installer         |
| `python3-yaml`                 | 6.0.3-1                 | Used by `tools/parse_aiqb.py`               |
| `fwupd`                        | any recent              | BIOS update via `fwupdmgr` (section 3)      |

Install everything except `fwupd` (usually already present) in one go:

```bash
sudo apt install libcamera0.7 libcamera-ipa libcamera-tools libcamera-v4l2 \
  gstreamer1.0-libcamera gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  v4l2loopback-dkms v4l2loopback-utils v4l-utils p7zip-full python3-yaml
```

A kernel with working `intel_ipu6`, `intel_ipu6_isys`, `hi556`, and IVSC
(`mei_vsc`, `ivsc_csi`) drivers is required — these are mainline as of a
reasonably recent kernel (verified here on 7.0.13); no out-of-tree
`ipu6-drivers` module is needed.

---

## 1. Initial problem

The built-in camera did show up as `/dev/video0`–`/dev/video32` under the
name `ipu6` (kernel drivers `intel_ipu6`, `intel_ipu6_isys`, `hi556` were
all loaded), but:

- `libcamera` (`cam --list`) reported **"No sensor found for /dev/media0"**
- `media-ctl -d /dev/media0 -p` only showed IPU6 CSI2/ISYS capture entities,
  **no** `hi556` sensor entity in the media graph

## 2. Root cause: IPU6 firmware authentication failure

`dmesg` showed:

```
intel-ipu6 0000:00:05.0: IPU6 in secure mode
intel-ipu6 0000:00:05.0: Sending BOOT_LOAD to CSE
vsc-tp spi-INTC1009:00: wakeup firmware failed ret: -110
intel-ipu6 0000:00:05.0: Unexpected magic number 0x15
intel-ipu6 0000:00:05.0: FW authentication failed(-110)
```

The IPU6 driver loads its firmware through the Intel CSE (Converged
Security Engine / Management Engine) in a secure handshake. That handshake
failed because **IVSC** (Intel Visual Sensing Controller — the chip that on
modern business laptops drives the camera privacy shutter/presence
detection and owns the CSI-2 link by default) itself failed a GPIO/timing
race during its own firmware wakeup (`vsc-tp ... wakeup firmware failed
ret: -110`). Without a successful IVSC bring-up, the IPU6 CSE
authentication never completes — the camera hardware stays completely
inactive regardless of any software configuration.

This is a known, but for this exact symptom (Raptor Lake-P + Hi556 +
`INTC1009`) not publicly triaged, IVSC timing issue (related but not
identical bugs: RH Bugzilla #2316918, #2324683, GitHub
`intel/ipu6-drivers` #306, #391, #171, #407).

## 3. Fix: Dell BIOS update

The BIOS was 10 versions out of date (1.16.0 instead of 1.32.0, May 2026).
After a full BIOS update via `fwupdmgr`:

```
sudo fwupdmgr refresh --force
sudo fwupdmgr update
```

...and a subsequent reboot, the camera came up cleanly:

```
intel-ipu6 0000:00:05.0: Sending BOOT_LOAD to CSE
intel-ipu6 0000:00:05.0: Sending AUTHENTICATE_RUN to CSE
intel-ipu6 0000:00:05.0: CSE authenticate_run done
intel-ipu6 0000:00:05.0: Found supported sensor INT3537:00
intel-ipu6 0000:00:05.0: Connected 1 cameras
```

**If the BIOS update doesn't help**, research suggested these next steps:
a full cold boot (not a warm reboot/suspend — pull the power, wait 30s), or
reloading the driver modules in order
(`mei_vsc_hw` → `mei_vsc` → `intel_ipu6` → `intel_ipu6_isys` → `ivsc_csi`).

**Not useful** per research: Secure Boot, TPM/PTT settings, alternative
firmware blob sources — no documented connection to this failure mode.

## 4. Software stack: libcamera SoftISP instead of Intel HAL

Intel's proprietary camera stack (`ipu6-camera-hal`, `icamerasrc`) isn't
packaged for Debian and would require a source build with several
pitfalls. Instead: Debian's regular `libcamera` package
(`libcamera0.7`/`libcamera-ipa` ≥ 0.7.2 from unstable) with the **Simple
pipeline handler + SoftISP** — works natively with Hi556, no proprietary
blobs needed.

```
sudo apt install --only-upgrade libcamera0.7 libcamera-ipa libcamera-tools \
  libcamera-v4l2 gstreamer1.0-libcamera libspa-0.2-libcamera
```

After the BIOS fix (section 3), the regular Debian package already
detects the camera correctly (`cam --list` shows `hi556`) — no custom
libcamera build needed.

Image-quality trade-off vs. Intel's proprietary ISP: no lens-shading
correction, simpler debayer/AWB/AGC. Good enough for video calls.

## 5. On-demand privacy pipeline (v4l2loopback)

**Goal:** the sensor/camera LED should only be active while an app is
actually using the camera — not run continuously in the background.

### Architecture

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
service) polls every 0.3s via `fuser`/`lsof` whether an *external* process
(not its own producer) holds `/dev/video42` open, and switches between the
placeholder and the real camera feed accordingly (with a ~3s grace period
to avoid flapping on short gaps).

**Why a placeholder is needed:** Some apps (e.g. Webex) enumerate
available cameras only once at startup and discard devices that don't
provide a valid video format at that moment. Without a continuous
placeholder, the camera wouldn't show up in the app's device list at all.

**Why `exclusive_caps=0` is needed for `video42`:** With `exclusive_caps=1`
(the default), the loopback device refuses any consumer open as long as
no producer is actively writing — this creates a chicken-and-egg deadlock
with the trigger model above. With `exclusive_caps=0`, the device reports
valid formats even while idle.

### Loopback device

A `v4l2loopback` device named `libcamera-cam` is configured on
`/dev/video42` with `exclusive_caps=0`
(`/etc/modprobe.d/v4l2loopback.conf`, see
[`etc-modprobe.d-v4l2loopback.conf`](etc-modprobe.d-v4l2loopback.conf)).
`video_nr=42` and the device path are arbitrary — adjust both the config
file and `DEVICE=` in `libcamera-loopback-watch.sh` if you'd rather use a
different number.

Select **"libcamera-cam"** as the camera in video apps (Webex, Zoom,
Chrome, Cheese, …).

## 6. Colour calibration from the Dell Windows driver

Without calibration data for the specific Hi556 sensor, the SoftISP
pipeline only has generic "uncalibrated" values (strong green/cyan cast,
undersaturated colours — red in particular looked orange/washed out).

**Solution:** Intel's proprietary IPU6 camera stack stores factory colour
calibration data as binary `.aiqb` files, typically keyed to the specific
camera module part number. These files ship as part of Dell's official
Windows camera driver for this laptop model.

### Procedure (everyone needs to do this themselves)

**Important:** Neither the Dell/Intel `.aiqb` calibration file nor a
`hi556.yaml` generated from it are part of this repo. Both are subject to
Intel's/Dell's licensing terms ("Intel Limited Distribution (Commercial
Use)", included as a PDF in the driver package) and are not redistributed
here. Instead there's a script that automates the extraction for anyone
who owns the driver themselves.

1. Download the Dell driver "Intel 2D Imaging/MCU/Visual Sensing
   Controller Driver for Camera" from the official Dell support site for
   your own, exact laptop model (support site → drivers & downloads →
   enter service tag).
2. Run the extraction script:

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

   In this case, the Dell driver contained four different Hi556 module
   variants (`1BG502T3`, `1BG502TG`, `1BG508T3`, `CJFLE25`) — **all with
   identical colour calibration values**, so the specific module ID
   turned out to be irrelevant for colour correction. The script picks
   the first one it finds automatically.

3. The result ends up as `hi556.yaml` in the repo root (excluded via
   `.gitignore`, never committed). Install it with:

   ```bash
   sudo cp hi556.yaml /usr/share/libcamera/ipa/simple/hi556.yaml
   ```

### Fallback without your own driver download

If you don't (yet) have access to the Dell driver,
[`hi556.default.yaml`](hi556.default.yaml) is included in the repo — a
rough hand-estimated stopgap (derived from photo comparisons, not a real
calibration). Works for video calls, but is inaccurate especially for
saturated colours. Installation is identical, just a different source
file:

```bash
sudo cp hi556.default.yaml /usr/share/libcamera/ipa/simple/hi556.yaml
```

## 7. Installation / reproduction

On a freshly set-up system on this laptop (or an identical Dell
Latitude 7440):

```bash
# 1. Keep the BIOS current (section 3)
sudo fwupdmgr refresh --force && sudo fwupdmgr update

# 2. Install/update required packages (see Requirements above)
sudo apt install libcamera0.7 libcamera-ipa libcamera-tools libcamera-v4l2 \
  gstreamer1.0-libcamera gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  v4l2loopback-dkms v4l2loopback-utils v4l-utils p7zip-full python3-yaml

# 3. Configure v4l2loopback
sudo cp etc-modprobe.d-v4l2loopback.conf /etc/modprobe.d/v4l2loopback.conf
echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf
sudo modprobe -r v4l2loopback && sudo modprobe v4l2loopback

# 4. Install colour tuning
# Either generate the real calibration (see section 6):
#   ./tools/extract_calibration.sh /path/to/dell-driver.EXE
#   sudo cp hi556.yaml /usr/share/libcamera/ipa/simple/hi556.yaml
# ...or use the fallback without a driver download:
sudo cp hi556.default.yaml /usr/share/libcamera/ipa/simple/hi556.yaml

# 5. Install the watcher script + systemd service
mkdir -p ~/.local/bin
cp libcamera-loopback-watch.sh ~/.local/bin/
chmod +x ~/.local/bin/libcamera-loopback-watch.sh
mkdir -p ~/.config/systemd/user
cp systemd/libcamera-loopback.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now libcamera-loopback.service
```

Then select **"libcamera-cam"** as the camera in your video app.

## 8. Known limitations

- No autofocus/advanced AE algorithms like Intel's proprietary ISP has
  (SoftISP is deliberately simple)
- `videocrop` values (256/256/160/160, assuming a 1280×720 delivery size)
  are hardcoded to a moderate zoom level (~20% tighter framing); adjust
  in `libcamera-loopback-watch.sh` if needed
- Trigger detection is based on `fuser`/`lsof` polling (no native
  v4l2loopback open event); apps that open the device for only
  milliseconds (e.g. `ffmpeg -frames:v 1`) can be missed by the watcher.
  Real video-call apps with preview/retry behaviour (Webex etc.) trigger
  reliably.
- `videotestsrc` needs `is-live=true` in the placeholder pipeline —
  without it, `sync=false` on the sink lets the source push frames as
  fast as possible instead of respecting the declared framerate, pegging
  a CPU core even while idle. Already fixed in
  `libcamera-loopback-watch.sh`; worth knowing if you adapt the pipeline.

## License

GPL-2.0-or-later, see [`LICENSE`](LICENSE) — matching the license of the
included [`tools/parse_aiqb.py`](tools/parse_aiqb.py). Excluded from this
is the Dell/Intel `.aiqb` binary file itself, which is not part of this
repo (see [section 6](#6-colour-calibration-from-the-dell-windows-driver)
above).

## 9. Useful debug commands

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
