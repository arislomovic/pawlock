"""Windows backend against a fake user32/kernel32, so it runs on any OS.
Drives the real hook callback (through ctypes, with a real KBDLLHOOKSTRUCT) and the timer.
Real Win32 behaviour is not covered: run PawLock on Windows for that.
Run: uv run python tests/windows_sim.py"""
import ctypes
import queue
import threading
import time
from ctypes import wintypes

ctypes.WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
ctypes.get_last_error = getattr(ctypes, "get_last_error", lambda: 0)

import pawlock.windows as W  # noqa: E402
from pawlock.core import DEFAULTS, Controller  # noqa: E402

msgs, hook, fg = queue.Queue(), {}, ["explorer.exe"]


class DLL:
    def __init__(self, **fns):
        for name, f in fns.items():
            setattr(self, name, type("Fn", (), {"__call__": staticmethod(f)})())


def get_message(pmsg, *_):
    m = msgs.get()
    if m is None:
        return 0
    pmsg._obj.message = m
    return 1


def set_pid(hwnd, ppid):
    ppid._obj.value = 42


def image_name(h, flags, buf, psize):
    buf.value = "C:\\Games\\" + fg[0]
    return 1


user32 = DLL(SetWindowsHookExW=lambda i, proc, mod, tid: hook.update(proc=proc, id=i) or 1234,
             CallNextHookEx=lambda *a: 0, GetMessageW=get_message, SetTimer=lambda *a: 1,
             GetForegroundWindow=lambda: 1, GetWindowThreadProcessId=set_pid)
kernel32 = DLL(GetModuleHandleW=lambda n: 1, OpenProcess=lambda *a: 99,
               QueryFullProcessImageNameW=image_name, CloseHandle=lambda h: 1)
ctypes.WinDLL = lambda name, use_last_error=False: {"user32": user32, "kernel32": kernel32}[name]


class KB(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


def send(scan, up=False, ext=False, vk=0x41, injected=False):
    kb = KB(vk, scan, (0x80 if up else 0) | (0x01 if ext else 0) | (0x10 if injected else 0), 0, 0)
    return hook["proc"](0, 0x101 if up else 0x100, ctypes.addressof(kb))


def tap(scan, **kw):
    return send(scan, **kw), send(scan, up=True, **kw)


def tick():
    msgs.put(W.WM_TIMER)
    time.sleep(0.05)


class Icon:
    def __init__(self):
        self.done, self.updates = threading.Event(), 0

    def run(self):
        self.done.wait(30)

    def stop(self):
        self.done.set()

    def update_menu(self):
        self.updates += 1


failed = []


def check(name, actual, expected):
    print("ok  " if actual == expected else "FAIL", name, "" if actual == expected else f"{actual} != {expected}")
    if actual != expected:
        failed.append(name)


def drive(ctl, icon):
    try:
        time.sleep(0.2)
        check("hook installed as WH_KEYBOARD_LL", hook.get("id"), 13)
        check("normal key passes", tap(0x23), (0, 0))
        check("paw: a s passes, d dropped", [send(s) for s in (0x1E, 0x1F, 0x20)], [0, 0, 1])
        check("locked", ctl.locked, True)
        check("press dropped, its up passes", tap(0x2D), (1, 0))
        check("modifier passes", tap(0x2A, vk=0xA0), (0, 0))
        check("injected key passes", tap(0x2D, injected=True), (0, 0))
        check("extended arrow dropped", tap(0x4B, ext=True), (1, 0))
        check("a s d ups pass", [send(s, up=True) for s in (0x1E, 0x1F, 0x20)], [0, 0, 0])
        for s in (0x26, 0x12, 0x14, 0x32, 0x12, 0x17, 0x31):  # l e t m e i n
            tap(s)
        tap(0x1C)  # enter
        check("unlock phrase", ctl.locked, False)
        check("keys pass after unlock", tap(0x25), (0, 0))

        send(0x22)  # g down, its key up never arrives, no autorepeat
        tick()
        check("held key kept while fresh", W.key(0x22, 0)[0] in ctl.held, True)
        time.sleep(W.STALE + 0.1)
        tick()
        check("stale held key dropped", W.key(0x22, 0)[0] in ctl.held, False)
        check("no lock from the stale key", ctl.locked, False)

        before = icon.updates
        fg[0] = "Steam.exe"
        tick()
        check("pause_in_apps matches 'steam'", (ctl.app_paused, ctl.status(time.monotonic())), (True, "Paused"))
        check("tray menu refreshed", icon.updates > before, True)
        check("paused: paw passes", [send(s) for s in (0x1E, 0x1F, 0x20)], [0, 0, 0])
        [send(s, up=True) for s in (0x1E, 0x1F, 0x20)]
        fg[0] = "explorer.exe"
        tick()

        real_down = ctl.down
        ctl.down = lambda *a: 1 / 0
        check("handler error fails open", send(0x23), 0)
        ctl.down = real_down
    finally:
        msgs.put(None)
        icon.stop()


cfg = {**DEFAULTS, "pause_in_apps": ["steam"]}
ctl, icon = Controller(cfg), Icon()
W.tray = lambda *a: icon
threading.Thread(target=drive, args=(ctl, icon), daemon=True).start()
W.run(ctl, cfg, dry_run=False)
print(f"{'FAILED: ' + ', '.join(failed) if failed else 'all passed'}")
raise SystemExit(1 if failed else 0)
