#!/usr/bin/env python3
"""Capture real Flipper Zero screens over the serial RPC session.

The Flipper's own CLI has no screenshot command on current firmware, but the
protobuf RPC session does expose the framebuffer and an input injector, so this
drives the app and grabs frames from the device itself. No mockups.

    python3 tools_screenshot.py --all        # drive Nyx and capture every screen
    python3 tools_screenshot.py --shot menu  # grab whatever is on screen right now
    python3 tools_screenshot.py --sheet      # rebuild images/screens.png from shots

Only four message shapes are needed, so the protobuf is encoded by hand rather
than pulling in a generated stub. From flipper.proto:

    Main { command_id = 1, command_status = 2, has_next = 3, oneof content }
      16 = App.StartRequest { string name = 1, string args = 2 }
      19 = StopSession {}
      20 = Gui.StartScreenStreamRequest {}
      21 = Gui.StopScreenStreamRequest {}
      22 = Gui.ScreenFrame { bytes data = 1 }
      23 = Gui.SendInputEventRequest { key = 1, type = 2 }

Screenshots come out at the panel's native 128x64 (what the Flipper app catalog
wants) plus a 4x nearest-neighbour upscale for the README.
"""
import argparse
import glob
import os
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is required:  python3 -m pip install pyserial")
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "screenshots")
IMAGES = os.path.join(HERE, "images")

KEY = {"up": 0, "down": 1, "right": 2, "left": 3, "ok": 4, "back": 5}
TYPE = {"press": 0, "release": 1, "short": 2, "long": 3, "repeat": 4}

F_APP_START = 16
F_STOP_SESSION = 19
F_START_STREAM = 20
F_STOP_STREAM = 21
F_SCREEN_FRAME = 22
F_INPUT = 23


# ---------------------------------------------------------------- protobuf ---
def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def read_varint(buf, i):
    shift = val = 0
    while True:
        if i >= len(buf):
            return None, i
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def tag(field, wire=2):
    return varint((field << 3) | wire)


# ------------------------------------------------------------------ device ---
def find_port(explicit=None):
    if explicit:
        return explicit
    found = sorted(glob.glob("/dev/cu.usbmodemflip_*")) or sorted(
        glob.glob("/dev/ttyACM*")
    )
    if not found:
        sys.exit(
            "No Flipper found on USB.\n"
            "Plug it in and make sure nothing else holds the port "
            "(qFlipper and the Flipper mobile app both do)."
        )
    return found[0]


