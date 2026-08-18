#!/bin/bash
# Extracts the Hi556 factory colour calibration from Dell's Windows camera
# driver and generates hi556.yaml for the libcamera Simple IPA pipeline.
#
# You must download the driver yourself from Dell's support site for your
# exact laptop model — it is not redistributed here (see README.md).
# Search Dell support for "Intel 2D Imaging/MCU/Visual Sensing Controller
# Driver for Camera" for your service tag / model.
#
# Usage:
#   ./tools/extract_calibration.sh /path/to/Intel-2D-Imaging-..._WIN64_....EXE
#
# Output: hi556.yaml in the repo root (gitignored, not committed).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

if [ $# -ne 1 ]; then
	echo "Usage: $0 /path/to/dell-camera-driver.EXE" >&2
	exit 1
fi

DRIVER_EXE="$1"
if [ ! -f "$DRIVER_EXE" ]; then
	echo "Error: file not found: $DRIVER_EXE" >&2
	exit 1
fi

if ! command -v 7z >/dev/null 2>&1; then
	echo "Error: 7z (p7zip) is required. Install with: sudo apt install p7zip-full" >&2
	exit 1
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Extracting driver package..."
7z x "$DRIVER_EXE" -o"$WORKDIR" -y >/dev/null

AIQB=$(find "$WORKDIR" -iname "HI556_*_ADL.aiqb" | sort | head -1)
if [ -z "$AIQB" ]; then
	echo "Error: no HI556_*_ADL.aiqb file found in the driver package." >&2
	echo "This script targets a Hi556 sensor; adjust the find pattern for other sensors." >&2
	exit 1
fi

echo "Found calibration file: $(basename "$AIQB")"
find "$WORKDIR" -iname "HI556_*_ADL.aiqb" -exec basename {} \; | sort

echo
echo "Parsing calibration data (all module variants should give identical" \
     "values; using the first one found)..."
python3 "$SCRIPT_DIR/parse_aiqb.py" "$AIQB" --sensor-name hi556_tmp

mv "hi556_tmp.yaml" "$REPO_ROOT/hi556.yaml"
# Add BlackLevel (auto-detect) as the parser doesn't emit one — see its own
# printed reminder above.
sed -i "s/^algorithms:$/algorithms:\n- BlackLevel:/" "$REPO_ROOT/hi556.yaml"
sed -i "1i # Calibrated from $(basename "$AIQB") (Dell Windows camera driver, extracted locally — not redistributed)" \
	"$REPO_ROOT/hi556.yaml"

echo
echo "Wrote $REPO_ROOT/hi556.yaml"
echo "Install it with:"
echo "  sudo cp hi556.yaml /usr/share/libcamera/ipa/simple/hi556.yaml"
