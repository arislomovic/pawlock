# PawLock

PawLock watches how keys are pressed. If the pattern looks like a cat walking on the keyboard, it locks the keyboard. When the cat is gone, it unlocks.

It exists for one reason: cats walk on keyboards. They send half-written emails, delete files and type `ggggggggggggg` into production terminals.

No camera. No network. Key events stay in memory for about 2 seconds and are never written to disk.

Runs on macOS, Linux and Windows.

---

## How it works

```
OS key events ──► key hook ──► sliding window (2 s) ──► cat rules ──► lock state
                     ▲                                                  │
                     └────────── drop key presses while locked ◄────────┘
```

1. **Hook** every key down and key up: an event tap on macOS, evdev on Linux, a low-level keyboard hook on Windows.
2. **Keep** the last 2 seconds of key presses in memory.
3. **Check** them against a few cat rules (below) on every key press, and 4 times a second.
4. **Lock** when any rule fires. From then on, key presses are still seen by PawLock but not passed to apps. Key releases always pass, so no app is left with a stuck key.
5. **Unlock** when the keyboard has been quiet for a few seconds, or when you type the unlock phrase.

### Why keystrokes work

People press keys with fingertips, one or two at a time. A paw is wide and heavy. It presses a patch of neighbouring keys at once, or rests on one key and holds it down. Those patterns are rare in human typing and easy to spot.

---

## Cat rules

Any one rule firing locks the keyboard. Modifier keys (Shift, Ctrl, Alt/Option, Cmd/Win, Caps Lock, Fn) are ignored by every rule, so shortcuts never trigger a lock. Key repeats from holding a key down are not counted as new presses.

| Rule | Fires when | Default |
|---|---|---|
| **Paw press** | 3+ non-modifier keys go down within 40 ms and are physical neighbours | 3 keys, 40 ms |
| **Too many held** | 4+ non-modifier keys held down at the same time | 4 keys |
| **Sitting** | one key held longer than the hold limit | 1.5 s |
| **Mash** | 12+ key downs in 1 s where most are neighbours of the key before | 12 keys, 70 % neighbours |

Long holds on Backspace, Delete, arrow keys, Page Up/Down and Space use a longer limit (6 s) because people hold those on purpose.

### Neighbour map

"Neighbour" means the keys touch on the physical keyboard. PawLock places each key on this grid, with each row shifted right the way real keyboard rows are:

```
` 1 2 3 4 5 6 7 8 9 0 - =
   q w e r t y u i o p [ ]
    a s d f g h j k l ; '
     z x c v b n m , . /
