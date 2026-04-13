"""Merlin v2 — UVC PTZ Controller for EMEET PIXY.

Controls pan/tilt/zoom via libuvc (ctypes wrapper).
Falls back to uvc-util CLI if libuvc fails.

Usage:
    ptz = UVCPTZController()
    ptz.set_pantilt(30.0, -10.0)  # pan 30° right, tilt 10° down
    ptz.home()                     # return to center
    ptz.close()
"""

import ctypes
import ctypes.util
import logging
import platform
import subprocess
import time
from pathlib import Path

log = logging.getLogger("merlin.ptz")


# ============================================================
# LIBUVC CTYPES WRAPPER
# ============================================================

class _UVCContext(ctypes.Structure):
    pass

class _UVCDevice(ctypes.Structure):
    pass

class _UVCDeviceHandle(ctypes.Structure):
    pass


def _load_libuvc():
    """Load libuvc shared library. Returns None if not found."""
    search_paths = [
        # Local install (our build)
        str(Path.home() / ".local" / "lib" / "libuvc.dylib"),
        # Homebrew
        "/opt/homebrew/lib/libuvc.dylib",
        "/usr/local/lib/libuvc.dylib",
        # System
        "libuvc.dylib",
        "libuvc.so",
    ]

    for path in search_paths:
        try:
            lib = ctypes.cdll.LoadLibrary(path)
            log.info(f"libuvc loaded from {path}")
            return lib
        except OSError:
            continue

    # Try ctypes.util as last resort
    found = ctypes.util.find_library("uvc")
    if found:
        try:
            lib = ctypes.cdll.LoadLibrary(found)
            log.info(f"libuvc loaded from {found}")
            return lib
        except OSError:
            pass

    return None


def _setup_libuvc(lib):
    """Set up function prototypes for libuvc."""
    # uvc_init
    lib.uvc_init.argtypes = [
        ctypes.POINTER(ctypes.POINTER(_UVCContext)),
        ctypes.c_void_p
    ]
    lib.uvc_init.restype = ctypes.c_int

    # uvc_find_device
    lib.uvc_find_device.argtypes = [
        ctypes.POINTER(_UVCContext),
        ctypes.POINTER(ctypes.POINTER(_UVCDevice)),
        ctypes.c_int,    # vid
        ctypes.c_int,    # pid
        ctypes.c_char_p  # serial
    ]
    lib.uvc_find_device.restype = ctypes.c_int

    # uvc_open / uvc_close
    lib.uvc_open.argtypes = [
        ctypes.POINTER(_UVCDevice),
        ctypes.POINTER(ctypes.POINTER(_UVCDeviceHandle))
    ]
    lib.uvc_open.restype = ctypes.c_int

    lib.uvc_close.argtypes = [ctypes.POINTER(_UVCDeviceHandle)]
    lib.uvc_close.restype = None

    lib.uvc_unref_device.argtypes = [ctypes.POINTER(_UVCDevice)]
    lib.uvc_unref_device.restype = None

    lib.uvc_exit.argtypes = [ctypes.POINTER(_UVCContext)]
    lib.uvc_exit.restype = None

    # PTZ absolute
    lib.uvc_set_pantilt_abs.argtypes = [
        ctypes.POINTER(_UVCDeviceHandle),
        ctypes.c_int32,  # pan (arc-seconds)
        ctypes.c_int32   # tilt (arc-seconds)
    ]
    lib.uvc_set_pantilt_abs.restype = ctypes.c_int

    lib.uvc_get_pantilt_abs.argtypes = [
        ctypes.POINTER(_UVCDeviceHandle),
        ctypes.POINTER(ctypes.c_int32),  # pan
        ctypes.POINTER(ctypes.c_int32),  # tilt
        ctypes.c_uint8                   # req_code
    ]
    lib.uvc_get_pantilt_abs.restype = ctypes.c_int

    # Zoom
    lib.uvc_set_zoom_abs.argtypes = [
        ctypes.POINTER(_UVCDeviceHandle),
        ctypes.c_uint16
    ]
    lib.uvc_set_zoom_abs.restype = ctypes.c_int

    return lib


# ============================================================
# PTZ CONTROLLER
# ============================================================

