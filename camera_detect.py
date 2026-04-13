#!/usr/bin/env python3
"""
camera_detect.py — Auto-detect EMEET PIXY camera and PTZ control device.

The PIXY registers two v4l2 nodes on the Pi:
  - A video capture node  (e.g. /dev/video0 or /dev/video1)
  - A PTZ control node    (a second node associated with the same USB device)

USB index ordering is not stable across replug. This module finds both nodes
by parsing `v4l2-ctl --list-devices` output and probing each candidate node to
determine which one accepts pan_absolute (PTZ) vs. which one delivers frames
(capture).

Usage
-----
    # Drop-in for tracker_pi.py config block:
    from camera_detect import detect_pixy
    CAMERA_INDEX, PTZ_DEVICE = detect_pixy()

    # Or run standalone for diagnostics:
    python3 camera_detect.py

Returns
-------
detect_pixy() -> tuple[int, str]
    (camera_index, ptz_device_path)

    camera_index : int  — OpenCV VideoCapture index (e.g. 0 or 1)
    ptz_device   : str  — /dev/videoN path to use with v4l2-ctl PTZ commands

Raises
------
RuntimeError if EMEET PIXY is not found or nodes cannot be probed.
"""

import re
import subprocess
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIXY_NAME = "EMEET PIXY"          # substring to match in v4l2-ctl output
V4L2_CTL  = "v4l2-ctl"            # must be on PATH (v4l2-utils package)