class Flipper:
    def __init__(self, port=None):
        self.s = serial.Serial(find_port(port), timeout=1.0)
        self.buf = b""
        self.cmd = 0
        self._to_cli()
        # Terminate with a bare CR and consume the echo *exactly*. Sending
        # "\r\n" leaves a stray newline in the device's input buffer, and the
        # RPC decoder reads it as the start of a frame (0x0a = "a 10-byte
        # message follows"), swallowing the first real request and leaving the
        # session permanently desynced — every reply comes back ERROR_DECODE.
        self.s.write(b"start_rpc_session\r")
        self.s.read_until(b"start_rpc_session\r\n")

    # -- session plumbing --
    def _drain(self, seconds=0.5):
        out = b""
        t0 = time.time()
        while time.time() - t0 < seconds:
            if self.s.in_waiting:
                out += self.s.read(self.s.in_waiting)
            else:
                time.sleep(0.03)
        return out

    def _to_cli(self):
        """Get back to a CLI prompt from any state.

        A session left open by an earlier run swallows plain text, so the first
        move is always to close one that may or may not exist.
        """
        for _ in range(4):
            self.send(F_STOP_SESSION)
            self._drain(0.4)
            self.s.write(b"\r")
            if b">:" in self._drain(0.6):
                self._drain(0.2)
                return
        sys.exit("Could not get the Flipper back to a CLI prompt — try replugging it.")

    def send(self, field, body=b""):
        self.cmd += 1
        msg = tag(1, 0) + varint(self.cmd) + tag(field) + varint(len(body)) + body
        self.s.write(varint(len(msg)) + msg)

    def _next_message(self, timeout=2.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            ln, i = read_varint(self.buf, 0)
            if ln is not None and len(self.buf) >= i + ln:
                msg = self.buf[i : i + ln]
                self.buf = self.buf[i + ln :]
                return msg
            if self.s.in_waiting:
                self.buf += self.s.read(self.s.in_waiting)
            else:
                time.sleep(0.02)
        return None

    # -- screen --
    def start_stream(self):
        self.send(F_START_STREAM)
        time.sleep(0.3)

    def stop_stream(self):
        self.send(F_STOP_STREAM)

    def flush(self):
        """Throw away frames already in flight.

        The device pushes a frame on every redraw, so by the time a key press
        has been delivered there are several older frames queued. Without this
        a capture returns the screen as it was one or two steps ago.
        """
        # Drain whole MESSAGES, never raw bytes. Clearing the byte buffer
        # mid-message throws away a length prefix, and everything after it is
        # then parsed at the wrong offset — the stream never resynchronises and
        # every later capture times out. An animating screen streams forever, so
        # the loop is also bounded in wall-clock time.
        deadline = time.time() + 0.9
        while time.time() < deadline:
            if self._await_frame(0.2) is None:
                break

    def frame(self, timeout=3.0, nudge=True):
        """The 1024-byte framebuffer from the next ScreenFrame message.

        The device only pushes a frame when the screen actually *redraws*. Nyx's
        sweep, probe and splash views animate on a tick so they stream happily,
        but the menu, Settings and About are static and would otherwise time out
        with nothing sent. `nudge` walks the selection down and back up, which
        forces two repaints and lands on the row it started from — non-destructive
        on a submenu, a variable item list and a scrolling text widget alike.
        """
        data = self._await_frame(timeout)
        if data is None and nudge:
            self.press("down", settle=0.25)
            self.press("up", settle=0.25)
            data = self._await_frame(timeout)
        if data is None:
            return None
        # Keep draining briefly and return the NEWEST frame. The first frame to
        # arrive is often mid-transition — after a nudge it is the screen with
        # the selection moved down, before the matching "up" has been drawn.
        deadline = time.time() + 0.45
        while time.time() < deadline:
            newer = self._await_frame(0.25)
            if newer is None:
                break
            data = newer
        return data

    def _await_frame(self, timeout):
        t0 = time.time()
        while time.time() - t0 < timeout:
            msg = self._next_message(1.2)
            if msg is None:
                continue
            i = 0
            while i < len(msg):
                fw, i = read_varint(msg, i)
                if fw is None:
                    break
                fnum, wire = fw >> 3, fw & 7
                if wire == 0:
                    v, i = read_varint(msg, i)
                    if v is None:
                        break
                elif wire == 2:
                    ln, i = read_varint(msg, i)
                    if ln is None:
                        break
                    body, i = msg[i : i + ln], i + ln
                    if fnum == F_SCREEN_FRAME:
                        j = 0
                        while j < len(body):
                            fw2, j = read_varint(body, j)
                            if fw2 is None:
                                break
                            f2, w2 = fw2 >> 3, fw2 & 7
                            if w2 == 2:
                                l2, j = read_varint(body, j)
                                if l2 is None:
                                    break
                                data, j = body[j : j + l2], j + l2
                                if f2 == 1:
                                    return data
                            elif w2 == 0:
                                _, j = read_varint(body, j)
                            else:
                                break
                else:
                    break
        return None

    # -- input --
    def app_start(self, path, args_str=""):
        """App.StartRequest { string name = 1; string args = 2 }.

        Launching this way rather than through `loader open` on the CLI means the
        screen stream stays up across the launch, which is the only way to catch
        the 1.6 s boot intro.
        """
        name = path.encode()
        body = tag(1) + varint(len(name)) + name
        if args_str:
            a = args_str.encode()
            body += tag(2) + varint(len(a)) + a
        self.send(F_APP_START, body)
        time.sleep(0.15)

    def _key(self, name, kind):
        body = tag(1, 0) + varint(KEY[name]) + tag(2, 0) + varint(TYPE[kind])
        self.send(F_INPUT, body)

    def press(self, name, long=False, settle=0.45):
        """A real press is press -> short|long -> release, same as the hardware."""
        self._key(name, "press")
        time.sleep(0.4 if long else 0.03)
        self._key(name, "long" if long else "short")
        time.sleep(0.03)
        self._key(name, "release")
        time.sleep(settle)

    def close(self):
        try:
            self.stop_stream()
            time.sleep(0.2)
            self.send(F_STOP_SESSION)
            time.sleep(0.2)
        finally:
            self.s.close()


# ------------------------------------------------------------------- image ---
def to_image(data):
    """1024 bytes: 8 pages of 128 columns, LSB = topmost pixel of the page."""
    img = Image.new("1", (128, 64), 1)
    px = img.load()
    for i, byte in enumerate(data[:1024]):
        x, page = i % 128, i // 128
        for bit in range(8):
            if byte & (1 << bit):
                px[x, page * 8 + bit] = 0
    return img


def amber(img, scale=1):
    """Render a 1-bit frame the way the device actually looks."""
    out = Image.new("RGB", (128, 64), LCD_ON)
    px, src = out.load(), img.convert("1").load()
    for y in range(64):
        for x in range(128):
            if src[x, y] == 0:
                px[x, y] = LCD_INK
    if scale > 1:
        out = out.resize((128 * scale, 64 * scale), Image.NEAREST)
    return out


def save(img, name, scale=4):
    os.makedirs(SHOTS, exist_ok=True)
    native = os.path.join(SHOTS, f"{name}.png")
    img.save(native)  # 1-bit, for the app catalog
    amber(img, scale).save(os.path.join(SHOTS, f"{name}@{scale}x.png"))
    print(f"  saved {name}.png (128x64, 1-bit) + @{scale}x amber")
    return native


# ------------------------------------------------------------------- sheet ---
# The Flipper's LCD is a monochrome panel behind an amber backlight, so a plain
# black-on-white capture does not look like the thing in your hand. Everything
# meant for a human (the upscales, the contact sheet, the GIFs) is rendered in
# that amber; the native 128x64 files stay 1-bit black and white, because that
# is what the Flipper app catalog's validator expects.
LCD_ON = (255, 130, 0)  # lit pixel — Flipper amber
LCD_INK = (17, 10, 0)  # unlit pixel — near black, warmed slightly

SKY = (14, 10, 24)
VIOLET = (159, 122, 255)
PAPER = (238, 236, 248)
GRAY = (150, 146, 172)


def contact_sheet(names, out=None, cols=4, scale=3):
    """Compose the captured shots into the README's screens.png."""
    out = out or os.path.join(IMAGES, "screens.png")
    tiles = []
    for name, label in names:
        p = os.path.join(SHOTS, f"{name}.png")
        if not os.path.exists(p):
            print(f"  (skipping {name} — not captured)")
            continue
        tiles.append((amber(Image.open(p)), label))
    if not tiles:
        sys.exit("No screenshots to compose — run --all first.")

    tw, th = 128 * scale, 64 * scale
    pad, cap, gut = 16, 26, 18
    rows = (len(tiles) + cols - 1) // cols
    W = pad * 2 + cols * tw + (cols - 1) * gut
    H = pad * 2 + rows * (th + cap) + (rows - 1) * gut

    sheet = Image.new("RGB", (W, H), SKY)
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Andale Mono.ttf", 15)
    except OSError:
        f = ImageFont.load_default()

    for i, (tile, label) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (tw + gut)
        y = pad + r * (th + cap + gut)
        d.rectangle([x - 1, y - 1, x + tw, y + th], outline=(92, 52, 8))
        sheet.paste(tile.resize((tw, th), Image.NEAREST), (x, y))
        d.text((x, y + th + 7), label, font=f, fill=GRAY)

    sheet.save(out)
    print(f"wrote {out}  ({W}x{H}, {len(tiles)} screens)")


# -------------------------------------------------------------------- drive ---
# Each step: (name, caption, keys to press to get there from the previous shot)
# Each step: (name, caption, keys pressed to get there from the previous shot).
# The start scene stores the row you activated, so Back returns the cursor to it
# and exactly one Down is needed to reach the next entry — not a running count.
TOUR = [
    ("menu", "Main menu", []),
    ("sweep", "Sweep — scanning", [("ok", False)]),
    ("sweep_sens", "Sweep — sensitivity, live", [("left", False)]),
    ("probe_wiring", "Probe Setup — wiring", [("back", False), ("down", False), ("ok", False)]),
    ("probe_check", "Probe Setup — live check", [("right", False)]),
    ("settings", "Settings", [("back", False), ("down", False), ("ok", False)]),
    ("about", "About", [("back", False), ("down", False), ("ok", False)]),
]


FAP_PATH = "/ext/apps/Infrared/nyx.fap"


def restart_app(f, fap=FAP_PATH):
    """Put the device in a known state: a freshly launched Nyx on its main menu.

    The tour navigates by relative key presses, so it only lines up if it starts
    from a known screen with a known menu cursor. Backing out and relaunching
    over RPC gives that, and unlike a CLI launch it does not require closing the
    screen stream — so the intro can be captured on the way past.
    """
    for _ in range(6):
        f.press("back", settle=0.2)
    time.sleep(0.5)
    f.app_start(fap)

    # The intro runs ~1.6 s. Wait long enough that the app has actually painted
    # (the very first frame after the launch request is still the desktop), but
    # not so long that the eye has finished opening.
    time.sleep(0.8)
    data = f.frame(timeout=2.0, nudge=False)
    if data is not None:
        save(to_image(data), "splash")

    time.sleep(2.0)  # let the intro finish and the menu settle
    f.flush()


def alarm_band_lit(img):
    """True when the status strip is inverted, i.e. an emitter is locked on.

    The sweep screen fills rows 53..63 solid and draws the text knocked out in
    white, so a majority-ink bottom band is a reliable signal that the alarm
    state is on screen — no need to guess at timing.
    """
    px = img.load()
    ink = sum(1 for y in range(STRIP_TOP, 64) for x in range(128) if px[x, y] == 0)
    return ink > (128 * (64 - STRIP_TOP)) * 0.55


STRIP_TOP = 53


def wait_for_alarm(f, name="sweep_alarm", seconds=60):
    """Sit on the sweep screen until something actually emits, then capture.

    Waiting for the real thing beats staging one: the strip only inverts when
    the engine says present, so whatever this catches is a genuine detection.
    """
    print(f"  watching the sweep for a detection (up to {seconds}s) ...")
    deadline = seconds * 4
    best = None
    for i in range(int(deadline)):
        data = f.frame(timeout=1.5, nudge=False)
        if data is None:
            continue
        img = to_image(data)
        if alarm_band_lit(img):
            best = img
            print(f"  detection seen after ~{i / 4:.0f}s")
            break
    if best is None:
        print("  !! nothing detected — no alarm frame captured")
        return None
    save(best, name)
    return best


def run_tour(f, only=None):
    shots = []
    for name, caption, keys in TOUR:
        if only and name not in only:
            continue
        for key, is_long in keys:
            f.press(key, long=is_long)
        time.sleep(0.6)
        f.flush()
        data = f.frame()
        if data is None:
            print(f"  !! no frame for {name}")
            continue
        save(to_image(data), name)
        shots.append((name, caption))
    return shots


def record(f, name, seconds=6.0, fps=10):
    """Grab a run of frames and write them out as an animated GIF.

    Real device frames, not a re-render: the eye ring, the trend arrow and the
    alarm strip animate on the Flipper's own 100 ms tick, so the GIF shows the
    actual instrument rather than an approximation of it.
    """
    frames = []
    period = 1.0 / fps
    deadline = seconds * fps
    print(f"  recording ~{seconds:.0f}s at {fps}fps ...")
    while len(frames) < deadline:
        data = f.frame(timeout=1.5, nudge=False)
        if data is None:
            break
        frames.append(to_image(data))
        time.sleep(period * 0.35)
    if not frames:
        print("  !! no frames captured")
        return None

    # drop consecutive duplicates so a static stretch does not pad the file
    deduped = [frames[0]]
    for fr in frames[1:]:
        if fr.tobytes() != deduped[-1].tobytes():
            deduped.append(fr)

    os.makedirs(IMAGES, exist_ok=True)
    out = os.path.join(IMAGES, f"{name}.gif")
    big = [amber(fr, 3) for fr in deduped]
    big[0].save(
        out,
        save_all=True,
        append_images=big[1:],
        duration=int(1000 / fps),
        loop=0,
        optimize=True,
    )
    print(f"  wrote {out}  ({len(big)} unique frames of {len(frames)} captured)")
    return out


# What goes on the README sheet, in reading order.
SHEET = [
    ("splash", "Boot intro"),
    ("menu", "Main menu"),
    ("sweep", "Sweep — closing in"),
    ("sweep_alarm", "Sweep — locked on"),
    ("probe_wiring", "Probe Setup — wiring"),
    ("probe_check", "Probe Setup — live check"),
    ("settings", "Settings"),
    ("about", "About"),
]


def tour_gif(f, name="nyx-demo", fps=10, scale=3):
    """Record one continuous GIF of a scripted walk through the app.

    Two deliberate choices. Frames are collected *between* key presses rather
    than after them, so transitions and animations end up in the recording — it
    is a screen capture of the device being used, not a slideshow of stills.
    And the walk never opens About, because that screen prints the version
    number: leaving it out means the same GIF stays accurate across releases
    instead of needing a re-shoot every time the version moves.

    It also ends back on the main menu, so the loop closes where it opened.
    """
    frames = []

    def grab(seconds):
        end = time.time() + seconds
        while time.time() < end:
            d = f._await_frame(0.6)
            if d is not None:
                frames.append(to_image(d))

    for _ in range(6):
        f.press("back", settle=0.2)
    time.sleep(0.4)
    # Everything captured while backing out is still queued. Without this the
    # recording opens on the *previous* session's screen, which also defeats the
    # ink test below — a stale menu frame is just as unlit as a fresh one.
    f.flush()
    f.app_start(FAP_PATH)
    # The launch request returns before the app paints, so the first frames off
    # the wire are still the Flipper desktop. Counting frames is unreliable —
    # how many arrive depends on how busy the device is — so discard by content
    # instead: the desktop's dolphin art is a large dark scene, while every Nyx
    # screen is mostly unlit. Anything more than 40% ink is not us yet.
    for _ in range(20):
        d = f._await_frame(0.4)
        if d is None:
            continue
        px = to_image(d).load()
        ink = sum(1 for y in range(0, 64, 2) for x in range(0, 128, 2) if px[x, y] == 0)
        if ink < (64 * 32) * 0.40:
            break

    grab(4.0)                       # the intro, then the menu settles
    f.press("ok", settle=0.1)
    grab(6.0)                       # the sweep — still the longest beat
    f.press("left", settle=0.1)
    grab(2.0)                       # sensitivity drops, live
    f.press("right", settle=0.1)
    f.press("right", settle=0.1)
    grab(2.4)                       # and back up again
    f.press("back", settle=0.1)
    f.press("down", settle=0.1)
    f.press("ok", settle=0.1)
    grab(3.0)                       # probe wiring
    f.press("right", settle=0.1)
    grab(3.4)                       # probe live check
    f.press("back", settle=0.1)
    f.press("down", settle=0.1)
    f.press("ok", settle=0.1)
    grab(2.4)                       # settings
    f.press("down", settle=0.5)
    f.press("down", settle=0.5)
    f.press("down", settle=0.5)
    grab(2.2)
    f.press("back", settle=0.1)
    grab(2.4)                       # home again, so the loop closes cleanly

    if not frames:
        print("  !! nothing recorded")
        return None
    # Hold a static screen with per-frame DURATION, not repeated frames.
    #
    # Repeating a frame does not survive encoding: Pillow's GIF optimiser
    # collapses identical consecutive frames again on the way out, so a page
    # with nothing moving on it flashes past in one tick however many copies
    # were handed to it. Timing each unique frame by how long it was actually on
    # screen is both what we mean and a smaller file.
    runs = []
    for fr in frames:
        if runs and fr.tobytes() == runs[-1][0].tobytes():
            runs[-1][1] += 1
        else:
            runs.append([fr, 1])

    tick = 1000.0 / fps
    # Cap the hold so a long idle stretch cannot stall the loop.
    durations = [max(int(tick), min(int(count * tick), 2200)) for _, count in runs]

    os.makedirs(IMAGES, exist_ok=True)
    out = os.path.join(IMAGES, f"{name}.gif")
    big = [amber(fr, scale) for fr, _ in runs]
    big[0].save(out, save_all=True, append_images=big[1:],
                duration=durations, loop=0, optimize=True)
    total = sum(durations) / 1000.0
    print(f"  wrote {out}  ({len(big)} unique frames, "
          f"~{total:.0f}s of playback, {os.path.getsize(out)//1024} KB)")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: autodetect)")
    ap.add_argument("--all", action="store_true", help="drive Nyx and capture every screen")
    ap.add_argument("--shot", metavar="NAME", help="capture whatever is on screen right now")
    ap.add_argument("--sheet", action="store_true", help="rebuild images/screens.png from screenshots/")
    ap.add_argument("--record", metavar="NAME", help="record an animated GIF of the live screen")
    ap.add_argument("--seconds", type=float, default=6.0, help="recording length (default 6)")
    ap.add_argument("--fps", type=int, default=10, help="recording frame rate (default 10)")
    ap.add_argument("--launch", metavar="FAP", help="start this app over RPC first (catches the intro)")
    ap.add_argument("--alarm", action="store_true", help="open Sweep and wait for a real detection")
    ap.add_argument("--tour-gif", action="store_true", help="record a GIF of a scripted walk through the app")
    args = ap.parse_args()

    if args.sheet and not (args.all or args.shot):
        contact_sheet(SHEET)
        return

    if not (args.all or args.shot or args.record or args.alarm or args.tour_gif):
        ap.print_help()
        return

    f = Flipper(args.port)
    try:
        f.start_stream()
        if args.launch:
            f.app_start(args.launch)
        if args.tour_gif:
            print("  recording the tour — wave a TV remote at it during the sweep")
            tour_gif(f)
        elif args.alarm:
            restart_app(f, args.launch or FAP_PATH)
            f.press("ok", settle=1.0)  # menu -> Sweep
            if args.record:
                record(f, args.record, args.seconds, args.fps)
            else:
                wait_for_alarm(f, seconds=int(args.seconds) if args.seconds > 6 else 60)
        elif args.record:
            record(f, args.record, args.seconds, args.fps)
        elif args.shot:
            if args.launch:
                # Catching the intro means taking the FIRST frames after the
                # launch, so no flush and no settling delay here.
                time.sleep(0.45)
            else:
                time.sleep(0.4)
                f.flush()
            data = f.frame()
            if data is None:
                sys.exit("No frame received. Is the screen updating?")
            save(to_image(data), args.shot)
        else:
            print("Restarting Nyx over RPC so the tour starts from a known screen...")
            restart_app(f, args.launch or FAP_PATH)
            run_tour(f)
            contact_sheet(SHEET)
    finally:
        f.close()


if __name__ == "__main__":
    main()