```

Two keys are neighbours if they are in the same or next row and no more than one key width apart. Keys off the grid (Space, Enter, Backspace, arrows) are nobody's neighbour.

Keys are found by their physical position (macOS keycode, Linux key code, Windows scan code), not by the letter they type. So the grid is right for QWERTY, AZERTY, QWERTZ and Dvorak with no setting to change.

---

## Unlocking

| Way | How |
|---|---|
| **Cat leaves** | No key down for `unlock_quiet_seconds` (default 3 s) and no keys held. Also checked on the next key press, so the first key you press after the cat leaves gets through |
| **Unlock phrase** | Type the phrase (default `letmein`) then Enter. Keys are dropped but PawLock still reads them. On Linux and Windows, type it by key position on a US QWERTY keyboard |
| **Tray** | Click "Unlock now" with the mouse. The mouse is never locked. On Linux this needs a system tray (see Tray menu) |
| **Time limit** | Unlocks after `max_lock_seconds` (default 120 s) no matter what |

---

## Requirements

| Item | Version / note |
|---|---|
| Python | 3.15+ |
| OS | macOS 12+, Linux (X11, Wayland or text console), or Windows 10+ |
| uv | to install and run |

### Python packages

```
pyobjc-framework-Quartz   # macOS: event tap and key state
evdev                     # Linux: read and grab keyboards, uinput keyboard
pystray                   # tray icon
Pillow                    # tray icon image
pyyaml                    # config file
```

uv installs only the ones for your OS. Windows needs no extra package: the hook uses `ctypes` from the standard library. PyInstaller is a dev dependency, used only to build the app.

---

## Install

### From source

```bash
git clone https://github.com/arislomovic/pawlock.git
cd pawlock
uv sync
```

### Packaged app (macOS and Windows)

Build on the OS you want the app for. PyInstaller can't build for another OS.

```bash
uv sync
uv run pyinstaller --noconfirm pawlock.spec
```

| OS | You get | Install |
|---|---|---|
| macOS | `dist/PawLock.app`: menu bar only, no Dock icon | Drag it to Applications and open it |
| Windows | `dist/PawLock.exe`: one file, no console window | Put it anywhere and run it. Quit from the tray |

To start PawLock at login:

- macOS: System Settings ▸ General ▸ Login Items ▸ add PawLock.
- Windows: press Win+R, type `shell:startup`, and put a shortcut to `PawLock.exe` in that folder.

The packaged app has no console. Startup errors (missing permission, bad config) show in a dialog. Everything else goes to the log file.

The build is not signed for distribution:

- **macOS:** PyInstaller signs it ad hoc. That's fine on the Mac that built it. macOS ties permissions to the signature, so after a rebuild remove PawLock from Accessibility and Input Monitoring and add it again. To share the app with other Macs, sign it with a Developer ID and notarize it, or Gatekeeper will block it.
- **Windows:** SmartScreen warns about unsigned programs. Click "More info", then "Run anyway". Sign it to stop the warning.

### Linux

No packaged app. Install the `pawlock` command with uv:

```bash
uv tool install .
```

To start it at login, add `pawlock` to your desktop's autostart programs.

### macOS permissions

System Settings ▸ Privacy & Security. Grant these to **PawLock** if you use the app, or to your terminal if you run it from source (then restart the terminal):

| Mode | What to grant |
|---|---|
| `--dry-run` | **Input Monitoring** (it only listens) |
| Normal | **Accessibility** and **Input Monitoring** (it drops key presses) |

If permission is missing, PawLock says so and exits.

### Linux permissions

1. Read keyboards: add your user to the `input` group, then log out and in.

   ```bash
   sudo usermod -aG input $USER
   ```

2. Create PawLock's keyboard (not needed for `--dry-run`): let the `input` group use `/dev/uinput`.

   ```bash
   echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
     | sudo tee /etc/udev/rules.d/99-pawlock-uinput.rules
   sudo modprobe uinput
   sudo udevadm control --reload && sudo udevadm trigger
   ```

The `input` group can read every key you type. Only add users you trust.

### Windows permissions

Nothing extra. Windows doesn't send keys typed into admin windows to a normal program, so run PawLock as admin if you want it to work there too.

Antivirus tools may flag PawLock because a global key hook looks like a keylogger. The source is small and readable for this reason.

---

## Usage

```bash
uv run pawlock              # start with defaults
uv run pawlock --dry-run    # detect and log, but never lock
uv run pawlock --debug      # log why each rule fired
```

`python -m pawlock` works too, inside the venv.

Run `--dry-run` for a day or two of normal work first. If a rule fires while you are typing, raise its threshold.

### Tray menu

- **Status**: Watching / Locked / Paused
- **Unlock now**
- **Pause 15 min** (for games or anything that holds many keys)
- **Quit**

The icon is `src/pawlock/paw_lock.svg`, a keyboard with a paw padlock. The tray libraries can't read SVG, so PawLock loads a 1024 px PNG of it. The packaged app's icon is made from the same PNG, on a dark rounded square. On macOS it is a template image: macOS draws it dark on a light menu bar and light on a dark one. On Windows and Linux it shows in the SVG's light grey. After editing the SVG, render the PNG again (librsvg; on macOS `brew install librsvg`):

```bash
rsvg-convert -w 1024 -h 1024 src/pawlock/paw_lock.svg -o src/pawlock/paw_lock.png
```

On Linux the tray needs a system tray. KDE, Xfce and most X11 desktops have one. GNOME needs the AppIndicator extension. With no tray, PawLock logs a warning and runs anyway: unlock with the phrase, the quiet time or the time limit, and stop it with Ctrl+C.

---

## Configuration

`~/.pawlock/config.yaml`. The file is optional and every key in it is optional. Leave a key out to keep its default. An unknown key is an error, so typos don't fail silently.

```yaml
rules:
  paw_press:
    enabled: true
    min_keys: 3
    within_ms: 40
  too_many_held:
    enabled: true
    min_keys: 4
  sitting:
    enabled: true
    hold_seconds: 1.5
    long_hold_seconds: 6       # for the keys below
    long_hold_keys: [backspace, delete, left, right, up, down, page_up, page_down, space]
  mash:
    enabled: true
    min_keys_per_second: 12
    min_neighbour_share: 0.7

unlock_quiet_seconds: 3
unlock_phrase: letmein         # must not be empty
max_lock_seconds: 120

