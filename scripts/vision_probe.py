#!/usr/bin/env python3
"""Blind vision probe: can the current model serving actually see images?

Motivation (measured): an agent serving can be silently image-blind — the harness
read tool returns "model does not support images" or, worse, image support drifts
mid-session — while the skill's visual-review steps (schematic PDF review, layout
render review, datasheet figure reading) silently degrade to text-only proxies.
One benchmark session recorded a "visually reviewed" claim that could not have
happened. This probe closes that hole with a content round-trip the model cannot
fake from context.

Usage:
  vision_probe.py new [dir]        write probe PNG + truth sidecar; prints ONLY the
                                   PNG path (the truth never enters the transcript)
  vision_probe.py check <png> <digits>
                                   compare the model's reading against the sidecar

The agent workflow: run `new`, read the printed PNG with the harness image/read
tool, then run `check <png> <the digits you saw>`.

Exit codes / verdict lines (fail-closed):
  0  VISION-PROBE-PASS         digits match
  1  VISION-PROBE-FAIL         digits do not match (image-blind or hallucinated)
  2  VISION-PROBE-UNVERIFIED   probe could not run (missing sidecar, bad args, IO
                               error) — treat exactly like FAIL for gating

If the read tool itself errors ("model does not support images", image omitted),
that is a FAIL for gating purposes; `check` exists for the subtler case where an
image block is returned but the model cannot actually see it.

No third-party dependencies: the PNG is written with zlib/struct only.
"""

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


def render(code: str):
    cols = len(code) * (3 + GAP) - GAP
    w = cols * SCALE + 2 * MARGIN
    h = 5 * SCALE + 2 * MARGIN
    rows = [bytearray([255]) * 0 or bytearray([255] * w) for _ in range(h)]
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


def write_png(path: str, w: int, h: int, rows) -> None:
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)  # 8-bit grayscale
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(raw)))
        f.write(chunk(b"IEND", b""))


def main(argv) -> int:
    if len(argv) >= 2 and argv[1] == "new":
        out_dir = argv[2] if len(argv) > 2 else "."
        try:
            code = "".join(str(b % 10) for b in os.urandom(6))
            base = os.path.join(out_dir, "vision-probe-%s" % os.urandom(4).hex())
            w, h, rows = render(code)
            write_png(base + ".png", w, h, rows)
            with open(base + ".truth", "w") as f:
                f.write(code + "\n")
            # Print only the PNG path: the truth must not enter the transcript.
            print(base + ".png")
            return 0
        except Exception as exc:  # noqa: BLE001
            print("VISION-PROBE-UNVERIFIED: could not create probe: %s" % exc)
            return 2

    if len(argv) == 4 and argv[1] == "check":
        png, guess = argv[2], argv[3].strip()
        truth_path = png[:-4] + ".truth" if png.endswith(".png") else png + ".truth"
        try:
            truth = open(truth_path).read().strip()
        except Exception as exc:  # noqa: BLE001
            print("VISION-PROBE-UNVERIFIED: cannot read truth sidecar: %s" % exc)
            return 2
        if not truth:
            print("VISION-PROBE-UNVERIFIED: empty truth sidecar")
            return 2
        if guess == truth:
            print("VISION-PROBE-PASS: serving read the image correctly")
            return 0
        print(
            "VISION-PROBE-FAIL: read %r, image says %r — treat this serving as "
            "image-blind" % (guess, truth)
        )
        return 1

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
