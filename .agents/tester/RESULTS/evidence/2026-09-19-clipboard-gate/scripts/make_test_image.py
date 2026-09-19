#!/usr/bin/env python3
"""Generate a distinctive PNG that the image-reader LLM can recognize.
The image carries the literal text 'GATE-42 CLIPBOARD E2E' on a teal background
with a unique 6-digit code '847291' so we can verify the conversion text
mentions what the image actually shows (not just generic placeholder).
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

OUT = sys.argv[1] if len(sys.argv) > 1 else "/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-clipboard-img/data-gate-main/scripts/gate42.png"

W, H = 480, 200
img = Image.new("RGB", (W, H), (32, 138, 145))  # teal
d = ImageDraw.Draw(img)

# Try to load a usable system font; fall back to default bitmap.
font_big = None
font_small = None
for path in [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]:
    if os.path.exists(path):
        try:
            font_big = ImageFont.truetype(path, 38)
            font_small = ImageFont.truetype(path, 22)
            break
        except Exception:
            pass

if font_big is None:
    font_big = ImageFont.load_default()
    font_small = ImageFont.load_default()

# main banner
d.rectangle([(0, 0), (W, H)], fill=(32, 138, 145))
d.text((20, 30), "GATE-42 CLIPBOARD E2E", fill=(255, 255, 255), font=font_big)
d.text((20, 100), "Verification code: 847291", fill=(255, 240, 180), font=font_small)
d.text((20, 140), "Triangle marker: >>> <<<", fill=(255, 255, 255), font=font_small)

# Decorative diamond to make shape distinctive
d.polygon([(420, 40), (450, 100), (420, 160), (390, 100)], fill=(255, 200, 60))

img.save(OUT, "PNG")
print(f"Wrote {OUT} size={os.path.getsize(OUT)} bytes dims={W}x{H}")
