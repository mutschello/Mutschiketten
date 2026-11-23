r"""Erzeugt das Programmsymbol (Initialen JM auf einem Preisschild).

Aufruf: .venv\Scripts\python.exe tools\make_icon.py
Ergebnis: studio/mutschiketten.ico (16-256 px) und studio/mutschiketten.png (512 px)
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = "C:/Windows/Fonts/segoeuib.ttf"
TOP, BOTTOM = (59, 130, 246), (29, 78, 216)   # Blau der App (#2563eb) als Verlauf
INK = (29, 78, 216)
S = 1024                                        # Zeichenauflösung, wird verkleinert


def background(size: int) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    grad = grad.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size * 0.22, fill=255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    return img


def centered_text(d: ImageDraw.ImageDraw, box, text, size, fill):
    f = ImageFont.truetype(FONT, size)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    x = (box[0] + box[2]) / 2 - (l + r) / 2
    y = (box[1] + box[3]) / 2 - (t + b) / 2
    d.text((x, y), text, font=f, fill=fill)


def detailed() -> Image.Image:
    """Großes Symbol: weißes Preisschild mit Loch und Initialen."""
    img = background(S)
    tag = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(tag)
    # Schild: Rechteck mit spitzer linker Seite, Ecken abgerundet über Supersampling
    body = (330, 285, 880, 739)
    d.rounded_rectangle(body, radius=70, fill="white")
    # Spitze bei (150, 512), Flanken bis in den geraden Teil des Schilds; die
    # Spitze wird durch einen Kreis ersetzt, der beide Flanken berührt.
    tip, r = 150.0, 45.0
    half = math.atan2(512 - body[1], 345 - tip)      # Flanke trifft Ober-/Unterkante bei x=345
    cx = tip + r / math.sin(half)
    t = r / math.tan(half)
    tx, ty = tip + math.cos(half) * t, math.sin(half) * t
    d.polygon([(tx, 512 - ty), (345, body[1]), (420, body[1]),
               (420, body[3]), (345, body[3]), (tx, 512 + ty)], fill="white")
    d.ellipse((cx - r, 512 - r, cx + r, 512 + r), fill="white")
    d.ellipse((255 - 42, 512 - 42, 255 + 42, 512 + 42), fill=(0, 0, 0, 0))  # Loch
    centered_text(d, (body[0] + 10, body[1], body[2], body[3]), "JM", 330, INK)
    tag = tag.rotate(-12, resample=Image.BICUBIC, center=(S / 2, S / 2))
    # leichter Schatten unter dem Schild
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow.putalpha(tag.getchannel("A").point(lambda a: a * 0.28))
    shadow = shadow.transform((S, S), Image.AFFINE, (1, 0, -10, 0, 1, -18))
    img.alpha_composite(shadow)
    img.alpha_composite(tag)
    return img


def simple(px: int) -> Image.Image:
    """Kleine Größen: nur Initialen, damit sie lesbar bleiben."""
    big = background(S)
    centered_text(ImageDraw.Draw(big), (0, 0, S, S + 30), "JM", 560, "white")
    return big.resize((px, px), Image.LANCZOS)


def main():
    out = ROOT / "studio"
    large = detailed()
    sizes = {px: (simple(px) if px <= 32 else large.resize((px, px), Image.LANCZOS))
             for px in (16, 20, 24, 32, 40, 48, 64, 128, 256)}
    sizes[256].save(out / "mutschiketten.ico", format="ICO",
                    sizes=[(p, p) for p in sizes], append_images=[sizes[p] for p in sizes if p != 256])
    large.resize((512, 512), Image.LANCZOS).save(out / "mutschiketten.png")
    preview = Image.new("RGBA", (16 + 32 + 48 + 64 + 128 + 256 + 70, 270), (255, 255, 255, 255))
    x = 10
    for p in (16, 32, 48, 64, 128, 256):
        preview.alpha_composite(sizes[p], (x, 260 - p))
        x += p + 10
    preview.save(ROOT / "tools" / "icon_preview.png")
    print("ok")


if __name__ == "__main__":
    main()
