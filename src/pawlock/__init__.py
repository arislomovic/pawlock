"""PawLock: lock the keyboard while a cat walks on it."""
import argparse
import contextlib
import logging
import sys
import time
from pathlib import Path

from .core import Controller, load_config, log

CONFIG = "~/.pawlock/config.yaml"


def main() -> None:
    ap = argparse.ArgumentParser(prog="pawlock", description="Lock the keyboard while a cat walks on it.")
    ap.add_argument("--dry-run", action="store_true", help="detect and log, but never lock")
    ap.add_argument("--debug", action="store_true", help="log why each rule fired")
    args = ap.parse_args()
    if sys.platform == "darwin":
        from .macos import run
    elif sys.platform.startswith("linux"):
        from .linux import run
    elif sys.platform == "win32":
        from .windows import run
    else:
        sys.exit(f"PawLock has no backend for {sys.platform}.")
    try:
        cfg = load_config(CONFIG)
    except (ValueError, OSError) as err:  # yaml errors are ValueErrors
        fail(f"Can't load {CONFIG}: {err}")
    logfile = Path(cfg["log_file"]).expanduser()
    logfile.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(logfile)] + ([logging.StreamHandler()] if sys.stderr else [])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=handlers)
    log.info("START%s", " (dry run)" if args.dry_run else "")
    run(Controller(cfg, args.debug), cfg, args.dry_run)
    log.info("STOP")


def fail(msg):
    """Exit with msg. The packaged app has no console, so it also shows msg in a dialog."""
    if getattr(sys, "frozen", False):  # set by PyInstaller
        if sys.platform == "darwin":
            import subprocess
            subprocess.run(["osascript", "-e", "on run argv", "-e",
                            'display alert "PawLock" message (item 1 of argv) as critical', "-e",
                            "end run", msg], check=False)
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "PawLock", 0x10)  # MB_ICONERROR
    sys.exit(msg)


def tray(ctl, dry_run, lock=contextlib.nullcontext()):
    """Tray icon: Status / Unlock now / Pause 15 min / Quit. Caller runs icon.run()."""
    import pystray
    now = time.monotonic
    suffix = " (dry run)" if dry_run else ""

    def act(f):
        def action():
            with lock:
                f()
            icon.update_menu()
        return action

    icon = pystray.Icon("pawlock", paw_image(), "PawLock", pystray.Menu(
        pystray.MenuItem(lambda _: f"Status: {ctl.status(now())}{suffix}", None, enabled=False),
        pystray.MenuItem("Unlock now", act(lambda: ctl.unlock("tray"))),
        pystray.MenuItem("Pause 15 min", act(lambda: ctl.pause(15 * 60, now()))),
        pystray.MenuItem("Quit", lambda icon: icon.stop()),
    ))
    return icon


ICON = Path(__file__).with_name("paw_lock.png")  # 1024 px render of paw_lock.svg (see README)


def paw_image():
    from PIL import Image
    return Image.open(ICON).convert("RGBA")