class UVCPTZController:
    """Control PTZ on a UVC camera via libuvc.

    Falls back to uvc-util CLI if libuvc can't open the device
    (common on macOS due to camera entitlements).
    """

    def __init__(self, vid=0, pid=0):
        self._lib = None
        self._ctx = None
        self._dev = None
        self._devh = None
        self._using_cli = False
        self._cli_device = "0"

        # Try libuvc first
        lib = _load_libuvc()
        if lib:
            try:
                lib = _setup_libuvc(lib)
                self._lib = lib

                ctx = ctypes.POINTER(_UVCContext)()
                res = lib.uvc_init(ctypes.byref(ctx), None)
                if res < 0:
                    raise RuntimeError(f"uvc_init failed: {res}")
                self._ctx = ctx

                dev = ctypes.POINTER(_UVCDevice)()
                res = lib.uvc_find_device(ctx, ctypes.byref(dev), vid, pid, None)
                if res < 0:
                    raise RuntimeError(f"uvc_find_device failed: {res}")
                self._dev = dev

                devh = ctypes.POINTER(_UVCDeviceHandle)()
                res = lib.uvc_open(dev, ctypes.byref(devh))
                if res < 0:
                    raise RuntimeError(f"uvc_open failed: {res}")
                self._devh = devh

                log.info("PTZ: libuvc connected")
                return

            except Exception as e:
                log.warning(f"libuvc failed ({e}), trying CLI fallback")

        # Fallback to uvc-util CLI
        self._using_cli = True
        log.info("PTZ: using uvc-util CLI fallback")

    def set_pantilt(self, pan_deg, tilt_deg):
        """Set absolute pan/tilt in degrees."""
        pan_arcsec = int(pan_deg * 3600)
        tilt_arcsec = int(tilt_deg * 3600)

        if self._using_cli:
            self._cli_set_pantilt(pan_arcsec, tilt_arcsec)
        else:
            res = self._lib.uvc_set_pantilt_abs(self._devh, pan_arcsec, tilt_arcsec)
            if res < 0:
                log.error(f"uvc_set_pantilt_abs failed: {res}")

    def get_pantilt(self):
        """Get current pan/tilt in degrees. Returns (pan, tilt)."""
        if self._using_cli:
            return (0.0, 0.0)  # CLI doesn't support get easily

        pan = ctypes.c_int32()
        tilt = ctypes.c_int32()
        UVC_GET_CUR = 0x81
        res = self._lib.uvc_get_pantilt_abs(
            self._devh, ctypes.byref(pan), ctypes.byref(tilt), UVC_GET_CUR
        )
        if res < 0:
            log.error(f"uvc_get_pantilt_abs failed: {res}")
            return (0.0, 0.0)
        return (pan.value / 3600.0, tilt.value / 3600.0)

    def set_zoom(self, level):
        """Set absolute zoom level."""
        if self._using_cli:
            return  # Skip zoom for CLI
        res = self._lib.uvc_set_zoom_abs(self._devh, int(level))
        if res < 0:
            log.error(f"uvc_set_zoom_abs failed: {res}")

    def home(self):
        """Return to center position."""
        self.set_pantilt(0.0, 0.0)

    def _cli_set_pantilt(self, pan_arcsec, tilt_arcsec):
        """Fallback: use uvc-util CLI for PTZ."""
        uvc_paths = [
            str(Path.home() / ".local" / "bin" / "uvc-util"),
            "/usr/local/bin/uvc-util",
            "/opt/homebrew/bin/uvc-util",
            "uvc-util",
        ]
        uvc_bin = None
        for p in uvc_paths:
            try:
                subprocess.run([p, "--version"], capture_output=True, timeout=2)
                uvc_bin = p
                break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        if not uvc_bin:
            log.error("uvc-util not found — install from https://github.com/jtfrey/uvc-util")
            return

        try:
            subprocess.run(
                [uvc_bin, "-I", self._cli_device,
                 "-s", f"pan-tilt-abs={{{pan_arcsec}, {tilt_arcsec}}}"],
                capture_output=True, timeout=5, check=False
            )
        except Exception as e:
            log.error(f"CLI PTZ failed: {e}")

    def close(self):
        """Clean up libuvc resources."""
        if self._devh and self._lib:
            try:
                self._lib.uvc_close(self._devh)
                self._lib.uvc_unref_device(self._dev)
                self._lib.uvc_exit(self._ctx)
            except Exception:
                pass
        log.info("PTZ: closed")

    def __del__(self):
        self.close()


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="[ptz] %(message)s")

    print("Testing UVC PTZ Controller...")
    ptz = UVCPTZController()

    if ptz._using_cli:
        print("Using CLI fallback (libuvc couldn't open device)")
    else:
        print("Using libuvc direct control")

    print("Homing...")
    ptz.home()
    time.sleep(1)

    print("Pan right 30°...")
    ptz.set_pantilt(30.0, 0.0)
    time.sleep(1)

    print("Pan left 30°, tilt up 15°...")
    ptz.set_pantilt(-30.0, 15.0)
    time.sleep(1)

    print("Home...")
    ptz.home()
    time.sleep(1)

    pos = ptz.get_pantilt()
    print(f"Current position: pan={pos[0]:.1f}°, tilt={pos[1]:.1f}°")

    ptz.close()
    print("Done.")
