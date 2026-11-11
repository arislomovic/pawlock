"""Pure logic: key grid, cat rules, lock controller, config. No OS calls, so tests run anywhere."""
import copy
import logging
from collections import deque
from pathlib import Path

log = logging.getLogger("pawlock")

# Physical key positions by macOS virtual keycode (kVK_ANSI_*). Keycodes are positional,
# so this grid is right for QWERTY, AZERTY, QWERTZ and Dvorak alike.
# Rows: (x offset in key widths, labels, keycodes). Offsets match a real keyboard's stagger.
ROWS = [
    (0.0, "`1234567890-=", [50, 18, 19, 20, 21, 23, 22, 26, 28, 25, 29, 27, 24]),
    (1.5, "qwertyuiop[]", [12, 13, 14, 15, 17, 16, 32, 34, 31, 35, 33, 30]),
    (1.75, "asdfghjkl;'", [0, 1, 2, 3, 5, 4, 38, 40, 37, 41, 39]),
    (2.25, "zxcvbnm,./", [6, 7, 8, 9, 11, 45, 46, 43, 47, 44]),
]
POS = {code: (r, off + i) for r, (off, _, codes) in enumerate(ROWS) for i, code in enumerate(codes)}
KEYS = {label: code for off, labels, codes in ROWS for label, code in zip(labels, codes)}
KEYS |= dict(backspace=51, delete=117, left=123, right=124, down=125, up=126,
             page_up=116, page_down=121, space=49, enter=36, tab=48, escape=53)
MODIFIERS = set(range(54, 64))  # cmd, shift, caps, option, control (L/R), fn
ENTER = {36, 76}                # return, keypad enter
WINDOW = 2.0                    # seconds of key downs kept in memory

DEFAULTS = {
    "rules": {
        "paw_press": {"enabled": True, "min_keys": 3, "within_ms": 40},
        "too_many_held": {"enabled": True, "min_keys": 4},
        "sitting": {"enabled": True, "hold_seconds": 1.5, "long_hold_seconds": 6,
                    "long_hold_keys": ["backspace", "delete", "left", "right", "up", "down",
                                       "page_up", "page_down", "space"]},
        "mash": {"enabled": True, "min_keys_per_second": 12, "min_neighbour_share": 0.7},
    },
    "unlock_quiet_seconds": 3,
    "unlock_phrase": "letmein",
    "max_lock_seconds": 120,
    "pause_in_apps": [],
    "log_file": "~/.pawlock/pawlock.log",
}


def near(a, b):
    """Keys touch on the physical keyboard (same key counts). Keys off the grid touch nothing."""
    pa, pb = POS.get(a), POS.get(b)
    return bool(pa and pb) and abs(pa[0] - pb[0]) <= 1 and abs(pa[1] - pb[1]) <= 1


def biggest_cluster(keys):
    left, best = set(keys), 0
    while left:
        stack, n = [left.pop()], 0
        while stack:
            k = stack.pop()
            n += 1
            nb = {o for o in left if near(k, o)}
            left -= nb
            stack += nb
        best = max(best, n)
    return best


def check(downs, held, now, rules, long_keys):
    """downs: [(t, code)] non-repeat presses, oldest first. held: {code: t_down}.
    Returns 'rule: why' or None. The why holds counts only, never keys."""
    r = rules["paw_press"]
    if r["enabled"] and downs:
        recent = [c for t, c in downs if t >= downs[-1][0] - r["within_ms"] / 1000]
        if (n := biggest_cluster(recent)) >= r["min_keys"]:
            return f"paw_press: {n} neighbouring keys within {r['within_ms']} ms"
    r = rules["too_many_held"]
    if r["enabled"] and len(held) >= r["min_keys"]:
        return f"too_many_held: {len(held)} keys held"
    r = rules["sitting"]
    if r["enabled"]:
        for code, t in held.items():
            limit = r["long_hold_seconds"] if code in long_keys else r["hold_seconds"]
            if now - t > limit:
                return f"sitting: key held {now - t:.1f} s (limit {limit} s)"
    r = rules["mash"]
    if r["enabled"]:
        w = [c for t, c in downs if t > now - 1]
        if len(w) >= r["min_keys_per_second"]:
            share = sum(near(a, b) for a, b in zip(w, w[1:])) / (len(w) - 1)
            if share >= r["min_neighbour_share"]:
                return f"mash: {len(w)} keys in 1 s, {share:.0%} neighbours"
    return None


