#!/usr/bin/env python3
"""Blind vision probe: can the current model serving actually see images?

Motivation (measured): an agent serving can be silently image-blind — the harness
read tool returns "model does not support images" or, worse, image support drifts
mid-session — while the skill's visual-review steps (schematic PDF review, layout
render review, datasheet figure reading) silently degrade to text-only proxies.
One benchmark session recorded a "visually reviewed" claim that could not have
happened. This probe closes that hole with a content round-trip the model cannot
answer from transcript context alone.

Threat boundary — state it honestly: this detects ACCIDENTAL blindness and
hallucinated capability in an instruction-following agent. It is not
tamper-resistant and does not defend against an agent that opens or edits the
`.truth` sidecar; the sidecar sits next to the PNG on purpose, and reading it is a
protocol violation, not an impossibility.

Usage:
  vision_probe.py new [dir]        write probe PNG + truth sidecar; prints ONLY the
                                   PNG path (the truth never enters the transcript)
  vision_probe.py check <png> <digits>
                                   compare the model's reading against the sidecar

The agent workflow: run `new`, read the printed PNG with the harness image/read
tool, then run `check <png> <the digits you saw>`.

Single-use is enforced: `check` claims the sidecar with an atomic rename before
grading, so each probe grades at most once even under concurrent duplicate checks,
a second `check` returns UNVERIFIED, and a FAIL does not reveal the expected
digits. The sidecar also carries the SHA-256 of the PNG, and `check` recomputes
it, so a missing, altered, or swapped PNG (or a sidecar from a different probe)
cannot produce a PASS. Every run prints exactly one verdict line
(PASS/FAIL/UNVERIFIED), help included, so exit-code-only consumers cannot misread
a non-grading run.

Exit codes / verdict lines (fail-closed):
  0  VISION-PROBE-PASS         digits match this PNG's recorded truth
  1  VISION-PROBE-FAIL         digits do not match (image-blind or hallucinated)
  2  VISION-PROBE-UNVERIFIED   probe could not run or could not be trusted
                               (bad arguments, missing/altered PNG, missing or
                               already-consumed sidecar, IO error) — treat exactly
                               like FAIL for gating

If the read tool itself errors ("model does not support images", image omitted),
that is a FAIL for gating purposes; `check` exists for the subtler case where an
image block is returned but the model cannot actually see it.

No third-party dependencies: the PNG is written with zlib/struct only.
"""

import hashlib
import os
import struct
import sys
import zlib

# 3x5 digit bitmaps, rows top-to-bottom, 1 = ink
FONT = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
}
SCALE = 12  # pixels per font cell
MARGIN = 24
GAP = 1  # font columns between digits
CONSUMED = "consumed"


def unverified(msg: str) -> int:
    print("VISION-PROBE-UNVERIFIED: %s" % msg)
    return 2


def render(code: str):
    cols = len(code) * (3 + GAP) - GAP
    w = cols * SCALE + 2 * MARGIN
    h = 5 * SCALE + 2 * MARGIN
    rows = [bytearray([255] * w) for _ in range(h)]
    for i, ch in enumerate(code):
        glyph = FONT[ch]
        x0 = MARGIN + i * (3 + GAP) * SCALE
        for gy in range(5):
            for gx in range(3):
                if glyph[gy][gx] == "1":
                    for py in range(SCALE):
                        y = MARGIN + gy * SCALE + py
                        for px in range(SCALE):
                            rows[y][x0 + gx * SCALE + px] = 0
    return w, h, rows


def png_bytes(w: int, h: int, rows) -> bytes:
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)  # 8-bit grayscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def sidecar_path(png: str) -> str:
    root, ext = os.path.splitext(png)
    return (root if ext.lower() == ".png" else png) + ".truth"


def cmd_new(args) -> int:
    if len(args) > 1:
        return unverified("too many arguments to `new` (want at most a directory)")
    out_dir = args[0] if args else "."
    try:
        code = "".join(str(b % 10) for b in os.urandom(6))
        base = os.path.join(out_dir, "vision-probe-%s" % os.urandom(4).hex())
        data = png_bytes(*render(code))
        with open(base + ".png", "wb") as f:
            f.write(data)
        digest = hashlib.sha256(data).hexdigest()
        with open(base + ".truth", "w") as f:
            f.write("%s %s\n" % (code, digest))
        # Print only the PNG path: the truth must not enter the transcript.
        print(base + ".png")
        return 0
    except Exception as exc:  # noqa: BLE001
        return unverified("could not create probe: %s" % exc)


def cmd_check(args) -> int:
    if len(args) != 2:
        return unverified("`check` wants exactly <png> <digits>")
    png, guess = args[0], args[1].strip()
    truth_path = sidecar_path(png)
    # Claim the probe atomically before any verdict: rename wins exactly once, so
    # concurrent duplicate checks cannot both grade, and a FAIL leaves no reusable
    # probe behind. The claimed file is kept as the audit trail of what was graded.
    claimed = truth_path + ".graded"
    try:
        os.rename(truth_path, claimed)
    except FileNotFoundError:
        return unverified(
            "no fresh truth sidecar — probe already checked or never created; "
            "generate a fresh probe"
        )
    except Exception as exc:  # noqa: BLE001
        return unverified("cannot claim sidecar, refusing to grade: %s" % exc)
    try:
        with open(claimed) as f:
            record = f.read().strip()
    except Exception as exc:  # noqa: BLE001
        return unverified("cannot read claimed sidecar: %s" % exc)
    try:  # breadcrumb for a clearer message on a later duplicate check
        with open(truth_path, "x") as f:
            f.write(CONSUMED + "\n")
    except Exception:  # noqa: BLE001
        pass
    if record == CONSUMED:
        return unverified(
            "this probe was already checked once — generate a fresh probe"
        )
    parts = record.split()
    if len(parts) != 2 or not (parts[0].isdigit() and len(parts[0]) == 6):
        return unverified("malformed truth sidecar")
    code, digest = parts
    try:
        with open(png, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
    except Exception as exc:  # noqa: BLE001
        return unverified("cannot read the probe PNG: %s" % exc)
    if actual != digest:
        return unverified("PNG does not match this sidecar (altered, swapped, or foreign)")
    if guess == code:
        print("VISION-PROBE-PASS: serving read the image correctly")
        return 0
    print(
        "VISION-PROBE-FAIL: the reading does not match the image — treat this "
        "serving as image-blind (probe consumed; use a fresh probe for any retry)"
    )
    return 1


def main(argv) -> int:
    if len(argv) >= 2 and argv[1] == "new":
        return cmd_new(argv[2:])
    if len(argv) >= 2 and argv[1] == "check":
        return cmd_check(argv[2:])
    if len(argv) >= 2 and argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return unverified("help shown — this was not a grading run")
    return unverified("no or unknown command (use `new`, `check`, or `--help`)")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
