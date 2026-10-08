"""Draw a synthetic Chinese manhua page (speech bubbles on a coloured scene) for tests and demos. No copyrighted art."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINES = [("罗天艰难爬起，", "脸上有些青肿。"), ("这废物不会死了吧？",), ("快撤！",)]
BUBBLES = [((60, 50, 430, 200), 0), ((480, 260, 840, 420), 1), ((120, 520, 400, 650), 2)]


def font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/System/Library/Fonts/PingFang.ttc"):
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    raise SystemExit("no CJK font found")


def make(path: str = "sample_page.png", size=(900, 760)) -> None:
    img = Image.new("RGB", size, (92, 128, 172))
    d = ImageDraw.Draw(img)
    for y in range(size[1]):  # sky gradient so the scene is not flat
        d.line([(0, y), (size[0], y)], fill=(80 + y // 12, 120 + y // 14, 170 - y // 20))
    f = font(34)
    for (x0, y0, x1, y1), i in BUBBLES:
        d.ellipse((x0, y0, x1, y1), fill=(255, 255, 255), outline=(0, 0, 0), width=4)
        lines = LINES[i]
        top = (y0 + y1) / 2 - len(lines) * 21
        for k, ln in enumerate(lines):
            d.text(((x0 + x1) / 2, top + k * 42 + 21), ln, font=f, fill=(0, 0, 0), anchor="mm")
    img.save(path)


if __name__ == "__main__":
    make(*(sys.argv[1:2] or ["sample_page.png"]))