class Controller:
    """Lock state. Not thread-safe: the macOS backend calls everything from the main run loop."""

    def __init__(self, cfg, debug=False):
        self.cfg, self.debug = cfg, debug
        self.long_keys = {KEYS[n] for n in cfg["rules"]["sitting"]["long_hold_keys"]}
        self.downs = deque()       # (t, code), last WINDOW seconds only
        self.held = {}             # code -> t of key down
        self.last_down = float("-inf")
        self.locked_at = None
        self.paused_until = 0.0
        self.app_paused = False
        self.typed = ""            # last len(unlock_phrase) chars typed while locked

    @property
    def locked(self):
        return self.locked_at is not None

    def status(self, now):
        if now < self.paused_until or self.app_paused:
            return "Paused"
        return "Locked" if self.locked else "Watching"

    def down(self, code, char, now):
        """Every key down, autorepeats included. Caller swallows the key if self.locked after."""
        if code in MODIFIERS:
            return
        self._expire(now)  # cat may have left before this key: unlock first
        if code not in self.held:  # autorepeat of a held key is not a new press
            self.held[code] = now
            self.last_down = now
            self.downs.append((now, code))
        if self.locked:
            phrase = self.cfg["unlock_phrase"]
            if code in ENTER:
                if self.typed.endswith(phrase):
                    self.unlock("phrase")
                self.typed = ""
            elif char:
                self.typed = (self.typed + char)[-len(phrase):]
        else:
            self._check(now)

    def up(self, code, now):
        self.held.pop(code, None)

    def tick(self, now):
        """Call a few times a second: catches sitting without autorepeat and timed unlocks."""
        self._expire(now)
        if not self.locked:
            self._check(now)

    def pause(self, seconds, now):
        self.unlock("pause")
        self.paused_until = now + seconds

    def unlock(self, why):
        if self.locked:
            log.info("UNLOCK %s", why)
        self.locked_at = None
        self.downs.clear()  # don't let pre-lock keys re-fire a rule
        self.typed = ""

    def _expire(self, now):
        while self.downs and self.downs[0][0] < now - WINDOW:
            self.downs.popleft()
        if self.locked:
            if now - self.locked_at >= self.cfg["max_lock_seconds"]:
                self.unlock("time limit")
            elif not self.held and now - self.last_down >= self.cfg["unlock_quiet_seconds"]:
                self.unlock("cat left")

    def _check(self, now):
        if now < self.paused_until or self.app_paused:
            return
        if why := check(self.downs, self.held, now, self.cfg["rules"], self.long_keys):
            self.locked_at = now
            self.typed = ""
            log.info("LOCK %s", why if self.debug else why.split(":")[0])


def load_config(path):
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path).expanduser()
    if path.exists():
        import yaml
        _merge(cfg, yaml.safe_load(path.read_text()) or {})
    if not cfg["unlock_phrase"]:
        raise ValueError("unlock_phrase must not be empty")
    if bad := set(cfg["rules"]["sitting"]["long_hold_keys"]) - KEYS.keys():
        raise ValueError(f"unknown long_hold_keys: {sorted(bad)} (known: {sorted(KEYS)})")
    return cfg


def _merge(base, new, where=""):
    for k, v in new.items():
        if k not in base:
            raise ValueError(f"unknown config key: {where}{k}")
        if isinstance(base[k], dict):
            _merge(base[k], v, f"{where}{k}.")
        else:
            base[k] = v
