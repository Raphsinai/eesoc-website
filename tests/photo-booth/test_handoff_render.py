"""Handoff postcard renderer: exact CP1500 geometry and persistent framing."""
import io
import os
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend' / 'api'))
os.environ.setdefault('DATABASE_ADAPTER_URL', 'http://127.0.0.1:8081')
from services import photo_print
from services import booth_template

URL = 'https://eesoc.com/p/testPrintQr012345'


@pytest.fixture
def pictures():
    result = []
    for colors in (('#ff593b', '#1e5167'), ('#52a858', '#e8cf24'),
                   ('#8857c8', '#eeeafa'), ('#fbb037', '#101729')):
        img = Image.new('RGB', (1800, 1200), colors[0])
        for i in range(200, 900):
            img.paste(colors[1], (i, 200, i + 1, 1200))
        data = io.BytesIO()
        img.save(data, 'JPEG')
        result.append(data.getvalue())
    return result


@pytest.mark.parametrize('count', (1, 2, 3, 4))
def test_exact_postcard_dimensions_and_determinism(pictures, count):
    args = (pictures[:count], 'Careers Fair', URL, 'portrait', None)
    first = photo_print.render(*args, event_date='Wed 7 Oct 2026', event_venue='EEE Building')
    second = photo_print.render(*args, event_date='Wed 7 Oct 2026', event_venue='EEE Building')
    assert first == second
    with Image.open(io.BytesIO(first)) as img:
        assert img.size == (1181, 1747)
        assert img.mode == 'RGB'
        assert img.info['dpi'][0] == pytest.approx(300, abs=1)
        # Black ink background, gold top pins, preserved white QR tile.
        assert img.getpixel((3, 200)) == (11, 13, 18)
        assert img.getpixel((23, 12)) == (231, 181, 112)
        assert img.getpixel((965, 1495)) == (255, 255, 255)


@pytest.mark.parametrize('count', (1, 2, 3, 4))
def test_extreme_pan_and_zoom_never_reveal_background(pictures, count):
    windows = booth_template.spec()['layouts'][str(count)]['windows']
    positions = ({'x': 0., 'y': 0., 'zoom': 1.},
                 {'x': 1., 'y': 1., 'zoom': 1.},
                 {'x': 0., 'y': 1., 'zoom': 8.},
                 {'x': 1., 'y': 0., 'zoom': 8.})
    for pos in positions:
        data = photo_print.render(pictures[:count], 'Event', URL, placements=[pos] * count)
        with Image.open(io.BytesIO(data)) as img:
            for slot in windows:
                rect = slot['px300']
                cx = rect['left'] + rect['width'] // 2
                cy = rect['top'] + rect['height'] // 2
                assert img.getpixel((cx, cy)) != (11, 13, 18)


def test_saved_framing_changes_pixels(pictures):
    a = photo_print.render(pictures[:1], 'Event', URL, placements=[{'x': 0, 'y': 0, 'zoom': 2}])
    b = photo_print.render(pictures[:1], 'Event', URL, placements=[{'x': 1, 'y': 1, 'zoom': 2}])
    with Image.open(io.BytesIO(a)) as left, Image.open(io.BytesIO(b)) as right:
        area = (56, 228, 1125, 1434)
        assert ImageChops.difference(left.crop(area), right.crop(area)).getbbox() is not None


def test_placements_reject_out_of_range():
    for key, value in [('x', -0.01), ('y', 1.01), ('zoom', 0.1), ('zoom', 9)]:
        with pytest.raises(ValueError):
            booth_template.normalized_placement({key: value})


def test_handoff_logo_and_font_sources_exist():
    assets = ROOT / 'backend' / 'api' / 'assets' / 'booth'
    for count in range(1, 5):
        assert (assets / f'booth-{count}-photo.svg').exists()
        assert (assets / f'overlay-{count}.png').exists()
    for font in ('SpaceMono-Bold.ttf', 'IBMPlexMono-Regular.ttf'):
        assert (assets / 'fonts' / font).exists()


@pytest.mark.parametrize('url,min_coverage,expected_quiet', [
    ('https://eesoc.com/api/p/AbCdEfGhIjKl', .87, 2),
    ('https://eesoc.raphsinai.dev/api/p/AbCdEfGhIjKl', .88, 2),
    ('https://eesoc.raphsinai.dev/photos/0123456789abcdef0123456789abcdef', .83, 3.5),
])
def test_qr_fills_the_white_tile_in_background_navy(url, min_coverage, expected_quiet):
    """QR ink covers the tile without losing the scannable margin."""
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    modules = len(qr.get_matrix())
    expected = (booth_template.QR_SHORT_QUIET_MODULES if modules <=
                booth_template.QR_DENSE_THRESHOLD else
                booth_template.QR_DENSE_QUIET_MODULES)
    assert expected == expected_quiet

    layout = booth_template.spec()['fixed']['qrTile']
    scale = booth_template.SCALE
    page = Image.new('RGB', (1181, 1747), booth_template.WHITE)
    booth_template._draw_qr(ImageDraw.Draw(page), url)
    ink = ImageChops.difference(page, Image.new('RGB', page.size, booth_template.WHITE))
    bounds = ink.getbbox()
    assert bounds is not None
    left, top, right, bottom = bounds
    tile_left = round(layout['x'] * scale)
    tile_top = round(layout['y'] * scale)
    tile_right = round((layout['x'] + layout['w']) * scale)
    tile_bottom = round((layout['y'] + layout['h']) * scale)
    assert tile_left < left < right < tile_right
    assert tile_top < top < bottom < tile_bottom
    assert (right - left) / (tile_right - tile_left) >= min_coverage
    # Finder corners are subtly rounded, while data modules remain opaque navy.
    assert page.getpixel((left, top)) == (255, 255, 255)
    assert page.getpixel((left + 5, top)) == (11, 13, 18)
    for point in ((left, top + 8), (right - 5, top), (left + 8, bottom - 1)):
        assert page.getpixel(point) in ((11, 13, 18), (255, 255, 255))
