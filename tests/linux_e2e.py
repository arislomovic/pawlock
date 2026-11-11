"""End-to-end Linux test: fake uinput keyboard -> PawLock -> read 'PawLock keyboard'.
Needs root and /dev/uinput. From macOS (or anywhere with Docker):
  docker run --rm --privileged -v /dev:/dev -v "$PWD":/src:ro python:3.15 \
    sh -c 'pip install -q evdev pyyaml pillow pystray && python /src/tests/linux_e2e.py'
"""
import os, select, signal, subprocess, sys, time
from evdev import UInput, InputDevice, list_devices, ecodes as e

A, S, D, X, H, I, K, L, ENTER, SHIFT = 30, 31, 32, 45, 35, 23, 37, 38, 28, 42
fake = UInput({e.EV_KEY: list(range(1, 128)) + [e.BTN_LEFT], e.EV_LED: [e.LED_CAPSL]}, name="fake kbd")
time.sleep(0.3)
env = dict(os.environ, HOME="/tmp/home", PYTHONPATH="/src/src")


def start(*flags):
    return subprocess.Popen([sys.executable, "-m", "pawlock", "--debug", *flags], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def find(name, tries=50):
    for _ in range(tries):
        for path in list_devices():
            if InputDevice(path).name == name:
                return InputDevice(path)
        time.sleep(0.1)


def grabbed():
    d = InputDevice(fake.device.path)
    try:
        d.grab(); d.ungrab(); return False
    except OSError:
        return True


def key(code, v, gap=0.005):
    fake.write(e.EV_KEY, code, v); fake.syn(); time.sleep(gap)


def tap(*codes):
    for c in codes:
        key(c, 1); key(c, 0)


def got(wait=0.2):
    time.sleep(wait)
    res = []
    while select.select([out], [], [], 0)[0]:
        res += [(ev.code, ev.value) for ev in out.read() if ev.type == e.EV_KEY]
    return res


def check(name, actual, expected):
    print("ok  " if actual == expected else "FAIL", name, "" if actual == expected else f"{actual} != {expected}")
    if actual != expected:
        global failed; failed = True


failed = False

# --- dry run: no grab, no PawLock keyboard ---
p = start("--dry-run"); time.sleep(1.5)
check("dry run does not grab", grabbed(), False)
check("dry run has no output keyboard", find("PawLock keyboard", tries=3), None)
p.terminate(); p.wait()

# --- real run ---
p = start()
out = find("PawLock keyboard")
check("output keyboard created", out is not None, True)
time.sleep(0.3)
check("fake keyboard grabbed", grabbed(), True)

tap(H, I)
check("normal keys pass", got(), [(H, 1), (H, 0), (I, 1), (I, 0)])

key(A, 1); key(S, 1); key(D, 1)          # paw: a s d within ~15 ms
tap(X)                                    # dropped while locked
key(SHIFT, 1); key(SHIFT, 0)              # modifiers pass
key(e.BTN_LEFT, 1); key(e.BTN_LEFT, 0)    # mouse buttons pass
key(A, 0); key(S, 0); key(D, 0)           # key ups pass (kernel drops ups of keys that never went down)
check("paw locks, presses dropped, ups/modifiers/mouse pass", got(),
      [(A, 1), (S, 1), (SHIFT, 1), (SHIFT, 0), (e.BTN_LEFT, 1), (e.BTN_LEFT, 0), (A, 0), (S, 0)])

tap(L, 18, 20, 50, 18, I, 49, ENTER)      # letmein + Enter
got()
tap(K)
check("unlock phrase", got(), [(K, 1), (K, 0)])

key(A, 1); key(S, 1); key(D, 1); key(A, 0); key(S, 0); key(D, 0)
got()
tap(K)
check("still locked right after", got(), [])
time.sleep(3.2)
tap(K)
check("unlocks when cat leaves", got(), [(K, 1), (K, 0)])

out.set_led(e.LED_CAPSL, 1); time.sleep(0.3)
leds = []
while select.select([fake], [], [], 0)[0]:
    leds += [(ev.code, ev.value) for ev in fake.read() if ev.type == e.EV_LED]
check("caps lock LED reaches real keyboard", leds, [(e.LED_CAPSL, 1)])

p.send_signal(signal.SIGKILL); p.wait(); time.sleep(0.3)
check("kill -9 frees the keyboard", grabbed(), False)
log = p.stdout.read()

# --- watchdog: pressing z makes the key loop hang for 30 s ---
stall = ("import sys, time, pawlock.linux as L; k = L.key; "
         "L.key = lambda c: (time.sleep(30) if c == 44 else None) or k(c); "
         "sys.argv = ['pawlock']; from pawlock import main; main()")
p = subprocess.Popen([sys.executable, "-c", stall], env=env, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True)
find("PawLock keyboard"); time.sleep(0.3)
tap(44)
time.sleep(4)
check("still grabbed during short stall", grabbed(), True)
time.sleep(3)
check("watchdog exits after 5 s stall", (p.poll(), grabbed()), (1, False))
log += p.stdout.read()
print("--- pawlock output ---")
print(log)
sys.exit(1 if failed else 0)
