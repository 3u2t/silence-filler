from __future__ import annotations

from PIL import Image, ImageDraw


def make_icon(enabled: bool = True, playing: bool = False) -> Image.Image:
    """Draw the tray/window icon: green circle, pause bars while filling silence."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg = (29, 185, 84, 255) if enabled else (105, 105, 105, 255)
    draw.ellipse((2, 2, 62, 62), fill=bg)

    white = (255, 255, 255, 255)
    if enabled and playing:
        draw.rounded_rectangle((22, 20, 29, 44), radius=2, fill=white)
        draw.rounded_rectangle((35, 20, 42, 44), radius=2, fill=white)
    else:
        for x, height in ((20, 18), (29, 30), (38, 22)):
            draw.rounded_rectangle(
                (x, 32 - height / 2, x + 6, 32 + height / 2), radius=3, fill=white
            )
    return img
