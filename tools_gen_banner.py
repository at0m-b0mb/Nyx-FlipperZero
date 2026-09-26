#!/usr/bin/env python3
"""Render the Nyx GitHub banner and social-preview card.

The hero is not an illustration of the product — it is the product. The right
side of both images is a real 128x64 framebuffer read off a Flipper Zero
(`screenshots/sweep_alarm.png`, the sweep locked onto a live emitter at 98%,
PULSED, STRONG), shown at integer scale in the amber of the actual panel. The
drawn eye behind it is the sweep screen's own gauge, built from the same parts
in the same order `views/sweep_view.c` draws them, and it is deliberately kept
faint and allowed to bleed off the canvas: it is atmosphere, the capture is
evidence.

GitHub's social card geometry is a hard constraint, not a preference:

    Canvas 1280x640. GitHub recommends a 40pt border around anything that
    matters, which is 80px on this 2x canvas, leaving a content box of
    1120x480 at x 80..1200, y 80..560. Twitter/X, Discord, Slack and LinkedIn
    each crop the card to their own aspect ratio, so a footer sitting 36px
    from the bottom looks fine in the file and vanishes on Discord.

Background art may bleed to the edges. Information may not — and that is
checked rather than trusted. Every element that carries meaning is registered
in a SafeBox as it is drawn, and `render()` asserts the union of those
rectangles fits. A layout change that pushes the footer out fails the build
instead of silently shipping a cropped card.
"""
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "images")
SHOTS = os.path.join(HERE, "screenshots")
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
IRRED = (255, 74, 96)
PAPER = (238, 236, 248)
GRAY = (150, 146, 172)
GOLD = (201, 162, 39)
RULE = (54, 46, 78)

# The device's two colours. Only ever applied to a real capture — never drawn.
LCD_ON = (255, 130, 0)
LCD_INK = (17, 10, 0)

SS = 2  # supersample factor

# GitHub's 40pt recommendation, in pixels on a 2x canvas.
SAFE = 80


class SafeBox:
    """Records every rectangle that carries meaning, so the margin can be
    asserted instead of eyeballed. Background art is simply never registered."""

    def __init__(self, w, h, margin):
        self.w, self.h, self.margin = w, h, margin
        self.boxes = []

    def add(self, x0, y0, x1, y1, what):
        self.boxes.append((x0, y0, x1, y1, what))

    def check(self):
        if not self.boxes:
            return
        worst = None
        for x0, y0, x1, y1, what in self.boxes:
            m = min(x0, y0, self.w - x1, self.h - y1)
            if worst is None or m < worst[0]:
                worst = (m, what, (x0, y0, x1, y1))
        m, what, box = worst
        if m < self.margin:
            raise AssertionError(
                f"{what} at {box} leaves a {m}px margin on a {self.w}x{self.h} "
                f"canvas; GitHub's safe border needs {self.margin}px. "
                f"Move it inside x {self.margin}..{self.w - self.margin}, "
                f"y {self.margin}..{self.h - self.margin}."
            )
        return m, what


def font(path, px, index=0):
    try:
        return ImageFont.truetype(path, px, index=index)
    except OSError:
        return ImageFont.truetype(FALLBACK, px)


def sky(w, h):
    """Vertical gradient plus an eased ramp, so the dark is not flat."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        t = t * t * (3 - 2 * t)
        d.line([(0, y), (w, y)],
               fill=tuple(int(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOT)))
    return img


def starfield(size, n, seed=20260926):
    """A few dozen faint stars. Seeded, so the art is reproducible."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rnd = random.Random(seed)
    w, h = size
    for _ in range(n):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        r = rnd.choice([1, 1, 1, 2]) * SS * 0.5
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*PAPER, rnd.randint(26, 110)))
    return layer


