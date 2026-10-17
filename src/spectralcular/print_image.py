"""Raster images as they go to print: flat sRGB, a fixed ppi, JPEG.

Lulu's own PDF presets (``Lulu-Interior-Print-PDF.joboptions``) leave color
as sRGB, forbid transparency, downsample to 300 ppi and store images as
maximum-quality JPEG without chroma subsampling. Everything that writes a
raster for the printed page goes through these helpers so the spreads, the
front matter and the cover all match that preset, and so :mod:`print_pdf`
can enforce the same rules on whatever reaches the final PDF.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageCms

PRINT_PPI = 300
# Lulu's preset is QFactor 0.15 ("Maximum", ~Pillow 95). Noisy 10 m plates
# are ~5–8 MB each at 95, ~4 MB at 90 and ~3.5 MB at 88 (PSNR ≈ 35 dB,
# invisible through a 300 ppi halftone); 88 keeps a 100-plate interior under
# Lulu's 500 MB guidance (at 90 it came to 506 MB). Always 4:4:4 — no chroma
# subsampling. Keep in step with book_specs.print_pdf.jpeg_quality.
JPEG_QUALITY = 88

# Editorial paper (render_assets.PAPER) and the plate page's dark ground.
PAPER_RGB = (0xF7, 0xF4, 0xEC)
PLATE_GROUND_RGB = (0x0D, 0x0D, 0x10)


@lru_cache(maxsize=1)
def srgb_icc() -> bytes:
    """An sRGB ICC profile (IEC 61966-2.1 primaries and tone curve)."""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def flatten(img: Image.Image, background: tuple[int, int, int] = PAPER_RGB) -> Image.Image:
    """``img`` as opaque RGB, any alpha composited over ``background``."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        ground = Image.new("RGBA", rgba.size, (*background, 255))
        ground.alpha_composite(rgba)
        return ground.convert("RGB")
    return img.convert("RGB")


def cover_fit(img: Image.Image, width: int, height: int) -> Image.Image:
    """Centre-crop to the target aspect, then resample to ``width × height``
    (CSS ``object-fit: cover; object-position: center``)."""
    src_w, src_h = img.size
    aspect = width / height
    if src_w / src_h > aspect:
        new_w = int(round(src_h * aspect))
        x0 = (src_w - new_w) // 2
        img = img.crop((x0, 0, x0 + new_w, src_h))
    elif src_w / src_h < aspect:
        new_h = int(round(src_w / aspect))
        y0 = (src_h - new_h) // 2
        img = img.crop((0, y0, src_w, y0 + new_h))
    if img.size == (width, height):
        return img
    return img.resize((width, height), Image.Resampling.LANCZOS)


def resample(img: Image.Image, width: int, height: int) -> Image.Image:
    """Lanczos down, bicubic up (Lanczos rings when enlarging small sources)."""
    if img.size == (width, height):
        return img
    up = width > img.width or height > img.height
    return img.resize((width, height), Image.Resampling.BICUBIC if up else Image.Resampling.LANCZOS)


def jpeg_bytes(img: Image.Image, *, quality: int = JPEG_QUALITY, ppi: int = PRINT_PPI) -> bytes:
    """Encode opaque RGB as a print JPEG: 4:4:4, sRGB profile embedded."""
    if img.mode != "RGB":
        img = flatten(img)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=0, optimize=True,
             icc_profile=srgb_icc(), dpi=(ppi, ppi))
    return buf.getvalue()


def save_print_jpeg(img: Image.Image, path: Path, *, quality: int = JPEG_QUALITY,
                    ppi: int = PRINT_PPI) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg_bytes(img, quality=quality, ppi=ppi))
    return path


def px_for(inches: float, ppi: int = PRINT_PPI) -> int:
    return max(1, int(round(inches * ppi)))
