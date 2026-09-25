# Changelog

## v1.2

A usability and honesty pass. The engine is unchanged; what changed is whether
the thing responds to you, whether the screen tells the truth about itself, and
whether it loads on your firmware.

### Fixed
- **The detector chattered, and over-claimed while doing it.** Presence was a
  bare threshold on a single 100 ms window, so a source that is physically
  bursty — which every source is, through a demodulating receiver — toggled the
  verdict several times a second: the alarm strip flashed, the tone stuttered,
  and the hit count ran into the dozens while nothing had moved. Measured with a
  dome camera's illuminator held directly in front of the receiver, 143 of 155
  windows read exactly zero and four read 10–15% of full scale, and each of
  those four blips became its own "IR EMITTER" alarm.

  Presence now asks whether activity has *persisted*: one bit per window in a
  shift register, asserted once at least 3 of the last 16 windows clear the
  floor and released only on complete silence. Hysteretic by construction, with
  no timer to tune. On the same camera it now reports **0 detections across 148
  frames** instead of flickering, and a TV remote still locks on solidly and
  reads STRONG — quieter where it should be quiet, not deafer.
- **Presence disagreed with its own meter.** The level reads whichever of edge
  rate or receiver duty cycle is louder, but the present/absent decision only
  ever looked at edge rate. A receiver that holds its output asserted rather
  than toggling — high duty, few edges — filled the ring while the verdict said
  nothing was there. Both now read the same number.
- **The onboard readout called edge transitions "pulses".** Each pulse produces
  two transitions, so the figure was roughly double the real pulse rate. It is
  labelled `edges/s` now, which is what it actually counts.
- **Upgrading from v1.1 silently wiped your settings.** Adding the `intro`
  option changed the size of the saved struct, and `saved_struct` validates
  size, so every v1.1 settings file failed to load and Mode, Sensitivity, probe
  pin and the three alert toggles quietly reset. v1 files are now read through
  their own layout and carried forward.
- **Validation of the saved flags was dead code.** They were `bool`, and a
  `_Bool` is *defined* to hold 0 or 1 — so a "normalise on load" step written
  against one is something the compiler may delete, while a corrupt byte off the
  SD card still reaches a list index. They are plain bytes now, so the clamp is
  real.
- **Screens drew one frame of blank state on entry.** Resetting a view zeroed
  its model, so the sweep header labelled its first frame `HIGH` whatever the
  real sensitivity was, and Probe Setup opened citing a pin that does not exist
  (`p0`). Both are seeded before the first paint.
- **A worker that bailed out could be orphaned.** `ir_sense_start()` guarded on
  the running flag while `ir_sense_stop()` guards on the thread handle, so
  starting after a self-terminated worker overwrote a live `FuriThread`.
- **Two layout collisions found by capturing real device frames, not by
  reading the code**: the Probe Setup page counter and its left chevron
  overlapped on page 2, and the schematic's `ADC` label ran under the column
  divider and lost its last letter. The counter is gone — the page title already
  names the page — and the lead line is shorter.
- **The About screen printed a stray `#` after every heading.** `\e#` sets bold
  until the end of the line, so the closing marker was never a marker; it was
  literal text.
- **The documented default probe pin was wrong.** The README said `PC0`
  (pin 16); the code has always defaulted to the first ADC pin in the SDK's own
  table, which is `PA7` (pin 2). The docs now match the device, and list the
  pins in the order the picker offers them.
- **Sluggish and dropped key presses during a probe sweep.** The probe path
  paces its sample burst with `furi_delay_us()`, which the SDK documents as a
  non-yielding busy-wait, so roughly a third of every 100 ms window was spent
  spinning — at the system-default thread priority, which is the same band the
  view dispatcher runs in. The worker now runs one priority step below the UI,
  so input preempts it instead of queuing behind it.
- **The alarm border shaved the bottom row off its own text.** When an emitter
  locked on, the inner frame's bottom edge landed exactly on the baseline of
  the inverted strip's "IR EMITTER" / proximity text. The border now stops
  above the strip.
- **A dead sweep could still report itself as running.** A worker that could
  not take the IR receiver or the ADC returned without clearing the running
  flag. Stopping is now keyed off the thread handle, so an error exit is honest
  without leaking the thread.
- **Stale state on re-entry.** The sweep, probe and splash views outlive the
  scenes that use them, so re-opening a screen could show a frame of the
  previous visit — the last sweep's mode and level, a peak reading from an
  earlier probe check, or a splash that had already run to the end. Each view
  resets on entry.