def crescent(size, cx, cy, r, colour):
    """A crescent cut with alpha, not with a disc of background colour —
    punching the shadow with a flat fill leaves a visible dark circle over a
    gradient."""
    mask = Image.new("L", size, 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    off, lift = int(r * 0.46), int(r * 0.10)
    md.ellipse([cx - r + off, cy - r - lift, cx + r + off, cy + r - lift], fill=0)
    moon = Image.new("RGBA", size, (*colour, 0))
    moon.putalpha(mask)
    return moon


def beams(layer, cx, cy, r0, r1, colour, n=7, spread=126, tilt=-150, width=5):
    """IR beams that fade along their length. A flat full-length line reads as
    a scratch on the image; the point is light bleeding away into the dark."""
    d = ImageDraw.Draw(layer)
    steps = 26
    for i in range(n):
        a = math.radians(tilt - spread / 2 + spread * i / max(1, n - 1))
        ca, sa = math.cos(a), math.sin(a)
        for s in range(steps):
            t0, t1 = s / steps, (s + 1) / steps
            fade = (1 - t0) ** 2.4
            alpha = int(120 * fade)
            if alpha <= 2:
                break
            d.line([cx + ca * (r0 + (r1 - r0) * t0), cy + sa * (r0 + (r1 - r0) * t0),
                    cx + ca * (r0 + (r1 - r0) * t1), cy + sa * (r0 + (r1 - r0) * t1)],
                   fill=(*colour, alpha), width=max(1, int(width * (0.45 + 0.55 * fade))))


def art_mask(size, x_clear, x_full, y_fade, y_gone):
    """Where the background art is allowed to be seen.

    Two ramps multiplied together. Horizontally it is invisible across the type
    column and reaches full strength by the device panel, so the beams stop
    scratching across the wordmark. Vertically it dies out above the footer
    rule, because the eye's arc sweeps through the bottom of its circle and was
    running straight over the licence line and making it unreadable.

    Masking the art is better than shrinking it: the ring wants to be large and
    bleed, it just must not compete with anything that has to be read.
    """
    w, h = size
    horiz = Image.new("L", (w, 1))
    hp = horiz.load()
    for x in range(w):
        if x <= x_clear:
            hp[x, 0] = 0
        elif x >= x_full:
            hp[x, 0] = 255
        else:
            t = (x - x_clear) / float(x_full - x_clear)
            hp[x, 0] = int(255 * (t * t * (3 - 2 * t)))
    horiz = horiz.resize((w, h))

    vert = Image.new("L", (1, h))
    vp = vert.load()
    for y in range(h):
        if y <= y_fade:
            vp[0, y] = 255
        elif y >= y_gone:
            vp[0, y] = 0
        else:
            t = (y - y_fade) / float(y_gone - y_fade)
            vp[0, y] = int(255 * (1 - t * t * (3 - 2 * t)))
    vert = vert.resize((w, h))

    return ImageChops.multiply(horiz, vert)


def ring_point(cx, cy, radius, deg):
    a = math.radians(deg - 90)
    return cx + math.cos(a) * radius, cy + math.sin(a) * radius


def eye(layer, cx, cy, r, level=76, peak=88, alpha=255):
    """The sweep screen's eye gauge, blown up.

    Same parts in the same order as views/sweep_view.c: outer ring, a dotted arc
    filling clockwise from 12 o'clock with the live level, a tick at the peak
    reading, the iris, a pupil dilated by the level, and the lock-on glare
    spikes. Keeping them in step means the art cannot drift from the product.
    """
    d = ImageDraw.Draw(layer)
    A = lambda a: int(a * alpha / 255)  # noqa: E731

    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*VIOLET, A(210)), width=int(2.2 * SS))

    deg = 0.0
    while deg < level * 360 / 100:
        for rr in (r * 0.90, r * 0.83):
            x, y = ring_point(cx, cy, rr, deg)
            dot = rr * 0.035
            d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=(*VIOLET, A(255)))
        deg += 3.2

    xi, yi = ring_point(cx, cy, r * 0.78, peak * 360 / 100)
    xo, yo = ring_point(cx, cy, r * 1.08, peak * 360 / 100)
    d.line([xi, yi, xo, yo], fill=(*PAPER, A(245)), width=int(3.2 * SS))

    ir = r * 0.58
    d.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], outline=(*IRRED, A(235)), width=int(2.6 * SS))
    d.ellipse([cx - ir * .92, cy - ir * .92, cx + ir * .92, cy + ir * .92], fill=(*IRRED, A(42)))
    pr = ir * 0.52
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=(9, 6, 14, A(255)))
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], outline=(*IRRED, A(180)), width=int(1.4 * SS))

    gl = pr * 0.34
    gx, gy = cx - pr * 0.36, cy - pr * 0.40
    d.ellipse([gx - gl, gy - gl, gx + gl, gy + gl], fill=(*PAPER, A(225)))

    for i in range(6):
        deg = i * 60 + 12
        x1, y1 = ring_point(cx, cy, ir * 1.18, deg)
        x2, y2 = ring_point(cx, cy, ir * 1.42, deg)
        d.line([x1, y1, x2, y2], fill=(*IRRED, A(200)), width=int(2.0 * SS))


