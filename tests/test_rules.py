"""Synthetic human and cat key sequences through the Controller.
Run: uv run python tests/test_rules.py   (or pytest, if you have it)"""
import copy
import logging

from pawlock.core import DEFAULTS, KEYS, Controller

K = KEYS | {" ": KEYS["space"]}


class Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def sim(events, ticks=(), app_paused=False):
    """events: (t, 'd'|'u', key) with key a label from KEYS or a raw code. Returns (log lines, ctl)."""
    ctl = Controller(copy.deepcopy(DEFAULTS))
    ctl.app_paused = app_paused
    h = Log()
    logging.getLogger("pawlock").addHandler(h)
    logging.getLogger("pawlock").setLevel(logging.INFO)
    try:
        timeline = sorted([(t, kind, key) for t, kind, key in events] + [(t, "t", None) for t in ticks],
                          key=lambda e: e[0])
        for t, kind, key in timeline:
            code = K.get(key, key)
            char = key if isinstance(key, str) and len(key) == 1 else ""
            if kind == "d":
                ctl.down(code, char, t)
            elif kind == "u":
                ctl.up(code, t)
            else:
                ctl.tick(t)
    finally:
        logging.getLogger("pawlock").removeHandler(h)
    return h.lines, ctl


def typed(text, start=0.0, interval=0.2, hold=0.1):
    ev = []
    for i, ch in enumerate(text):
        t = start + i * interval
        ev += [(t, "d", ch), (t + hold, "u", ch)]
    return ev


def held(key, start, seconds, repeat=0.05):
    """Hold a key with macOS-style autorepeat (first repeat after 0.5 s)."""
    ev, t = [(start, "d", key)], start + 0.5
    while t < start + seconds:
        ev.append((t, "d", key))
        t += repeat
    return ev + [(start + seconds, "u", key)]


def ticks(end, step=0.25):
    return [i * step for i in range(int(end / step) + 1)]


TEXT = "the quick brown fox jumps over the lazy dog, we were there sadly as fast as possible. " * 3


# --- human: must never lock ---

def test_normal_typing():
    assert sim(typed(TEXT), ticks(60))[0] == []


def test_fast_typing_with_rollover():
    assert sim(typed(TEXT, interval=0.075, hold=0.12), ticks(20))[0] == []  # 160 wpm


def test_shortcuts():
    cmd, shift, ctrl, opt = 55, 56, 59, 58
    ev = [(0, "d", cmd), (0.01, "d", shift), (0.02, "d", ctrl), (0.03, "d", opt), (0.05, "d", "t"),
          (0.15, "u", "t"), (2.0, "u", cmd), (2.0, "u", shift), (2.0, "u", ctrl), (2.0, "u", opt)]
    assert sim(ev, ticks(3))[0] == []


def test_holding_backspace_and_arrows():
    ev = held("backspace", 0, 4) + held("left", 5, 3) + held("space", 9, 2)
    assert sim(ev, ticks(12))[0] == []


def test_gaming_short_holds():
    ev = held("w", 0, 1.2) + typed("adadad", 0.1, 0.2, 0.15) + [(0.3, "d", 56), (1.1, "u", 56)]
    ev += typed("   ", 0.2, 0.3, 0.08)
    assert sim(ev, ticks(3))[0] == []


def test_paused_app_never_locks():
    assert sim(held("w", 0, 5) + typed("asd", 1, 0.01, 0.3), ticks(6), app_paused=True)[0] == []


# --- cat: must lock with the right rule ---

def test_paw_press():
    lines, ctl = sim([(0, "d", "a"), (0.01, "d", "s"), (0.025, "d", "d")])
    assert lines == ["LOCK paw_press"] and ctl.locked


def test_spread_keys_are_not_a_paw():
    assert sim([(0, "d", "a"), (0.01, "d", "l"), (0.02, "d", "p")] + [(0.08, "u", k) for k in "alp"] + typed("x", 0.1))[0] == []


def test_too_many_held():
    ev = [(i * 0.1, "d", k) for i, k in enumerate("qpzm")]
    assert sim(ev)[0] == ["LOCK too_many_held"]


def test_sitting_with_autorepeat():
    assert sim(held("g", 0, 3))[0][0] == "LOCK sitting"


def test_sitting_without_autorepeat():
    assert sim([(0, "d", "g")], ticks(2))[0] == ["LOCK sitting"]


def test_mash():
    walk = "asdfgfdsasdfgfdsasdf"
    assert sim(typed(walk, interval=0.066, hold=0.05))[0] == ["LOCK mash"]


# --- unlocking ---

PAW = [(0, "d", "a"), (0.01, "d", "s"), (0.025, "d", "d"), (0.2, "u", "a"), (0.2, "u", "s"), (0.2, "u", "d")]


def test_unlock_when_cat_leaves():
    lines, ctl = sim(PAW, ticks(4))
    assert lines == ["LOCK paw_press", "UNLOCK cat left"] and not ctl.locked


def test_unlock_on_next_key_even_without_ticks():
    lines, ctl = sim(PAW + typed("h", start=5))
    assert lines == ["LOCK paw_press", "UNLOCK cat left"] and not ctl.locked


def test_cat_still_walking_stays_locked():
    lines, ctl = sim(PAW + typed("qwerty" * 4, start=0.5, interval=0.4), ticks(9))
    assert lines == ["LOCK paw_press"] and ctl.locked  # never 3 s quiet until 9.7 s


def test_unlock_phrase():
    lines, ctl = sim(PAW + typed("xxletmein", start=0.5, interval=0.15) + typed(["enter"], start=2))
    assert lines == ["LOCK paw_press", "UNLOCK phrase"] and not ctl.locked


def test_wrong_phrase_stays_locked():
    lines, ctl = sim(PAW + typed("letmeout", start=0.5, interval=0.15) + typed(["enter"], start=2))
    assert ctl.locked


def test_time_limit():
    lines, ctl = sim([(0, "d", "g")], ticks(123))
    assert lines[:2] == ["LOCK sitting", "UNLOCK time limit"]


def test_pause():
    ctl = Controller(copy.deepcopy(DEFAULTS))
    ctl.pause(900, 0)
    for k in "asd":
        ctl.down(K[k], k, 0.01)
    assert not ctl.locked and ctl.status(1) == "Paused" and ctl.status(901) == "Watching"


def test_linux_keymap():
    from pawlock.linux import key
    assert key(30) == (K["a"], "a") and key(53) == (K["/"], "/") and key(41) == (K["`"], "`")
    assert key(57) == (K["space"], " ") and key(14) == (K["backspace"], "") and key(42) is None
    assert key(59)[0] not in K.values()  # F1: off the grid, no clash with a mapped key
    lines, _ = sim([(0, "d", key(30)[0]), (0.01, "d", key(31)[0]), (0.02, "d", key(32)[0])])
    assert lines == ["LOCK paw_press"]  # a s d by Linux code


def test_windows_keymap():
    from pawlock.windows import key
    assert key(0x1E, 0) == (K["a"], "a") and key(0x35, 0) == (K["/"], "/") and key(0x39, 0) == (K["space"], " ")
    assert key(0x4B, 1) == (K["left"], "") and key(0x53, 1) == (K["delete"], "") and key(0x1C, 1) == (76, "")
    assert key(0x4B, 0)[0] not in K.values()  # numpad 4 shares the scan code with Left, off the grid
    assert key(0x1C, 0) == (K["enter"], "")


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, f in tests:
        f()
        print("ok", name)
    print(f"{len(tests)} passed")
