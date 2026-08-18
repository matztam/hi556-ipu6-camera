#!/bin/bash
# Keeps /dev/video42 fed with a black placeholder frame at all times (so
# apps that enumerate cameras only at startup, like Webex, always see a
# valid device), and swaps in the real hi556 camera feed only while an
# app is actually holding the device open (i.e. in an active call).
# Swaps back to the placeholder shortly after nothing holds it anymore.
#
# fuser can't distinguish reader from writer on a V4L2 node (both show up
# with the same access flags), so instead we exclude our own producer's
# PID from the fuser result: anything left over is a real external app.

DEVICE=/dev/video42
MODE=""    # "" | "black" | "camera"
PIPELINE_PID=""
IDLE_COUNT=0
IDLE_GRACE=10 # ~3s at 0.3s poll interval before falling back to the placeholder

BLACK_CMD=(gst-launch-1.0 videotestsrc pattern=black is-live=true ! video/x-raw,width=1280,height=720,framerate=15/1 ! videoconvert ! video/x-raw,format=YUY2 ! v4l2sink device="$DEVICE" sync=false)
CAMERA_CMD=(gst-launch-1.0 libcamerasrc ! videocrop left=256 right=256 top=160 bottom=160 ! videoscale ! video/x-raw,width=1280,height=720 ! videoconvert ! video/x-raw,format=YUY2 ! v4l2sink device="$DEVICE" sync=false)

stop_pipeline() {
	if [ -n "$PIPELINE_PID" ]; then
		kill "$PIPELINE_PID" 2>/dev/null
		wait "$PIPELINE_PID" 2>/dev/null
		PIPELINE_PID=""
	fi
}

switch_to() {
	local target="$1"
	[ "$MODE" = "$target" ] && return
	stop_pipeline
	if [ "$target" = "black" ]; then
		"${BLACK_CMD[@]}" &
	else
		"${CAMERA_CMD[@]}" &
	fi
	PIPELINE_PID=$!
	MODE="$target"
}

has_external_opener() {
	local pid
	for pid in $(fuser "$DEVICE" 2>/dev/null); do
		if [ "$pid" != "$PIPELINE_PID" ]; then
			return 0
		fi
	done
	return 1
}

cleanup() {
	stop_pipeline
	exit 0
}
trap cleanup TERM INT

switch_to black

while true; do
	if has_external_opener; then
		IDLE_COUNT=0
		switch_to camera
	elif [ "$MODE" = "camera" ]; then
		IDLE_COUNT=$((IDLE_COUNT + 1))
		if [ "$IDLE_COUNT" -ge "$IDLE_GRACE" ]; then
			switch_to black
			IDLE_COUNT=0
		fi
	fi
	sleep 0.3
done