# Pan absolute control name as reported by v4l2-ctl --list-ctrls-menus
PAN_CTRL  = "pan_absolute"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _list_devices() -> str:
    """Run v4l2-ctl --list-devices and return raw output.

    Raises RuntimeError on command failure.
    """
    try:
        result = subprocess.run(
            [V4L2_CTL, "--list-devices"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout
    except FileNotFoundError:
        raise RuntimeError(
            f"'{V4L2_CTL}' not found. Install with: sudo apt install v4l2-utils"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("v4l2-ctl --list-devices timed out")


def _parse_pixy_nodes(v4l2_output: str) -> list[str]:
    """Extract /dev/videoN paths belonging to the EMEET PIXY section.

    v4l2-ctl --list-devices output format:
        EMEET PIXY (usb-...):
                /dev/video0
                /dev/video1
                /dev/media0
        Other Camera (usb-...):
                /dev/video2
                ...

    Returns list of /dev/videoN paths (excludes /dev/media* nodes).
    """
    nodes: list[str] = []
    in_pixy_section = False

    for line in v4l2_output.splitlines():
        # Section header — camera name
        if line and not line.startswith("\t") and not line.startswith(" "):
            in_pixy_section = PIXY_NAME in line
            continue

        if in_pixy_section:
            stripped = line.strip()
            if not stripped:
                continue
            # Collect only video nodes (not media nodes)
            if re.match(r"^/dev/video\d+$", stripped):
                nodes.append(stripped)

    return nodes


def _has_ptz_controls(device_path: str) -> bool:
    """Return True if the device exposes pan_absolute (UVC PTZ control set).

    Runs: v4l2-ctl -d <device> --list-ctrls-menus
    and checks for 'pan_absolute' in the output.
    """
    try:
        result = subprocess.run(
            [V4L2_CTL, "-d", device_path, "--list-ctrls-menus"],
            capture_output=True, text=True, timeout=5
        )
        return PAN_CTRL in result.stdout
    except Exception:
        return False


def _device_path_to_index(device_path: str) -> Optional[int]:
    """Convert /dev/videoN to integer N.

    Returns None if the path doesn't match expected format.
    """
    m = re.match(r"^/dev/video(\d+)$", device_path)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_pixy(verbose: bool = False) -> tuple[int, str]:
    """Locate the EMEET PIXY camera index and PTZ control device.

    Algorithm:
    1. Run v4l2-ctl --list-devices to find all /dev/videoN nodes under PIXY.
    2. For each node, probe whether it has PTZ controls (pan_absolute).
       - Node WITH pan_absolute  → PTZ control device.
       - Node WITHOUT pan_absolute → video capture device.
    3. Convert the capture node path to an OpenCV integer index.

    Returns (camera_index, ptz_device_path).
    Raises RuntimeError if detection fails.
    """
    raw = _list_devices()

    if verbose:
        print("[camera_detect] v4l2-ctl --list-devices output:")
        print(raw)

    nodes = _parse_pixy_nodes(raw)

    if not nodes:
        raise RuntimeError(
            f"'{PIXY_NAME}' not found in v4l2-ctl --list-devices.\n"
            "Check USB connection and that the camera is powered on.\n\n"
            "Raw output:\n" + raw
        )

    if verbose:
        print(f"[camera_detect] PIXY nodes found: {nodes}")

    ptz_device: Optional[str] = None
    capture_device: Optional[str] = None

    for node in nodes:
        has_ptz = _has_ptz_controls(node)
        if verbose:
            label = "PTZ" if has_ptz else "capture"
            print(f"[camera_detect]   {node} → {label}")
        if has_ptz:
            # PIXY uses the same node for PTZ and capture
            if ptz_device is None:
                ptz_device = node
            if capture_device is None:
                capture_device = node
        else:
            # Secondary node (metadata only on PIXY) — skip for capture
            pass

    if capture_device is None:
        # PIXY uses the SAME node for both capture and PTZ.
        # Use the PTZ node as capture too (common for UVC PTZ cameras).
        if ptz_device is not None:
            capture_device = ptz_device
        elif len(nodes) >= 1:
            capture_device = nodes[0]
        else:
            raise RuntimeError(
                f"No usable PIXY nodes found."
            )

    if ptz_device is None:
        # No PTZ controls found on any node — unexpected but possible if
        # UVC extension units aren't loaded. Guess: second node is PTZ.
        if len(nodes) >= 2:
            ptz_device = nodes[1] if nodes[1] != capture_device else nodes[0]
        else:
            raise RuntimeError(
                "Only one PIXY node found and it has no PTZ controls. "
                "UVC extension units may not be loaded on this kernel.\n"
                "Try: sudo modprobe uvcvideo"
            )

    camera_index = _device_path_to_index(capture_device)
    if camera_index is None:
        raise RuntimeError(
            f"Could not parse integer index from capture device path: {capture_device}"
        )

    return camera_index, ptz_device


def detect_pixy_safe(
    fallback_index: int = 1,
    fallback_ptz: str = "/dev/video1",
    verbose: bool = True,
) -> tuple[int, str]:
    """detect_pixy() with a fallback on failure.

    Suitable for use at module import time in tracker_pi.py where a hard
    crash on startup is undesirable. Logs a warning and returns the fallback
    values if detection fails.

    Args:
        fallback_index: OpenCV index to use if auto-detect fails.
        fallback_ptz:   PTZ device path to use if auto-detect fails.
        verbose:        Print detection result (default True for visibility).

    Returns (camera_index, ptz_device_path).
    """
    try:
        idx, ptz = detect_pixy(verbose=verbose)
        if verbose:
            print(
                f"[camera_detect] PIXY detected: "
                f"capture=/dev/video{idx}  ptz={ptz}"
            )
        return idx, ptz
    except RuntimeError as e:
        print(
            f"[camera_detect] WARNING: Auto-detect failed — using fallbacks "
            f"(index={fallback_index}, ptz={fallback_ptz})\n"
            f"  Reason: {e}"
        )
        return fallback_index, fallback_ptz


# ---------------------------------------------------------------------------
# Integration snippet for tracker_pi.py
# ---------------------------------------------------------------------------
#
# Replace the hardcoded config block in tracker_pi.py:
#
#   # OLD (fragile):
#   CAMERA_INDEX = 1
#   # ... and in set_ptz():
#   subprocess.run(['v4l2-ctl', '-d', '/dev/video1', ...])
#
# With:
#
#   from camera_detect import detect_pixy_safe
#   CAMERA_INDEX, PTZ_DEVICE = detect_pixy_safe()
#
# Then in set_ptz():
#
#   subprocess.run(['v4l2-ctl', '-d', PTZ_DEVICE,
#                   f'--set-ctrl=pan_absolute={p}',
#                   f'--set-ctrl=tilt_absolute={t}'], ...)
#
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CLI entrypoint — run as a diagnostic tool
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Detect EMEET PIXY camera and PTZ control device",
        epilog="Run this on the Pi after plugging/unplugging the camera to "
               "verify correct node assignment."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print v4l2-ctl raw output and per-node probe results"
    )
    parser.add_argument(
        "--camera-name", default=PIXY_NAME,
        help=f"Camera name substring to search for (default: '{PIXY_NAME}')"
    )
    args = parser.parse_args()

    print(f"Searching for: '{args.camera_name}'")
    print()

    try:
        idx, ptz = detect_pixy(verbose=args.verbose)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print()
    print("=" * 40)
    print(f"  Capture device : /dev/video{idx}  (OpenCV index {idx})")
    print(f"  PTZ device     : {ptz}")
    print("=" * 40)
    print()
    print("tracker_pi.py config:")
    print(f"  CAMERA_INDEX = {idx}")
    print(f"  PTZ_DEVICE   = '{ptz}'")
    print()
    print("Or use auto-detect (add to top of tracker_pi.py):")
    print("  from camera_detect import detect_pixy_safe")
    print("  CAMERA_INDEX, PTZ_DEVICE = detect_pixy_safe()")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
