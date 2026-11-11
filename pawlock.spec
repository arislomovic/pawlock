# PyInstaller spec. Builds dist/PawLock.app on macOS and dist/PawLock.exe on Windows.
#   uv run pyinstaller pawlock.spec
# PyInstaller can't cross-compile: build each one on its own OS.
import sys
import tomllib
from pathlib import Path

from PIL import Image, ImageDraw

version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
glyph = "src/pawlock/paw_lock.png"

# App icon: the light grey glyph on a dark rounded square, so it shows on white Finder and
# Explorer windows. PyInstaller turns the PNG into .icns / .ico.
icon = Path("build/app_icon.png")
icon.parent.mkdir(exist_ok=True)
img = Image.new("RGBA", (1024, 1024))
ImageDraw.Draw(img).rounded_rectangle((100, 100, 924, 924), radius=185, fill="#2c2c2e")
img.alpha_composite(Image.open(glyph).convert("RGBA").resize((600, 600), Image.LANCZOS), (212, 212))
img.save(icon)

a = Analysis(["src/pawlock/__main__.py"], pathex=["src"], datas=[(glyph, "pawlock")])
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PawLock", console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="PawLock")
    app = BUNDLE(coll, name="PawLock.app", icon=str(icon), version=version,
                 bundle_identifier="app.pawlock.PawLock",
                 info_plist={"LSUIElement": True})  # menu bar only, no Dock icon
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PawLock", icon=str(icon),
              console=False)  # one file, no console window: quit from the tray
