#!/usr/bin/env python3
"""Render the Nyx GitHub banner and social-preview card.

The motif is the app's own instrument. The sweep screen draws an eye whose ring
fills with the live IR level, with a tick at the best reading and glare spikes
when it locks on; the banner draws that same eye at 40x, so the picture on the
repo page is the thing you actually look at while sweeping a room, not stock
"cyber" ornament.

Night palette: near-black sky, violet for Nyx herself, IR-crimson for the light
a covert camera leaks. Type is a Didone for the wordmark (Nyx is a Greek night
goddess and the name deserves better than a geometric sans), Avenir Next for
the supporting copy, and a mono for the machine-ish lines.

Everything is supersampled and downscaled with LANCZOS, so thin strokes and the
dotted ring survive.
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math
import os
import random

OUT = os.path.join(os.path.dirname(__file__), "images")
os.makedirs(OUT, exist_ok=True)

DIDOT = "/System/Library/Fonts/Supplemental/Didot.ttc"
AVENIR = "/System/Library/Fonts/Avenir Next.ttc"
MONO = "/System/Library/Fonts/Supplemental/Andale Mono.ttf"
FALLBACK = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# palette — the night, and the invisible light in it
SKY_TOP = (7, 6, 13)
SKY_BOT = (23, 16, 38)
VIOLET = (159, 122, 255)
VIOLET_DIM = (96, 74, 158)
IRRED = (255, 74, 96)  # the 850/940 nm glow the camera leaks
PAPER = (238, 236, 248)
GRAY = (146, 142, 168)
RULE = (54, 46, 78)

SS = 3  # supersample factor


def font(path, px, index=0):
    try:
        return ImageFont.truetype(path, px, index=index)
    except OSError:
        return ImageFont.truetype(FALLBACK, px)


def sky(w, h):
    """Vertical gradient plus a corner vignette, so the dark is not flat."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        t = t * t * (3 - 2 * t)  # ease, so the gradient has no visible banding start
        d.line(
            [(0, y), (w, y)],
            fill=tuple(int(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOT)),
        )
    return img


def starfield(size, n, seed=20260925):
    """A few dozen faint stars. Seeded, so the art is reproducible."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rnd = random.Random(seed)
    w, h = size
    for _ in range(n):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        r = rnd.choice([1, 1, 1, 2]) * SS * 0.5
        a = rnd.randint(30, 120)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*PAPER, a))
    return layer


def crescent(size, cx, cy, r, colour):
    """A crescent cut with *alpha*, not with a disc of background colour.

    Punching the shadow by filling an offset ellipse with the background only
    works on a flat backdrop; over a gradient it leaves a visible dark circle
    hanging next to the moon. Building an L-mode mask and subtracting from it
    lets whatever is behind show through the bite.
    """
    mask = Image.new("L", size, 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    off = int(r * 0.46)
    lift = int(r * 0.10)
    md.ellipse(
        [cx - r + off, cy - r - lift, cx + r + off, cy + r - lift], fill=0
    )
    moon = Image.new("RGBA", size, (*colour, 0))
    moon.putalpha(mask)
    return moon


def beams(layer, cx, cy, r0, r1, colour, n=7, spread=118, tilt=-150, width=5):
    """IR beams that fade out along their length.

    Drawn as a run of short segments with falling alpha instead of one flat
    line: a hard-edged full-length line reads as a scratch on the image, and
    the whole point is light bleeding away into the dark.
    """
    d = ImageDraw.Draw(layer)
    steps = 26
    for i in range(n):
        a = math.radians(tilt - spread / 2 + spread * i / max(1, n - 1))
        ca, sa = math.cos(a), math.sin(a)
        for s in range(steps):
            t0 = s / steps
            t1 = (s + 1) / steps
            fade = (1 - t0) ** 2.4
            alpha = int(135 * fade)
            if alpha <= 2:
                break
            d.line(
                [
                    cx + ca * (r0 + (r1 - r0) * t0),
                    cy + sa * (r0 + (r1 - r0) * t0),
                    cx + ca * (r0 + (r1 - r0) * t1),
                    cy + sa * (r0 + (r1 - r0) * t1),
                ],
                fill=(*colour, alpha),
                width=max(1, int(width * (0.45 + 0.55 * fade))),
            )


def ring_point(cx, cy, radius, deg):
    a = math.radians(deg - 90)
    return cx + math.cos(a) * radius, cy + math.sin(a) * radius


def eye(layer, cx, cy, r, level=76, peak=88):
    """The sweep screen's eye gauge, blown up.

    Same parts in the same order as views/sweep_view.c: outer ring, a dotted
    arc filling clockwise from 12 o'clock with the live level, a tick at the
    peak reading, the iris, a pupil dilated by the level, and the lock-on glare
    spikes. Keeping them in step means the banner cannot drift from the product.
    """
    d = ImageDraw.Draw(layer)

    # outer ring
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*VIOLET, 210), width=int(2.2 * SS))

    # proximity arc — dots, exactly like the device draws it
    span = level * 360 / 100
    deg = 0.0
    while deg < span:
        for rr in (r * 0.90, r * 0.83):
            x, y = ring_point(cx, cy, rr, deg)
            dot = rr * 0.035
            d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=(*VIOLET, 255))
        deg += 3.2

    # peak tick: the reading to beat
    xi, yi = ring_point(cx, cy, r * 0.78, peak * 360 / 100)
    xo, yo = ring_point(cx, cy, r * 1.08, peak * 360 / 100)
    d.line([xi, yi, xo, yo], fill=(*PAPER, 245), width=int(3.2 * SS))

    # iris + pupil, the IR-crimson part
    ir = r * 0.58
    d.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], outline=(*IRRED, 235), width=int(2.6 * SS))
    d.ellipse(
        [cx - ir * 0.92, cy - ir * 0.92, cx + ir * 0.92, cy + ir * 0.92],
        fill=(*IRRED, 42),
    )
    pr = ir * 0.52
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=(9, 6, 14, 255))
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], outline=(*IRRED, 180), width=int(1.4 * SS))

    # catch-light — the spark that makes it read as an eye rather than a target
    gl = pr * 0.34
    gx, gy = cx - pr * 0.36, cy - pr * 0.40
    d.ellipse([gx - gl, gy - gl, gx + gl, gy + gl], fill=(*PAPER, 225))

    # lock-on glare spikes
    for i in range(6):
        deg = i * 60 + 12
        x1, y1 = ring_point(cx, cy, ir * 1.18, deg)
        x2, y2 = ring_point(cx, cy, ir * 1.42, deg)
        d.line([x1, y1, x2, y2], fill=(*IRRED, 200), width=int(2.0 * SS))


def tracked(d, xy, text, f, fill, track=0, anchor_y="a"):
    """Draw text with manual letter-spacing; PIL has no tracking of its own."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + track
    return x


