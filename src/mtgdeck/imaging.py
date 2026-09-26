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


def sizes(dpi: int = DPI) -> tuple[tuple[int, int], int, tuple[int, int]]:
    """(trim px, bleed px, full px) for a resolution; 300 DPI -> (750x1050, 36, 822x1122)."""
    trim_px = (round(TRIM_IN[0] * dpi), round(TRIM_IN[1] * dpi))
    bleed_px = round(BLEED_IN * dpi)
    return trim_px, bleed_px, (trim_px[0] + 2 * bleed_px, trim_px[1] + 2 * bleed_px)


def add_bleed(src: Path, dst: Path, dpi: int = DPI) -> Path:
    """Card scan -> MPC-ready image at ``dpi``: fill rounded corners, scale to trim size, extend edges."""
    trim_px, bleed_px, full_px = sizes(dpi)
    img = Image.open(src)
    img = img.convert("RGBA")
    base = Image.new("RGBA", img.size, _border_colour(img) + (255,))
    base.alpha_composite(img)
    card = base.convert("RGB").resize(trim_px, Image.Resampling.LANCZOS)

    out = Image.new("RGB", full_px)
    out.paste(card, (bleed_px, bleed_px))
    w, h, b = trim_px[0], trim_px[1], bleed_px
    # replicate the outermost pixel rows/columns into the bleed area
    out.paste(card.crop((0, 0, w, 1)).resize((w, b)), (b, 0))
    out.paste(card.crop((0, h - 1, w, h)).resize((w, b)), (b, h + b))
    out.paste(out.crop((b, 0, b + 1, h + 2 * b)).resize((b, h + 2 * b)), (0, 0))
    out.paste(out.crop((w + b - 1, 0, w + b, h + 2 * b)).resize((b, h + 2 * b)), (w + b, 0))
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, "JPEG", quality=95, dpi=(dpi, dpi))
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


def crop_bleed(img: Image.Image) -> Image.Image:
    """Cut the 1/8" bleed off an MPC-sized image (keeps its resolution)."""
    w, h = img.size
    bx = round(w * BLEED_IN / (TRIM_IN[0] + 2 * BLEED_IN))
    by = round(h * BLEED_IN / (TRIM_IN[1] + 2 * BLEED_IN))
    return img.crop((bx, by, w - bx, h - by))


def trim(img: Image.Image, has_bleed: bool = True) -> Image.Image:
    """Crop the bleed (if any) and scale to the PDF card size."""
    img = img.convert("RGB")
    if has_bleed:
        img = crop_bleed(img)
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


# --- descreening --------------------------------------------------------------------------------
# Scryfall scans are photos of *printed* cards: the halftone screen of the print (rosettes with a
# period of ~2-3 px at scan resolution) is part of the image. AI upscalers read it as texture and
# sharpen it into visible lines. Descreening removes the screen before upscaling.


def _box_blur(a, r: int):
    """Separable box blur via cumulative sums (numpy only, edges replicated)."""
    import numpy as np

    k = 2 * r + 1
    p = np.pad(a, r, mode="edge")
    c = np.cumsum(np.cumsum(p, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def descreen(img: Image.Image, strength: str = "normal") -> Image.Image:
    """Remove a print halftone screen with an FFT notch filter.

    Periodic screens show up as sharp peaks in the frequency spectrum, far above the smooth
    falloff of real image content. Peaks above ``min_freq`` are detected per image (log spectrum
    vs. its local background) and damped to the background level; text and edges, whose energy is
    spread over many frequencies, stay sharp. ``strength``: "light" | "normal" | "strong".
    """
    import numpy as np

    # (detection threshold in log-magnitude above background, notch widening in bins, damping factor)
    presets = {"light": (2.2, 1, 1.0), "normal": (1.6, 2, 1.6), "strong": (1.1, 3, 2.2)}
    thr, widen, damp = presets.get(strength, presets["normal"])
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32)
    h, w, _ = rgb.shape

    # mirror-pad to avoid edge discontinuities (they would create spectral streaks)
    ph, pw = h // 8, w // 8
    padded = np.pad(rgb, ((ph, ph), (pw, pw), (0, 0)), mode="reflect")
    H, W = padded.shape[:2]
    spec = np.fft.fft2(padded, axes=(0, 1))

    # detection on the mean log magnitude of all channels
    logmag = np.log1p(np.abs(spec)).mean(axis=2)
    logmag_s = np.fft.fftshift(logmag)
    background = _box_blur(logmag_s, max(3, min(H, W) // 60))
    excess = logmag_s - background
    fy = (np.arange(H) - H // 2)[:, None] / H
    fx = (np.arange(W) - W // 2)[None, :] / W
    radius = np.hypot(fy, fx)
    min_freq = 0.12  # cycles/px: keep everything coarser than ~8 px (art, text strokes' main energy)
    peaks = (excess > thr) & (radius > min_freq)
    if not peaks.any():
        return img
    # widen the notches slightly and turn them into a soft attenuation mask
    notch = _box_blur(peaks.astype(np.float32), widen) > 0.02
    atten = np.where(notch, np.exp(-damp * np.clip(excess, 0.3, None)), 1.0)
    atten = np.fft.ifftshift(_box_blur(atten.astype(np.float32), 1)).astype(np.float32)

    out = np.fft.ifft2(spec * atten[:, :, None], axes=(0, 1)).real[ph : ph + h, pw : pw + w]
    result = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
    if img.mode == "RGBA":
        result.putalpha(alpha)
    return result
