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
        # Navy background, gold pins and the warm ivory QR chip carrier.
        assert img.getpixel((3, 200)) == (11, 13, 18)
        assert img.getpixel((23, 12)) == (231, 181, 112)
        assert img.getpixel((round(302*booth_template.SCALE), round(472*booth_template.SCALE))) == (247, 241, 231)


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


@pytest.mark.parametrize('url', [
    'https://eesoc.com/api/p/AbCdEfGhIjKl',
    'https://eesoc.raphsinai.dev/api/p/AbCdEfGhIjKl',
    'https://eesoc.raphsinai.dev/photos/0123456789abcdef0123456789abcdef',
])
def test_branded_qr_keeps_4_module_quiet_zone_and_logo(url):
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    n = len(qr.get_matrix())
    assert booth_template.QR_QUIET_MODULES == 4
    assert booth_template.QR_BADGE_MODULES == 9
    tile = booth_template.spec()['fixed']['qrTile']
    page = Image.new('RGB', (1181, 1747), booth_template.QR_PAPER)
    booth_template._draw_qr(ImageDraw.Draw(page), url)
    scale = booth_template.SCALE
    cx = round((tile['x']+tile['w']/2)*scale)
    cy = round((tile['y']+tile['h']/2)*scale)
    assert page.getpixel((cx,cy)) != (247,241,231)  # real E in the chip
    pitch = tile['w']*scale/(n+8)
    left = round((tile['x']+tile['w']/2-n*pitch/(2*scale))*scale)
    # At least three entire modules of untouched quiet zone on each side.
    assert page.getpixel((round(left-2*pitch),cy)) == (247,241,231)
    assert (ROOT / 'backend/api/assets/booth/qr-e-mask.png').exists()
