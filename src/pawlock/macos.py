"""macOS backend: Quartz event tap on the main run loop."""
import signal
import time

import AppKit
import Quartz

from . import ICON, fail, tray
from .core import log


def menu_bar_image(icon):
    """pystray shrinks the icon to 22 px (blurry on Retina) and keeps its colour (light grey
    vanishes on a light menu bar). Give AppKit the 256 px PNG as a template image instead:
    sharp, and tinted to match light or dark mode.
    ponytail: replaces pystray's private Icon._assert_image; recheck on pystray upgrades."""
    if icon._icon_image is None:
        t = icon._status_bar.thickness()
        icon._icon_image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(ICON))
        icon._icon_image.setSize_((t, t))
        icon._icon_image.setTemplate_(True)
    icon._status_item.button().setImage_(icon._icon_image)


def run(ctl, cfg, dry_run):
    now = time.monotonic
    paused_apps = {a.lower() for a in cfg["pause_in_apps"]}

    # ponytail: no watchdog thread. Everything runs on the main run loop; if it stalls,
    # macOS disables the tap itself and keys flow again. If the process dies, the tap dies.
    def on_key(proxy, etype, event, refcon):
        try:
            if etype == Quartz.kCGEventTapDisabledByTimeout:
                Quartz.CGEventTapEnable(tap, True)
                return event
            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            if etype == Quartz.kCGEventKeyUp:
                ctl.up(code, now())
                return event  # key ups always pass, so no app is left with a stuck key
            _, char = Quartz.CGEventKeyboardGetUnicodeString(event, 4, None, None)
            ctl.down(code, char, now())
            refresh()
            return None if ctl.locked and not dry_run else event
        except Exception:
            log.exception("key handler failed, letting key through")
            return event  # fail open: a bug must never eat the keyboard

    def tick(timer, info):
        try:
            for code in [c for c in ctl.held
                         if not Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateHIDSystemState, c)]:
                ctl.up(code, now())  # missed key up (e.g. secure input); stale keys look like sitting
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            ctl.app_paused = bool(app) and (app.localizedName() or "").lower() in paused_apps
            ctl.tick(now())
            refresh()
        except Exception:
            log.exception("tick failed")

    shown = [None]

    def refresh():
        if (s := ctl.status(now())) != shown[0]:
            shown[0] = s
            icon.update_menu()

    mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
    mode = Quartz.kCGEventTapOptionListenOnly if dry_run else Quartz.kCGEventTapOptionDefault
    tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                  mode, mask, on_key, None)
    if not tap:
        fail("Can't watch the keyboard. In System Settings > Privacy & Security, grant "
             "Accessibility and Input Monitoring to PawLock (or to your terminal, if you "
             "started it from one), then start PawLock again.")
    loop = Quartz.CFRunLoopGetMain()
    Quartz.CFRunLoopAddSource(loop, Quartz.CFMachPortCreateRunLoopSource(None, tap, 0),
                              Quartz.kCFRunLoopCommonModes)
    timer = Quartz.CFRunLoopTimerCreate(None, Quartz.CFAbsoluteTimeGetCurrent(), 0.25, 0, 0, tick, None)
    Quartz.CFRunLoopAddTimer(loop, timer, Quartz.kCFRunLoopCommonModes)

    icon = tray(ctl, dry_run)
    icon._assert_image = lambda: menu_bar_image(icon)
    signal.signal(signal.SIGTERM, lambda *_: icon.stop())  # runs on the next tick
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory)  # tray only, no Dock icon
    icon.run()