def device_panel(capture_path, scale):
    """The real capture, in the panel's own amber, with a thin bezel.

    A 1-bit framebuffer is rendered at INTEGER scale with nearest-neighbour so
    the pixels stay pixels; resampling a 128x64 capture is how a screenshot
    starts looking like a drawing of a screenshot.
    """
    src = Image.open(capture_path).convert("1")
    px = src.load()
    lcd = Image.new("RGB", (128, 64), LCD_ON)
    lp = lcd.load()
    for y in range(64):
        for x in range(128):
            if px[x, y] == 0:
                lp[x, y] = LCD_INK
    lcd = lcd.resize((128 * scale, 64 * scale), Image.NEAREST)

    pad = int(7 * SS)
    panel = Image.new("RGBA", (lcd.width + pad * 2, lcd.height + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle([0, 0, panel.width - 1, panel.height - 1], radius=int(9 * SS),
                        fill=(30, 18, 4, 255), outline=(*LCD_ON, 150), width=max(1, SS))
    panel.paste(lcd, (pad, pad))
    return panel


def tracked(d, xy, text, f, fill, track=0):
    """Manual letter-spacing; PIL has no tracking of its own."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + track
    return x


def render(path, W, H, capture_scale, title_px, enforce_safe):
    w, h = W * SS, H * SS
    safe = SAFE * SS if enforce_safe else int(34 * SS)
    box = SafeBox(w, h, safe)

    img = sky(w, h).convert("RGBA")
    img.alpha_composite(starfield((w, h), int(w * h / 26000)))

    # ---- geometry first, so the art can be masked away from the content ----
    panel = device_panel(os.path.join(SHOTS, "sweep_alarm.png"), capture_scale * SS)
    px_ = w - safe - panel.width
    py_ = (h - panel.height) // 2 - int(14 * SS)

    f_foot_probe = font(MONO, int(18 * SS))
    foot_ink = ImageDraw.Draw(img).textbbox((0, 0), "Ag", font=f_foot_probe)
    footer_text_y = h - safe - foot_ink[3]
    footer_rule_y = footer_text_y - int(14 * SS)

    # ---- background art: registered nowhere, free to bleed, but masked ----
    art = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ex, ey = int(w * 0.80), py_ + panel.height // 2
    R = int(min(w, h) * 0.46)
    beams(art, ex, ey, int(R * 1.1), int(R * 3.0), IRRED, n=7, spread=128, tilt=-150)
    art.alpha_composite(crescent((w, h), int(w * 0.95), int(h * 0.15), int(h * 0.072), VIOLET))
    eye(art, ex, ey, R, alpha=78)

    mask = art_mask((w, h), x_clear=int(px_ - 150 * SS), x_full=px_ - int(20 * SS),
                    y_fade=footer_rule_y - int(56 * SS), y_gone=footer_rule_y - int(6 * SS))
    art.putalpha(ImageChops.multiply(art.getchannel("A"), mask))

    img.alpha_composite(art.filter(ImageFilter.GaussianBlur(6 * SS)))
    img.alpha_composite(art)

    # ---- the real capture: information, so it is registered ----
    img.alpha_composite(panel, (px_, py_))
    box.add(px_, py_, px_ + panel.width, py_ + panel.height, "device capture")

    tx = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tx)

    f_kick = font(MONO, int(19 * SS))
    f_title = font(DIDOT, int(title_px * SS), index=2)
    f_tag = font(AVENIR, int(31 * SS), index=5)
    f_sub = font(AVENIR, int(21 * SS), index=7)
    f_foot = font(MONO, int(18 * SS))
    f_cap = font(MONO, int(15 * SS))

    # caption under the capture, naming it as evidence
    cap = "REAL CAPTURE — 98%  PULSED  STRONG"
    cw = td.textlength(cap, font=f_cap)
    # Centre it under the panel, but never outside the safe box: on the shorter
    # banner the caption is wider than the panel it labels, and centring alone
    # walks it off the right edge.
    cx_ = px_ + (panel.width - cw) / 2
    cx_ = min(max(cx_, safe), w - safe - cw)
    cy_ = py_ + panel.height + int(16 * SS)
    td.text((cx_, cy_), cap, font=f_cap, fill=GOLD)
    box.add(int(cx_), cy_, int(cx_ + cw), cy_ + int(19 * SS), "capture caption")

    # ---- the type column ----
    x0 = safe
    col_w = px_ - x0 - int(48 * SS)

    # Centre the whole type block between the top of the safe box and the footer
    # rule, rather than hanging it from the top. Measuring the wordmark's real
    # ink box first is the only way to know the block's height: Didot's ascent
    # is nothing like its cap height, so a guessed line height leaves the card
    # visibly top-heavy with a dead zone above the footer.
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    title_ink = probe.textbbox((0, 0), "NYX", font=f_title)
    blk_title_top = int(30 * SS)
    blk_rule = blk_title_top + title_ink[3] + int(20 * SS)
    blk_tag = blk_rule + int(22 * SS)
    blk_sub = blk_tag + int(46 * SS)
    block_h = blk_sub + int(24 * SS)

    avail_top, avail_bot = safe, footer_rule_y - int(24 * SS)
    kick_y = max(safe, avail_top + ((avail_bot - avail_top) - block_h) // 2)
    end_x = tracked(td, (x0, kick_y), "FLIPPER ZERO — INFRARED", f_kick, VIOLET, track=2.4 * SS)
    box.add(x0, kick_y, int(end_x), kick_y + int(20 * SS), "eyebrow")

    title_y = kick_y + blk_title_top
    ghost = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ghost).text((x0, title_y), "NYX", font=f_title, fill=(*IRRED, 170))
    tx.alpha_composite(ghost.filter(ImageFilter.GaussianBlur(5 * SS)))
    td.text((x0, title_y), "NYX", font=f_title, fill=PAPER)
    tb = td.textbbox((x0, title_y), "NYX", font=f_title)
    box.add(tb[0], tb[1], tb[2], tb[3], "wordmark")

    rule_y = kick_y + blk_rule
    td.line([(x0, rule_y), (x0 + int(108 * SS), rule_y)], fill=GOLD, width=int(2 * SS))
    box.add(x0, rule_y, x0 + int(108 * SS), rule_y + int(2 * SS), "gold rule")

    tag_y = kick_y + blk_tag
    tag = "See the light they hoped you couldn't."
    td.text((x0, tag_y), tag, font=f_tag, fill=PAPER)
    box.add(x0, tag_y, int(x0 + td.textlength(tag, font=f_tag)), tag_y + int(34 * SS), "tagline")

    sub_y = kick_y + blk_sub
    sub = "Hidden-camera sweep. Honest about what it cannot see."
    td.text((x0, sub_y), sub, font=f_sub, fill=GRAY)
    box.add(x0, sub_y, int(x0 + td.textlength(sub, font=f_sub)), sub_y + int(24 * SS), "sub copy")

    img.alpha_composite(tx)

    # ---- footer, pinned to the BOTTOM OF THE SAFE BOX, not the canvas ----
    fd = ImageDraw.Draw(img)
    url = "github.com/at0m-b0mb/Nyx-FlipperZero"
    right = "MIT — at0m-b0mb"

    # Position from the BOTTOM OF THE SAFE BOX upward, and measure the glyphs
    # rather than assuming a line height. Deriving the footer from the canvas
    # height is how a card ends up with a 36px bottom margin that looks fine in
    # the file and is cropped away on Discord.
    text_y, rule_y = footer_text_y, footer_rule_y
    fd.line([(x0, rule_y), (w - safe, rule_y)], fill=RULE, width=max(1, int(1.4 * SS)))
    fd.text((x0, text_y), url, font=f_foot, fill=GRAY)
    box.add(*fd.textbbox((x0, text_y), url, font=f_foot), "footer url")

    rw = fd.textlength(right, font=f_foot)
    fd.text((w - safe - rw, text_y), right, font=f_foot, fill=VIOLET_DIM)
    box.add(*fd.textbbox((w - safe - rw, text_y), right, font=f_foot), "footer licence")

    if col_w < td.textlength(tag, font=f_tag):
        raise AssertionError("the tagline is wider than the type column; shrink it or the capture")

    got = box.check()
    img.convert("RGB").resize((W, H), Image.LANCZOS).save(path)
    print(f"wrote {path}  ({W}x{H}, tightest registered margin "
          f"{got[0] // SS}px on '{got[1]}', needs {safe // SS}px)")


if __name__ == "__main__":
    # The card is the one with GitHub's hard geometry; the README banner is
    # shown whole, so it only needs to look tidy.
    render(os.path.join(OUT, "social-preview.png"), 1280, 640,
           capture_scale=3, title_px=104, enforce_safe=True)
    render(os.path.join(OUT, "banner.png"), 1280, 400,
           capture_scale=2, title_px=84, enforce_safe=False)
