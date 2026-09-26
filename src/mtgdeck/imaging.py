"""Print image processing with Pillow: bleed for Scryfall scans, plain cardback, PDF sheets.

MakePlayingCards cards are 2.5" x 3.5" (63 x 88 mm) with 1/8" bleed on every side, i.e. the
uploaded image covers 2.74" x 3.74" (at 300 DPI: 822 x 1122 px). MPC Autofill scans already
include that bleed; Scryfall scans don't (and have transparent rounded corners).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

DPI = 300
TRIM_IN = (2.5, 3.5)
BLEED_IN = 0.12  # MPC template: 2.74" x 3.74" incl. bleed
TRIM_PX = (round(TRIM_IN[0] * DPI), round(TRIM_IN[1] * DPI))  # 750 x 1050
BLEED_PX = round(BLEED_IN * DPI)  # 36
FULL_PX = (TRIM_PX[0] + 2 * BLEED_PX, TRIM_PX[1] + 2 * BLEED_PX)  # 822 x 1122

PAPER_PX = {"A4": (2480, 3508), "Letter": (2550, 3300)}  # at 300 DPI
CARD_PDF_PX = (744, 1039)  # 63 x 88 mm at 300 DPI (card size used by most proxy printers)


def _border_colour(img: Image.Image) -> tuple[int, int, int]:
    """Median colour of a thin ring inside the card border (ignores transparent corners)."""
    w, h = img.size
    ring = Image.new("RGB", (w, 8))
    rgb = img.convert("RGB")
    ring.paste(rgb.crop((0, 12, w, 16)), (0, 0))
    ring.paste(rgb.crop((0, h - 16, w, h - 12)), (0, 4))
    return tuple(int(v) for v in ImageStat.Stat(ring).median)  # type: ignore[return-value]


def add_bleed(src: Path, dst: Path) -> Path:
    """Scryfall scan -> MPC-ready image: fill rounded corners, scale to trim size, extend edges."""
    img = Image.open(src)
    img = img.convert("RGBA")
    base = Image.new("RGBA", img.size, _border_colour(img) + (255,))
    base.alpha_composite(img)
    card = base.convert("RGB").resize(TRIM_PX, Image.Resampling.LANCZOS)

    out = Image.new("RGB", FULL_PX)
    out.paste(card, (BLEED_PX, BLEED_PX))
    w, h, b = TRIM_PX[0], TRIM_PX[1], BLEED_PX
    # replicate the outermost pixel rows/columns into the bleed area
    out.paste(card.crop((0, 0, w, 1)).resize((w, b)), (b, 0))
    out.paste(card.crop((0, h - 1, w, h)).resize((w, b)), (b, h + b))
    out.paste(out.crop((b, 0, b + 1, h + 2 * b)).resize((b, h + 2 * b)), (0, 0))
    out.paste(out.crop((w + b - 1, 0, w + b, h + 2 * b)).resize((b, h + 2 * b)), (w + b, 0))
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, "JPEG", quality=95, dpi=(DPI, DPI))
    return dst


def plain_cardback(dst: Path, rgb: tuple[int, int, int] = (38, 30, 58)) -> Path:
    """Single-colour cardback with a subtle frame (no copyrighted artwork)."""
    img = Image.new("RGB", FULL_PX, rgb)
    draw = ImageDraw.Draw(img)
    inset = BLEED_PX + 40
    lighter = tuple(min(255, c + 40) for c in rgb)
    draw.rounded_rectangle((inset, inset, FULL_PX[0] - inset, FULL_PX[1] - inset), radius=40, outline=lighter, width=6)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "JPEG", quality=95, dpi=(DPI, DPI))
    return dst


def trim(img: Image.Image, has_bleed: bool = True) -> Image.Image:
    """Crop the bleed (if any) and scale to the PDF card size."""
    img = img.convert("RGB")
    if has_bleed:
        w, h = img.size
        bx = round(w * BLEED_IN / (TRIM_IN[0] + 2 * BLEED_IN))
        by = round(h * BLEED_IN / (TRIM_IN[1] + 2 * BLEED_IN))
        img = img.crop((bx, by, w - bx, h - by))
    return img.resize(CARD_PDF_PX, Image.Resampling.LANCZOS)


def _pages(images: list[Path], paper: str, cut_marks: bool) -> Iterator[Image.Image]:
    pw, ph = PAPER_PX[paper]
    cw, ch = CARD_PDF_PX
    mx, my = (pw - 3 * cw) // 2, (ph - 3 * ch) // 2
    for start in range(0, len(images), 9):
        page = Image.new("RGB", (pw, ph), "white")
        for i, path in enumerate(images[start : start + 9]):
            x, y = mx + (i % 3) * cw, my + (i // 3) * ch
            with Image.open(path) as im:
                page.paste(trim(im), (x, y))
        if cut_marks:
            draw = ImageDraw.Draw(page)
            mark = 40
            for col in range(4):
                x = mx + col * cw
                draw.line((x, my - mark - 10, x, my - 10), fill="black", width=2)
                draw.line((x, my + 3 * ch + 10, x, my + 3 * ch + mark + 10), fill="black", width=2)
            for row in range(4):
                y = my + row * ch
                draw.line((mx - mark - 10, y, mx - 10, y), fill="black", width=2)
                draw.line((mx + 3 * cw + 10, y, mx + 3 * cw + mark + 10, y), fill="black", width=2)
        yield page


def make_pdf(images: Iterable[Path], dst: Path, *, paper: str = "A4", cut_marks: bool = True) -> dict[str, int]:
    """Proxy sheets: 3 x 3 cards (63 x 88 mm) per page at 300 DPI. ``images`` must include bleed."""
    if paper not in PAPER_PX:
        raise ValueError(f"paper must be one of {sorted(PAPER_PX)}")
    images = list(images)
    if not images:
        raise ValueError("no images")
    pages = _pages(images, paper, cut_marks)
    first = next(pages)
    dst.parent.mkdir(parents=True, exist_ok=True)
    first.save(dst, "PDF", resolution=DPI, save_all=True, append_images=pages)
    return {"cards": len(images), "pages": (len(images) + 8) // 9}
