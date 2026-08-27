"""Tests for the SD card image's branding asset generator.

The assets are what an owner sees during the ten seconds before the app
opens, and nobody notices they are wrong until an image has already shipped.
These pin the properties that matter: the palette matches the UI, every size
the icon theme asks for exists, and the transparent margins in the source
logo do not leak into the output.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "scripts" / "image" / "make_branding.py"

pytest.importorskip("PIL", reason="branding generation needs the 'image' extra")

from PIL import Image  # noqa: E402


def _load_module():
    """Import make_branding.py, which lives outside the package tree."""
    spec = importlib.util.spec_from_file_location("make_branding", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_branding"] = module
    spec.loader.exec_module(module)
    return module


branding = _load_module()


@pytest.fixture(name="logo")
def _logo():
    """A 2:1 logo with transparent margins, like the real asset."""
    image = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    image.paste((255, 255, 255, 255), (100, 50, 300, 150))
    return image


class TestPalette:
    def test_background_matches_the_ui_theme(self):
        """The splash, wallpaper, and app must not be three different blacks."""
        assert branding.BACKGROUND[:3] == (0x0E, 0x0F, 0x10)

    def test_accent_matches_the_ui_theme(self):
        assert branding.ACCENT[:3] == (0xFF, 0xD4, 0x00)


class TestLoadLogo:
    def test_crops_transparent_margins(self, logo, tmp_path):
        path = tmp_path / "logo.png"
        logo.save(path)
        loaded = branding.load_logo(path)
        assert loaded.size == (200, 100)

    def test_handles_a_fully_transparent_image(self, tmp_path):
        """getbbox() returns None here; cropping to None would raise."""
        path = tmp_path / "blank.png"
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(path)
        assert branding.load_logo(path).size == (10, 10)

    def test_converts_to_rgba(self, tmp_path):
        path = tmp_path / "opaque.png"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(path)
        assert branding.load_logo(path).mode == "RGBA"


class TestScaleToWidth:
    def test_preserves_aspect_ratio(self, logo):
        scaled = branding.scale_to_width(logo, 800)
        assert scaled.size == (800, 400)

    def test_never_produces_a_zero_height(self, logo):
        """A tiny target must still be a real image, not a 0-pixel one."""
        assert branding.scale_to_width(logo, 1).height >= 1


class TestWallpaper:
    def test_uses_the_requested_size(self, logo):
        assert branding.make_wallpaper(logo, (1280, 720)).size == (1280, 720)

    def test_corners_are_the_brand_background(self, logo):
        wallpaper = branding.make_wallpaper(logo, (1280, 720))
        for corner in ((0, 0), (1279, 0), (0, 719), (1279, 719)):
            assert wallpaper.getpixel(corner) == branding.BACKGROUND

    def test_logo_is_centred(self, logo):
        wallpaper = branding.make_wallpaper(logo, (1200, 800))
        centre = wallpaper.getpixel((600, 400))
        assert centre != branding.BACKGROUND

    def test_scales_the_logo_with_the_canvas(self, logo):
        small = branding.make_wallpaper(logo, (800, 600))
        large = branding.make_wallpaper(logo, (3840, 2160))
        # Same fraction of the width in both, so the mark reads the same size
        # on a 7" touchscreen and a wall-mounted TV.
        assert _lit_width(small) < _lit_width(large)


def _lit_width(image):
    """Width of the non-background region on the image's centre row."""
    row = image.height // 2
    columns = [x for x in range(image.width) if image.getpixel((x, row)) != branding.BACKGROUND]
    return (max(columns) - min(columns)) if columns else 0


class TestIcons:
    @pytest.mark.parametrize("size", branding.ICON_SIZES)
    def test_icons_are_square(self, logo, size):
        assert branding.make_icon(logo, size).size == (size, size)

    def test_icons_keep_transparency(self, logo):
        """An opaque background would show as a black box in the taskbar."""
        icon = branding.make_icon(logo, 64)
        assert icon.mode == "RGBA"
        assert icon.getpixel((0, 0))[3] == 0

    def test_a_tall_logo_is_fitted_to_the_height(self):
        """The fallback branch: fitting to width would overflow a 1:2 logo."""
        tall = Image.new("RGBA", (100, 400), (255, 255, 255, 255))
        icon = branding.make_icon(tall, 64)
        assert icon.size == (64, 64)


class TestDot:
    def test_has_the_accent_colour_at_its_centre(self):
        dot = branding.make_dot(20)
        assert dot.getpixel((10, 10))[:3] == branding.ACCENT[:3]

    def test_corners_are_transparent(self):
        """A square dot would read as a row of blocks on the splash."""
        dot = branding.make_dot(20)
        assert dot.getpixel((0, 0))[3] == 0


class TestGenerate:
    def test_writes_every_asset_the_image_installs(self, tmp_path, logo):
        logo_path = tmp_path / "logo.png"
        logo.save(logo_path)
        out = tmp_path / "out"

        written = branding.generate(logo_path, out)

        assert (out / "openflight-wallpaper.png").exists()
        assert (out / "openflight-splash.png").exists()
        assert (out / "openflight-dot.png").exists()
        for size in branding.ICON_SIZES:
            assert (out / "icons" / f"openflight-{size}.png").exists()
        assert len(written) == 3 + len(branding.ICON_SIZES)

    def test_creates_the_output_directory(self, tmp_path, logo):
        logo_path = tmp_path / "logo.png"
        logo.save(logo_path)
        branding.generate(logo_path, tmp_path / "does" / "not" / "exist")
        assert (tmp_path / "does" / "not" / "exist" / "icons").is_dir()

    def test_works_on_the_real_logo(self, tmp_path):
        """The shipped asset must actually be loadable by this generator."""
        assert branding.DEFAULT_LOGO.is_file()
        branding.generate(branding.DEFAULT_LOGO, tmp_path)
        wallpaper = Image.open(tmp_path / "openflight-wallpaper.png")
        assert wallpaper.size == branding.WALLPAPER_SIZE


class TestCli:
    def test_reports_a_missing_logo(self, tmp_path, capsys):
        code = branding.main(["--logo", str(tmp_path / "nope.png"), "--out", str(tmp_path)])
        assert code == 1
        assert "logo not found" in capsys.readouterr().err

    def test_prints_the_written_paths(self, tmp_path, capsys):
        code = branding.main(["--out", str(tmp_path)])
        assert code == 0
        assert "openflight-wallpaper.png" in capsys.readouterr().out
