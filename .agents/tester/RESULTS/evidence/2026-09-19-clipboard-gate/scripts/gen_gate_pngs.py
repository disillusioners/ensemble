"""Generate 4 distinguishable PNGs (GATE-N1..N4) for the N=4 concurrency gate."""
from PIL import Image, ImageDraw, ImageFont
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)))
BG = {"N1": (255, 255, 255), "N2": (230, 245, 255), "N3": (255, 240, 230), "N4": (235, 255, 235)}
FG = {"N1": (10, 10, 10), "N2": (0, 60, 160), "N3": (180, 60, 0), "N4": (0, 120, 40)}

for i in (1, 2, 3, 4):
    code = f"GATE-N{i}"
    img = Image.new("RGB", (400, 160), BG[f"N{i}"])
    d = ImageDraw.Draw(img)
    # big banner bars unique per image (i bars top, i*2 bottom) for redundancy
    for b in range(i):
        d.rectangle([10 + b * 30, 8, 30 + b * 30, 20], fill=FG[f"N{i}"])
    for b in range(i * 2):
        d.rectangle([10 + b * 15, 140, 20 + b * 15, 152], fill=FG[f"N{i}"])
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), code, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((400 - w) / 2 - bbox[0], (160 - h) / 2 - bbox[1]), code, font=font, fill=FG[f"N{i}"])
    path = os.path.join(OUT, f"gate_n{i}.png")
    img.save(path, "PNG")
    print(path, img.size, os.path.getsize(path), "bytes")
