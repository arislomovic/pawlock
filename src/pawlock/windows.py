"""Windows backend: low-level keyboard hook (WH_KEYBOARD_LL) through ctypes.

The hook runs on its own thread with a message loop. Returning 1 drops a key; anything
else goes to CallNextHookEx. If the hook is slow, Windows skips it and passes the key,
and after repeated timeouts removes it, so a stall never locks the keyboard.
Ctrl+Alt+Del can't be hooked, which is a useful escape hatch.
"""
import ntpath
import signal
import threading
import time

from . import fail, linux, tray
from .core import KEYS, log

WH_KEYBOARD_LL, HC_ACTION, WM_TIMER = 13, 0, 0x0113
LLKHF_EXTENDED, LLKHF_INJECTED, LLKHF_UP = 0x01, 0x10, 0x80
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}  # shift ctrl alt caps win
EXTENDED = {0x48: "up", 0x50: "down", 0x4B: "left", 0x4D: "right",
            0x49: "page_up", 0x51: "page_down", 0x53: "delete"}  # E0-prefixed scan codes
# Windows repeats a held key every ~30 ms after at most 1 s (keyboard delay setting).
# A held key silent for longer lost its key up (e.g. Ctrl+Alt+Del moved to the secure
# desktop) and would look like a sitting cat.
# ponytail: only the last key pressed repeats, so older held keys get dropped too.
# That only weakens detection, it never causes a lock. Upgrade: GetAsyncKeyState for passed keys.
STALE = 1.2


def key(scan, extended):
    """Scan code -> (core key id, char), None for modifiers. Main-block PC set-1 scan codes
    are the same numbers as Linux key codes, so the Linux map does the work."""
    if extended:
        if scan == 0x1C:
            return (76, "")  # keypad enter
        return (KEYS[EXTENDED[scan]], "") if scan in EXTENDED else (2000 + scan, "")
    return linux.key(scan)


def foreground_exe(user32, kernel32):
    import ctypes
    from ctypes import wintypes
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""  # e.g. an elevated app
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return ntpath.basename(buf.value).lower() if ok else ""
    finally:
        kernel32.CloseHandle(h)


def run(ctl, cfg, dry_run):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    paused_apps = {a.lower() for a in cfg["pause_in_apps"]}
    paused_apps |= {a + ".exe" for a in paused_apps if not a.endswith(".exe")}
    lock = threading.Lock()  # hook thread vs tray thread
    seen = {}                # core key id -> time of its last down or autorepeat
    icon, shown, state = None, [None], {}
    ready = threading.Event()

    def on_key(n, wparam, lparam):
        try:
            if n == HC_ACTION:
                kb = KBDLLHOOKSTRUCT.from_address(lparam)
                # injected = typed by software (macros, remote tools), not by a paw
                k = key(kb.scanCode, kb.flags & LLKHF_EXTENDED)
                if k and not kb.flags & LLKHF_INJECTED and kb.vkCode not in MODIFIER_VKS:
                    t = time.monotonic()
                    with lock:
                        if kb.flags & LLKHF_UP:
                            ctl.up(k[0], t)  # key ups always pass, so no app is left with a stuck key
                        else:
                            seen[k[0]] = t
                            ctl.down(*k, t)
                            if ctl.locked and not dry_run:
                                return 1
        except Exception:
            log.exception("key handler failed, letting key through")  # fail open
        return user32.CallNextHookEx(None, n, wparam, lparam)

    proc = HOOKPROC(on_key)  # keep a reference, or ctypes frees the callback

    def tick():
        try:
            t = time.monotonic()
            app_paused = bool(paused_apps) and foreground_exe(user32, kernel32) in paused_apps
            with lock:
                ctl.app_paused = app_paused
                for c in [c for c in ctl.held if t - seen.get(c, 0) > STALE]:
                    ctl.up(c, t)
                ctl.tick(t)
                s = ctl.status(t)
            if icon and s != shown[0]:
                shown[0] = s
                icon.update_menu()
        except Exception:
            log.exception("tick failed")

    def hook_thread():
        state["hook"] = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, kernel32.GetModuleHandleW(None), 0)
        state["error"] = ctypes.get_last_error()
        ready.set()
        if not state["hook"]:
            return
        user32.SetTimer(None, 0, 250, None)  # WM_TIMER to this thread's queue
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER:
                tick()

    threading.Thread(target=hook_thread, daemon=True).start()
    ready.wait()
    if not state["hook"]:
        fail(f"Can't install the keyboard hook (Windows error {state['error']}).")
    try:
        icon = tray(ctl, dry_run, lock)
    except Exception as err:
        log.warning("no tray icon (%s). Unlock with the phrase, or wait", err)
    if icon is None:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: icon.stop())
    icon.run()  # the hook dies with the process