def render(path, W, H, layout="wide"):
    w, h = W * SS, H * SS
    img = sky(w, h).convert("RGBA")
    img.alpha_composite(starfield((w, h), 70 if layout == "wide" else 110))

    if layout == "wide":
        ex, ey, R = int(w * 0.795), int(h * 0.485), int(h * 0.295)
        mx, my, mr = int(w * 0.945), int(h * 0.17), int(h * 0.085)
    else:
        ex, ey, R = int(w * 0.48), int(h * 0.255), int(h * 0.152)
        mx, my, mr = int(w * 0.79), int(h * 0.095), int(h * 0.052)

    # --- art, on its own layer so it can be bloomed as a whole ---
    art = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    beams(art, ex, ey, int(R * 1.15), int(R * 3.3), IRRED, n=7, spread=126, tilt=-150)
    art.alpha_composite(crescent((w, h), mx, my, mr, VIOLET))
    eye(art, ex, ey, R)

    bloom = art.filter(ImageFilter.GaussianBlur(7 * SS))
    img.alpha_composite(Image.blend(Image.new("RGBA", (w, h), (0, 0, 0, 0)), bloom, 0.85))
    img.alpha_composite(art)

    # --- type ---
    tx = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tx)

    if layout == "wide":
        x0 = 74 * SS
        kick_y, title_y, title_px = 84 * SS, 112 * SS, 104 * SS
    else:
        x0 = 88 * SS
        kick_y, title_y, title_px = 310 * SS, 340 * SS, 112 * SS

    f_kick = font(MONO, int(19 * SS))
    f_title = font(DIDOT, title_px, index=2)  # Didot Bold
    f_tag = font(AVENIR, int(31 * SS), index=5)  # Medium
    f_sub = font(AVENIR, int(21 * SS), index=7)  # Regular
    f_foot = font(MONO, int(18 * SS))

    tracked(td, (x0, kick_y), "FLIPPER ZERO — INFRARED", f_kick, VIOLET, track=2.4 * SS)

    # A soft crimson bleed behind the wordmark rather than a hard drop shadow:
    # the name should look like it is leaking IR, not like it has been offset.
    ghost = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ghost).text((x0, title_y), "NYX", font=f_title, fill=(*IRRED, 170))
    tx.alpha_composite(ghost.filter(ImageFilter.GaussianBlur(5 * SS)))
    td.text((x0, title_y), "NYX", font=f_title, fill=PAPER)

    box = td.textbbox((x0, title_y), "NYX", font=f_title)
    rule_y = box[3] + 17 * SS
    td.line([(x0, rule_y), (x0 + 108 * SS, rule_y)], fill=VIOLET, width=int(2 * SS))

    tag_y = rule_y + 19 * SS
    td.text((x0, tag_y), "See the light they hoped you couldn't.", font=f_tag, fill=PAPER)
    td.text(
        (x0, tag_y + 44 * SS),
        "Hidden-camera sweep for Flipper Zero. Honest about what it cannot see.",
        font=f_sub,
        fill=GRAY,
    )

    img.alpha_composite(tx)

    # --- footer ---
    fd = ImageDraw.Draw(img)
    fy = h - 56 * SS
    fd.line([(x0, fy), (w - x0, fy)], fill=RULE, width=int(1.4 * SS))
    fd.text((x0, fy + 14 * SS), "github.com/at0m-b0mb/Nyx-FlipperZero", font=f_foot, fill=GRAY)
    fd.text((w - x0, fy + 14 * SS), "MIT — at0m-b0mb", font=f_foot, fill=VIOLET_DIM, anchor="ra")

    img.convert("RGB").resize((W, H), Image.LANCZOS).save(path)
    print("wrote", path)


if __name__ == "__main__":
    render(os.path.join(OUT, "banner.png"), 1280, 400, layout="wide")
    render(os.path.join(OUT, "social-preview.png"), 1280, 640, layout="card")