pause_in_apps: []              # e.g. ["Steam", "Minecraft"] on macOS, ["steam.exe"] on Windows
log_file: ~/.pawlock/pawlock.log
```

`long_hold_keys` takes any grid key (`a`, `;`, …) or `backspace`, `delete`, `left`, `right`, `up`, `down`, `page_up`, `page_down`, `space`, `enter`, `tab`, `escape`.

`pause_in_apps` is matched against the frontmost app, ignoring case:

- macOS: the app's name, as in the menu bar (`Steam`).
- Windows: the program's file name, with or without `.exe` (`steam` or `steam.exe`). Apps running as admin can't be identified unless PawLock runs as admin too.
- Linux: not supported yet. It logs a warning and is ignored; use Pause in the tray.

---

## Project layout

```
src/pawlock/
├── __init__.py     # CLI, picks the backend, tray menu
├── __main__.py     # python -m pawlock
├── core.py         # key grid, cat rules, lock controller, config. No OS calls
├── macos.py        # Quartz event tap, pause_in_apps
├── linux.py        # evdev grab, uinput keyboard, watchdog
├── windows.py      # WH_KEYBOARD_LL hook through ctypes, pause_in_apps
├── paw_lock.svg    # tray icon, source
└── paw_lock.png    # tray and app icon, 1024 px render of the SVG
tests/
├── test_rules.py   # human and cat key sequences through the controller
├── linux_e2e.py    # fake keyboard through the real Linux backend
└── windows_sim.py  # Windows backend against a fake user32, runs on any OS
pawlock.spec        # PyInstaller build: PawLock.app (macOS), PawLock.exe (Windows)
pyproject.toml
README.md
```

### Hook backend

| OS | Method | Notes |
|---|---|---|
| macOS | Quartz `CGEventTap` at session level, on the main run loop. Return `None` to drop a key press | Needs Accessibility and Input Monitoring. `--dry-run` uses a listen-only tap |
| Linux | `grab()` every keyboard with evdev at start and pass its events on through a uinput keyboard called "PawLock keyboard". Drop key presses while locked | Works on X11, Wayland and the text console. `--dry-run` only reads and never grabs |
| Windows | Low-level keyboard hook (`WH_KEYBOARD_LL`) through `ctypes`, on its own thread with a message loop. Return 1 to drop a key press | Keys typed by software (macros, remote tools) are ignored and always pass. Ctrl+Alt+Del can't be hooked, which is a useful escape hatch |

`core.py` works on macOS keycodes. The Linux backend maps Linux key codes to those (`linux.KEYMAP`). Windows scan codes for the main keys are the same numbers as Linux key codes, so Windows reuses that map and adds the extended keys (arrows, Delete, Page Up/Down, keypad Enter).

**Why Linux grabs all the time.** Grabbing only while locked leaves keys stuck. A key held when the grab starts never sends its release to apps, and Wayland apps repeat a held key forever. So PawLock owns the keyboards the whole time and passes everything on, like the macOS tap. Caps Lock and other LEDs are passed back to the real keyboards. Mouse buttons and media keys are never touched.

---

## Safety rules

Locking someone's keyboard is easy to get wrong. These are not optional.

1. **The mouse is never locked.** "Unlock now" in the tray always works.
2. **Hard time limit.** Unlock after `max_lock_seconds`.
3. **Fail open.** If the key handler raises an error, the key goes through. A bug must never eat the keyboard.
4. **Unlock on exit.** The lock lives only in the PawLock process. When it exits, crashes or is killed, the macOS tap or Windows hook goes with it, and on Linux the kernel drops the grab. The keyboard works again.
5. **Stalls unlock.**
   - macOS: everything runs on the main run loop. If it stalls, macOS turns the tap off and keys flow again. PawLock turns the tap back on when it recovers.
   - Windows: if the hook is slow, Windows skips it and passes the key. After repeated timeouts it removes the hook.
   - Linux: a stalled PawLock would hold the grab with nothing passed on, so a watchdog thread checks the key loop. If it stalls for 5 seconds, PawLock exits and the kernel frees the keyboard. If the key loop crashes, PawLock exits at once.
6. **No stuck keys.** Key releases always pass. A release PawLock missed (on macOS, for example, while a password field had focus) is caught by asking the keyboard which keys are really down. Otherwise a stale key would look like a sitting cat. Windows can't be asked reliably, so a held key that hasn't autorepeated for 1.2 s counts as released (for example after Ctrl+Alt+Del moved you to the secure desktop). On Linux, PawLock also waits until no key is down before it grabs.
7. **Never lock at the login screen.** PawLock runs in the user session only.
8. **No key logging.** Memory holds key codes and timestamps for 2 seconds only. While locked, it also holds the last few typed characters, as many as the unlock phrase has, to check the phrase. The log file records START / STOP, LOCK / UNLOCK and the rule name. `--debug` adds counts and times, never keys.

---

## Build plan

| Step | Deliverable | Status |
|---|---|---|
| 1 | Key grid, cat rules, lock controller (`core.py`) | Done. Tests pass on made-up human and cat sequences |
| 2 | macOS hook + `--dry-run` | Done. Not yet checked with a real palm mash |
| 3 | Lock and unlock | Done. Not yet checked on a real keyboard |
| 4 | Safety rules | Done, see above |
| 5 | Tray | Done |
| 6 | Linux backend | Done. End-to-end test passes on a fake keyboard. Not yet checked on a real desktop |
| 6b | Windows backend | Done. Passes a simulation against a fake `user32`. Not yet run on Windows |
| 7 | Packaging with PyInstaller (`pawlock.spec`) | Done for macOS: `PawLock.app` builds and runs. Windows `PawLock.exe` not yet built (needs a Windows machine). Not signed for distribution |

---

## Testing

```bash
uv run python tests/test_rules.py
```

- **Rules:** made-up key sequences in `tests/test_rules.py`.
  - Human, must not lock: normal typing, fast typing with rollover, shortcuts, holding Backspace, arrows and Space, gaming with short holds, a paused app.
  - Cat, must lock with the right rule: paw press, too many held, sitting (with and without key repeat), mash.
  - Unlocking: cat leaves, next key after quiet, cat still walking, unlock phrase, wrong phrase, time limit, pause.
- **Linux backend:** `tests/linux_e2e.py` makes a fake uinput keyboard, runs PawLock against it, and reads what comes out of "PawLock keyboard". It checks: dry run doesn't grab, normal keys pass, a paw locks, presses are dropped while locked, key ups, modifiers and mouse buttons pass, the unlock phrase, cat-left unlock, the Caps Lock LED, `kill -9` frees the keyboard, and the watchdog exits after a 5 s stall. It needs root and `/dev/uinput`. From macOS, run it in Docker:

  ```bash
  docker run --rm --privileged -v /dev:/dev -v "$PWD":/src:ro python:3.15 \
    sh -c 'pip install -q evdev pyyaml pillow pystray && python /src/tests/linux_e2e.py'
  ```

- **Windows backend:** `tests/windows_sim.py` runs the real Windows backend against a fake `user32` and `kernel32`, so it runs on any OS. It calls the hook through `ctypes` with a real `KBDLLHOOKSTRUCT` and sends timer messages. It checks: normal keys pass, a paw locks, presses are dropped while locked, key ups, modifiers and injected keys pass, arrows are dropped, the unlock phrase, stale held keys, `pause_in_apps`, and failing open. It can't check real Windows behaviour, so run PawLock on Windows for that.

  ```bash
  uv run python tests/windows_sim.py
  ```

- **False lock rate:** run `--dry-run` through a few hours of your own typing. Target zero locks.
- **Failsafe:** `kill -9` during a lock, confirm the keyboard works.
- **Not yet:** recorded sequences from real cats (`--record`), and automated tests of the macOS event tap.

---

## Known limits

- **The first few keys get through.** A rule needs 2 to 4 key events to fire, so the cat types a few characters before the lock. Holding every key back to catch those would make normal typing feel laggy.
- **A careful cat** stepping on one key at a time, briefly, looks like slow human typing. It is rare, and it does little damage.
- **Gaming and chords.** Games hold keys for longer than 1.5 s (sitting) and some editors hold 3 to 4 keys at once. Use `pause_in_apps` or the tray pause.
- **Password fields.** While a password field has focus, macOS hides keys from event taps. PawLock can't see the cat there, so it can't lock.
- **Linux: new keyboards.** A keyboard plugged in after PawLock starts is not watched. Restart PawLock. A keyboard that is unplugged is dropped from the watch list.
- **Linux and Windows: unlock phrase by position.** Linux key events carry no letters, and on Windows reading letters inside the hook breaks dead keys. So the phrase is matched by key position on US QWERTY. On AZERTY, `letmein` is typed as the keys where QWERTY has those letters.
- **Windows: deaf after a long stall.** If Windows removes the hook after repeated timeouts, it doesn't tell PawLock. The keyboard works, but PawLock stops watching until you restart it.
- **Windows: held keys.** Windows repeats only the last key pressed, so another held key goes quiet and counts as released after 1.2 s. This can only miss a sitting cat; it never causes a lock.
- **Laptop keyboards** with poor rollover may not report 4+ held keys. The paw press and sitting rules still work.

---

## License

MIT. See [LICENSE](LICENSE).
