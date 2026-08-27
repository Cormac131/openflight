#!/usr/bin/env python3
"""Generate the SD card image's branding assets from the OpenFlight logo.

One source image (``ui/public/openflighttransparentlogo.png``) and the UI's
own palette produce every branded surface an owner sees before the app opens:
the boot splash, the desktop wallpaper, and the icon in the menu and on the
desktop. Deriving them instead of committing a dozen PNGs means a logo change
is a one-file change, and the boot screen can never drift from the app.

Run standalone to preview the assets:

    uv run --extra image python scripts/image/make_branding.py --out /tmp/branding

The image build calls it the same way and copies the result into the target
filesystem; see ``customize.sh``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOGO = REPO_ROOT / "ui" / "public" / "openflighttransparentlogo.png"

# Straight from ui/src/index.css — the dark theme the kiosk always runs in.
# Boot splash, wallpaper, and app must be the same colour or the handover
# from firmware to desktop to app flashes three different backgrounds.
BACKGROUND = (0x0E, 0x0F, 0x10, 0xFF)
ACCENT = (0xFF, 0xD4, 0x00, 0xFF)

# 1920x1080 covers the common kiosk displays; the desktop scales it to
# anything else, and a larger source only costs boot-time decode.
WALLPAPER_SIZE = (1920, 1080)
WALLPAPER_LOGO_WIDTH_FRACTION = 0.42

# Plymouth composites the logo over its own background, so the splash asset
# is the logo alone at a size that suits a 720p-and-up framebuffer.
SPLASH_LOGO_WIDTH = 640

# Sizes the freedesktop hicolor icon theme looks in.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

# The progress dot the Plymouth theme cycles through. Its on-screen size is
# set by the theme script; this is rendered larger and downscaled so the
# circle's edge stays smooth.
DOT_SIZE = 10
DOT_SUPERSAMPLE = 8


def load_logo(path: Path) -> Image.Image:
    """Load the logo as RGBA, cropped to its visible pixels.

    The source has generous transparent margins. Cropping to the alpha
    bounding box first means every downstream size is expressed in terms of
    the artwork rather than the padding, so the logo does not shrink when
    somebody re-exports it with different margins.
    """
    logo = Image.open(path).convert("RGBA")
    bbox = logo.getbbox()
    return logo.crop(bbox) if bbox else logo


def scale_to_width(logo: Image.Image, width: int) -> Image.Image:
    """Resize preserving aspect ratio."""
    height = max(1, round(logo.height * (width / logo.width)))
    return logo.resize((width, height), Image.Resampling.LANCZOS)


def make_wallpaper(logo: Image.Image, size: tuple[int, int] = WALLPAPER_SIZE) -> Image.Image:
    """Centre the logo on the brand background, with an accent underline."""
    canvas = Image.new("RGBA", size, BACKGROUND)
    scaled = scale_to_width(logo, round(size[0] * WALLPAPER_LOGO_WIDTH_FRACTION))

    x = (size[0] - scaled.width) // 2
    y = (size[1] - scaled.height) // 2
    canvas.alpha_composite(scaled, (x, y))

    # A thin accent rule under the mark. Drawn as a solid block rather than
    # with ImageDraw so it stays crisp at any wallpaper size.
    rule_width = round(scaled.width * 0.5)
    rule_height = max(2, round(size[1] * 0.003))
    rule = Image.new("RGBA", (rule_width, rule_height), ACCENT)
    canvas.alpha_composite(
        rule,
        ((size[0] - rule_width) // 2, y + scaled.height + round(size[1] * 0.05)),
    )
    return canvas


def make_splash(logo: Image.Image, width: int = SPLASH_LOGO_WIDTH) -> Image.Image:
    """The logo alone, sized for the boot splash. Plymouth paints the background."""
    return scale_to_width(logo, width)


def make_icon(logo: Image.Image, size: int) -> Image.Image:
    """A square icon: the logo fitted inside a transparent square canvas.

    Square is what icon themes expect, and the logo is 2:1, so it is fitted
    to the width and centred vertically rather than stretched.
    """
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scaled = scale_to_width(logo, size)
    if scaled.height > size:
        scaled = logo.resize(
            (max(1, round(logo.width * (size / logo.height))), size), Image.Resampling.LANCZOS
        )
    canvas.alpha_composite(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2))
    return canvas


def make_dot(size: int = DOT_SIZE) -> Image.Image:
    """An accent-coloured circle for the boot splash's progress row.

    Drawn at ``DOT_SUPERSAMPLE`` times the final size and reduced, because
    PIL's ellipse has hard edges and a 10 px aliased circle reads as a
    square on a high-DPI panel.
    """
    scale = size * DOT_SUPERSAMPLE
    canvas = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).ellipse((0, 0, scale - 1, scale - 1), fill=ACCENT)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def generate(logo_path: Path, out_dir: Path) -> list[Path]:
    """Write every branding asset under ``out_dir`` and return the paths."""
    logo = load_logo(logo_path)
    written: list[Path] = []

    (out_dir / "icons").mkdir(parents=True, exist_ok=True)

    wallpaper = out_dir / "openflight-wallpaper.png"
    make_wallpaper(logo).convert("RGB").save(wallpaper, "PNG", optimize=True)
    written.append(wallpaper)

    splash = out_dir / "openflight-splash.png"
    make_splash(logo).save(splash, "PNG", optimize=True)
    written.append(splash)

    dot = out_dir / "openflight-dot.png"
    make_dot().save(dot, "PNG", optimize=True)
    written.append(dot)

    for size in ICON_SIZES:
        icon = out_dir / "icons" / f"openflight-{size}.png"
        make_icon(logo, size).save(icon, "PNG", optimize=True)
        written.append(icon)

    return written


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse the CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--logo",
        type=Path,
        default=DEFAULT_LOGO,
        help=f"Source logo PNG (default: {DEFAULT_LOGO.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Directory to write the generated assets into.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Generate every branding asset, printing the paths written."""
    args = parse_args(argv)
    if not args.logo.is_file():
        print(f"logo not found: {args.logo}", file=sys.stderr)
        return 1
    for path in generate(args.logo, args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
