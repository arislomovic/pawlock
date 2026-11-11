"""Linux backend: grab every keyboard with evdev, pass keys on through a uinput keyboard.

Grabbing only while locked leaves keys stuck: keys held when the grab starts never send
their release to apps, and Wayland apps repeat a held key forever. So PawLock owns the
keyboards the whole time and drops only key presses while locked, like the macOS tap.
Works on X11, Wayland and the text console.
"""
import os
import select
import signal
import threading
import time

from . import fail, tray
from .core import KEYS, ROWS, log

# Linux input codes (linux/input-event-codes.h), laid out like core.ROWS.
LINUX_ROWS = [
    [41, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],      # ` 1 2 3 4 5 6 7 8 9 0 - =
    [16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27],  # q w e r t y u i o p [ ]
    [30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40],      # a s d f g h j k l ; '
    [44, 45, 46, 47, 48, 49, 50, 51, 52, 53],          # z x c v b n m , . /
]
KEYMAP = {lin: (mac, label) for (_, labels, macs), lins in zip(ROWS, LINUX_ROWS, strict=True)
          for label, mac, lin in zip(labels, macs, lins, strict=True)}
KEYMAP |= {lin: (KEYS[name], " " if name == "space" else "") for lin, name in {
    14: "backspace", 111: "delete", 105: "left", 106: "right", 103: "up", 108: "down",
    104: "page_up", 109: "page_down", 57: "space", 28: "enter", 15: "tab", 1: "escape"}.items()}
KEYMAP[96] = (76, "")                                 # keypad enter
MODIFIERS = {29, 42, 54, 56, 58, 97, 100, 125, 126}  # ctrl, shift, alt, caps lock, meta (L/R)
BTN_MISC = 0x100  # codes from here up are mouse buttons and media keys: never touched
STALL = 5         # seconds without a loop pass before the watchdog frees the keyboard


def key(code):
    """Linux key code -> (core key id, char), None for modifiers.
    ponytail: char is the US QWERTY label of the position, add xkbcommon for real layouts."""
    if code in MODIFIERS:
        return None
    return KEYMAP.get(code, (1000 + code, ""))  # other keys: off the grid, never neighbours


def run(ctl, cfg, dry_run):
    import evdev
    from evdev import ecodes as e

    if cfg["pause_in_apps"]:
        log.warning("pause_in_apps is not supported on Linux yet, use Pause in the tray")
    # ponytail: keyboards plugged in after start are not watched; restart PawLock.
    kbds = [d for d in map(evdev.InputDevice, evdev.list_devices())
            if {e.KEY_A, e.KEY_SPACE} <= set(d.capabilities().get(e.EV_KEY, []))]
    if not kbds:
        fail("No keyboard found. Add your user to the input group, then log out and in: "
                 "sudo usermod -aG input $USER")
    log.info("watching %s", ", ".join(d.name for d in kbds))

    ui = None
    if not dry_run:
        try:
            ui = evdev.UInput.from_device(*kbds, name="PawLock keyboard")
        except (OSError, evdev.UInputError) as err:
            fail(f"Can't create a keyboard with /dev/uinput ({err}). See README, Linux permissions.")
        while any(d.active_keys() for d in kbds):
            time.sleep(0.05)  # grab while a key is down and apps never see it come up
        for d in kbds:
            d.grab()

    lock = threading.Lock()  # loop thread vs tray thread
    beat = [time.monotonic()]
    icon, shown = None, [None]

    def handle(ev, t):
        """True if the event goes on to apps."""
        if ev.type != e.EV_KEY or ev.code >= BTN_MISC or (k := key(ev.code)) is None:
            return True
        if ev.value == 0:
            ctl.up(k[0], t)
            return True  # key ups always pass, so no app is left with a stuck key
        ctl.down(*k, t)  # value 1 = press, 2 = autorepeat
        return not ctl.locked

    def loop():
        try:
            while True:
                ready, _, _ = select.select([*kbds, *([ui] if ui else [])], [], [], 0.25)
                t = beat[0] = time.monotonic()
                with lock:
                    for src in ready:
                        try:
                            events = list(src.read())
                        except BlockingIOError:
                            continue
                        except OSError:
                            log.warning("keyboard gone: %s", src.name)
                            kbds.remove(src)
                            continue
                        if src is ui:  # LED changes from the desktop: pass to the real keyboards
                            for ev in events:
                                if ev.type == e.EV_LED:
                                    for d in kbds:
                                        d.set_led(ev.code, ev.value)
                            continue
                        for ev in events:
                            try:
                                ok = handle(ev, t)
                            except Exception:
                                log.exception("key handler failed, letting key through")
                                ok = True  # fail open: a bug must never eat the keyboard
                            if ok and ui:
                                ui.write_event(ev)
                    # missed key ups (e.g. SYN_DROPPED) would look like a sitting cat
                    down = {k[0] for d in kbds for c in d.active_keys() if c < BTN_MISC and (k := key(c))}
                    for c in [c for c in ctl.held if c not in down]:
                        ctl.up(c, t)
                    ctl.tick(t)
                if icon and (s := ctl.status(t)) != shown[0]:
                    shown[0] = s
                    icon.update_menu()
        except Exception:
            log.exception("event loop failed, exiting to free the keyboard")
            os._exit(1)

    def watchdog():  # a stuck loop holds the grab, so the keyboard would be dead
        while True:
            time.sleep(1)
            if time.monotonic() - beat[0] > STALL:
                log.error("event loop stalled, exiting to free the keyboard")
                os._exit(1)  # the kernel drops the grab when the process ends

    try:
        icon = tray(ctl, dry_run, lock)
    except Exception as err:
        log.warning("no tray icon (%s). Unlock with the phrase, or wait", err)
    if ui:
        threading.Thread(target=watchdog, daemon=True).start()
    if icon is None:
        try:
            loop()
        except KeyboardInterrupt:
            pass
        return
    threading.Thread(target=loop, daemon=True).start()
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: icon.stop())
    icon.run()
