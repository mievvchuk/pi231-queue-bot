"""Generate dependency-free queue pictogram PNGs using the existing Pillow dependency."""
from pathlib import Path
from PIL import Image, ImageDraw

target = Path(__file__).resolve().parents[1] / 'queue_bot' / 'mini_app' / 'static'
for size in (192, 512):
    scale = size / 512
    image = Image.new('RGB', (size, size), '#101218')
    draw = ImageDraw.Draw(image)
    def box(coords):
        return tuple(round(value * scale) for value in coords)
    draw.rounded_rectangle(box((104, 104, 408, 408)), radius=round(64 * scale), fill='#0a84ff')
    for y, end in ((186, 352), (256, 326), (326, 300)):
        draw.ellipse(box((158, y-10, 178, y+10)), fill='white')
        draw.rounded_rectangle(box((202, y-9, end, y+9)), radius=round(9*scale), fill='white')
    image.save(target / f'icon-{size}.png')