- **The About screen described a UI that no longer existed.** It still told you
  to "watch the trace climb" and pointed at a "dotted line", both of which were
  replaced by the eye gauge in v1.1. It now describes the ring, the peak tick
  and the arrow you actually hunt with.
- The sweep header claimed a mode before the worker had resolved one; it shows
  `--` until there is an honest answer.

### Added
- **Sensitivity on the sweep screen.** Left and Right change it live, without
  leaving the screen — the point being that you discover you need it while
  standing in a dark room, not while sitting in the menu. The current setting
  is named in the header, so a blank screen on Low is explainable rather than
  mysterious. Changing it zeroes the peak and hit count, because readings taken
  against a different floor are not the same measurement. Held keys repeat, and
  the change is saved when you leave the sweep.
- **Live telemetry where there was a repeated word.** Proximity (FAINT ..
  STRONG) now appears only in the alarm strip; while locked on, the readout row
  that used to repeat it shows the number behind it instead — `+mV` over
  ambient in probe mode, pulses per second in onboard mode. Onboard mode also
  surfaces its pulse rate in the idle hint, which probe mode already did for
  ambient and ripple.
- **Page chevrons and a peak reset on Probe Setup.** The "1/2" counter was the
  only hint that Left/Right did anything; there are now arrows. OK clears the
  peak, so you can re-test with a remote without leaving the screen.
- **`Intro` setting** — turn the boot animation off. It is 1.6 s you sat
  through on every launch of a tool you open and re-open.
- Settings remembers which row you were on.

### Changed
- **A release now ships a build per firmware line.** A `.fap` bakes in the API
  version of the SDK it was built against, and the loader refuses one that runs
  ahead of the firmware. Official / stock firmware tracks the release channel
  (API 87) while Unleashed, RogueMaster and Momentum track dev (API 88), so a
  tag now produces **`nyx.fap`** for stock and **`nyx-fw-dev.fap`** for the
  custom-firmware line. Previously only the release build was attached, and
  custom-firmware users got an API-mismatch warning.
- New banner and social card, built around the app's own eye gauge.
- **A project site** at `docs/`, published via GitHub Pages — the honesty notes,
  the worked example, every screen and the wiring, in light and true-black dark.
- **`tools_screenshot.py`**, which drives the app over the Flipper's protobuf
  RPC session and reads the real framebuffer. Every screenshot and the demo
  animation are captured from hardware and rendered in the panel's own amber.
- Screenshots in the README are now captured from a real Flipper rather than
  rendered mockups.

### Unchanged
- The detection engine, the two-mode model, and every honesty note. Onboard
  mode is still deaf to steady DC illuminators and still says so; Probe mode is
  still the one that finds night-vision cameras. Listen-only — Nyx never
  transmits IR.

## v1.1

Polish pass — same honest dual-path engine, a nicer instrument around it.

### Added
- **Animated boot intro** — a Nyx eye opening through expanding IR wave-rings.
  Runs ~1.6 s; any key skips it.
- **Settings persistence** — Mode, Sensitivity, probe pin, and sound / vibro /
  LED are saved to `/data/settings.bin` and restored on the next launch, via the
  firmware's `saved_struct` (a version bump or corrupt file falls back to
  defaults). New file: `helpers/nyx_store.c`.

### Changed
- **Redesigned Sweep screen** around an **eye that watches back**: the iris ring
  fills clockwise with the live level, the pupil dilates as you close on a
  source, a tick marks your best reading so far, and lock-on glare spikes rotate
  around the iris. A larger get-warmer trend arrow and a cleaner right-hand
  readout (source label · proximity state · peak / hit count) replace the old
  number-and-sparkline layout. The honest "onboard sees pulsed IR only" hint and
  the inverted alarm strip are unchanged.
- README and mock screenshots updated to match; added a splash mockup.

### Unchanged
- Detection engine, the two-mode model, and every honesty note. Onboard mode is
  still deaf to steady DC illuminators and still says so; Probe mode is still the
  one that finds night-vision cameras. Listen-only — Nyx never transmits IR.

## v1.0

Initial release. Dual-path IR emitter sweep for counter-surveillance:

- **Onboard mode** — the built-in TSOP-75338 receiver detects pulsed / modulated
  IR (remotes, beacons, PIR floodlights, PWM'd illuminators). Honest that its
  38 kHz band-pass makes it blind to steady DC illuminators.
- **Probe mode** — an optional ~$1 IR phototransistor on a GPIO ADC pin reads DC
  IR level and classifies the source STEADY / FLICKER / PULSED.
- Sweep meter, on-device probe wiring guide + live check, settings, about.
- Listen-only. `ufbt` + CI verified against Flipper API 87.1.
