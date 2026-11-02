"""
make_logo.py - draws the Aura logo.

The logo is an "A" drawn as one glowing white ribbon on the app's accent
gradient (indigo -> violet -> magenta, same as the send button), with soft
cyan / magenta light behind it. A sweep of light
crosses it instead of a straight crossbar, and a bright star sits on the top.

Run:  python assets/make_logo.py
Creates:
  assets/logo.svg, static/logo.svg   - rounded-square logo (app, favicon)
  static/logo-round.svg              - round version (dock pill, notification)
  assets/icon.png (256), assets/logo_1024.png, assets/icon.ico (for the .exe)
The PNGs are rendered by headless Microsoft Edge so they match the SVG exactly.
Small icon sizes (16-32 px) use a bolder version so the A stays readable.
"""

import os
import subprocess
import tempfile
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "static")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

A_PATH = "M62 200C88 160 104 96 128 56C152 96 168 160 194 200"   # the ribbon "A"
SWEEP = "M42 152C80 118 176 118 214 140"                          # light across it


def svg(round_=False, bold=False):
    """The logo as SVG text. round_ = circle instead of rounded square."""
    ribbon, sweep, star = (26, 12, 17) if bold else (19, 8, 13)
    shape = ('<circle cx="128" cy="128" r="128"/>' if round_
             else '<rect width="256" height="256" rx="60"/>')
    edge = ('<circle cx="128" cy="128" r="126.5"' if round_
            else '<rect x="1.5" y="1.5" width="253" height="253" rx="58.5"')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4b3cff"/><stop offset=".5" stop-color="#6b5cff"/><stop offset="1" stop-color="#d13dc2"/></linearGradient>
    <linearGradient id="ribbon" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#ffffff"/><stop offset=".5" stop-color="#f1eaff"/><stop offset="1" stop-color="#ffe3f6"/></linearGradient>
    <filter id="haze" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="28"/></filter>
    <filter id="soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="8"/></filter>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    <clipPath id="shape">{shape}</clipPath>
  </defs>
  <g clip-path="url(#shape)">
    <rect width="256" height="256" fill="url(#sky)"/>
    <ellipse cx="60" cy="210" rx="110" ry="60" fill="#3fa8ff" opacity=".45" filter="url(#haze)"/>
    <ellipse cx="210" cy="60" rx="110" ry="60" fill="#e14fd4" opacity=".45" filter="url(#haze)"/>
    <ellipse cx="128" cy="128" rx="90" ry="90" fill="#2a1d9e" opacity=".45" filter="url(#haze)"/>
    <g fill="none" stroke-linecap="round" stroke-linejoin="round">
      <path d="{A_PATH}" stroke="url(#ribbon)" stroke-width="{ribbon + 13}" opacity=".55" filter="url(#soft)"/>
      <path d="{A_PATH}" stroke="url(#ribbon)" stroke-width="{ribbon}"/>
      <path d="{A_PATH}" stroke="#fff" stroke-opacity=".7" stroke-width="{ribbon / 4.5:.1f}"/>
      <path d="{SWEEP}" stroke="#fff" stroke-width="{sweep}" filter="url(#glow)"/>
    </g>
    <circle cx="128" cy="56" r="{star}" fill="#fff" filter="url(#glow)"/>
  </g>
  {edge} fill="none" stroke="#fff" stroke-opacity=".18" stroke-width="3"/>
</svg>
'''


def render(svg_text, size):
    """Render SVG text to a transparent PIL image with headless Edge."""
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "logo.html")
        shot = os.path.join(tmp, "shot.png")
        with open(page, "w", encoding="utf-8") as f:
            f.write('<html><body style="margin:0;background:transparent">'
                    f'<div style="width:{size}px;height:{size}px">'
                    + svg_text.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
                    + "</div></body></html>")
        subprocess.run([EDGE, "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--default-background-color=00000000",
                        f"--window-size={size},{size}", f"--screenshot={shot}",
                        "file:///" + page.replace("\\", "/")],
                       check=True, capture_output=True, timeout=60)
        return Image.open(shot).convert("RGBA").crop((0, 0, size, size))


def save(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    save(os.path.join(HERE, "logo.svg"), svg())
    save(os.path.join(STATIC, "logo.svg"), svg())
    save(os.path.join(STATIC, "logo-round.svg"), svg(round_=True))

    big = render(svg(), 1024)
    big.save(os.path.join(HERE, "logo_1024.png"))
    icon = big.resize((256, 256), Image.LANCZOS)
    icon.save(os.path.join(HERE, "icon.png"))

    # .ico: each size drawn separately, bold version for the tiny ones
    bold = render(svg(bold=True), 512)
    frames = [(bold if s <= 32 else big).resize((s, s), Image.LANCZOS)
              for s in (256, 128, 64, 48, 32, 24, 16)]
    frames[0].save(os.path.join(HERE, "icon.ico"), format="ICO",
                   sizes=[f.size for f in frames], append_images=frames[1:])
    print("Saved logo.svg, logo-round.svg, icon.png, icon.ico, logo_1024.png")
