"""Small stitch files for tests, written in both formats — and a .DSB re-encoded the way the machines
store what they're sent (header rewritten, trailing jumps instead of an end marker)."""

# A design as moves: ("s", dx, dy) stitch, ("j", dx, dy) jump, ("c",) colour change.
SAMPLE = ([("s", 10, 5)] * 20 + [("j", 40, -30)] + [("s", -7, 12)] * 15 + [("c",)]
          + [("s", 3, -3)] * 25 + [("c",)] + [("s", -50, 60), ("s", 120, -110)] + [("s", 1, 1)] * 10)


def _header(label, stitches, colors):
    text = (f"LA:{label:<16}\rST:{stitches:>7}\rCO:{colors:>3}\r+X:    0\r-X:    0\r+Y:    0\r-Y:    0\r"
            "AX:+    0\rAY:+    0\rMX:+    0\rMY:+    0\rPD:******\r\x1a")
    return text.encode("ascii").ljust(512, b" ")


def _tern(v):
    """Balanced-ternary digits of v (−121…121) for 1, 3, 9, 27, 81."""
    digits = []
    for _ in range(5):
        r = ((v + 1) % 3) - 1
        digits.append(r)
        v = (v - r) // 3
    assert v == 0, "move too big for one DST record"
    return digits


def make_dst(moves, label="TEST"):
    out = bytearray()
    stitches = sum(1 for m in moves if m[0] == "s")
    colors = sum(1 for m in moves if m[0] == "c")
    for m in moves:
        if m[0] == "c":
            out += bytes([0, 0, 0xC3])
            continue
        kind, dx, dy = m
        x, y = _tern(dx), _tern(dy)
        b0 = b1 = 0
        b2 = 0x03
        b0 |= (0x01 if x[0] > 0 else 0x02 if x[0] < 0 else 0) | (0x04 if x[2] > 0 else 0x08 if x[2] < 0 else 0)
        b0 |= (0x80 if y[0] > 0 else 0x40 if y[0] < 0 else 0) | (0x20 if y[2] > 0 else 0x10 if y[2] < 0 else 0)
        b1 |= (0x01 if x[1] > 0 else 0x02 if x[1] < 0 else 0) | (0x04 if x[3] > 0 else 0x08 if x[3] < 0 else 0)
        b1 |= (0x80 if y[1] > 0 else 0x40 if y[1] < 0 else 0) | (0x20 if y[3] > 0 else 0x10 if y[3] < 0 else 0)
        b2 |= (0x04 if x[4] > 0 else 0x08 if x[4] < 0 else 0) | (0x20 if y[4] > 0 else 0x10 if y[4] < 0 else 0)
        if kind == "j":
            b2 |= 0x83
        out += bytes([b0, b1, b2])
    out += bytes([0, 0, 0xF3])
    return _header(label, stitches, colors) + bytes(out)


def make_dsb(moves, label="TEST", machine_style=False):
    """machine_style: as the machines store it — label "/local/b", a needle change before the first
    colour, extra colour-change records, trailing jumps and no end marker."""
    out = bytearray()
    needle = 1
    if machine_style:
        out += bytes([0x80 | (8 + needle), 0, 0])
    for m in moves:
        if m[0] == "c":
            needle += 1
            out += bytes([0x80 | (8 + needle), 0, 0])
            if machine_style:
                out += bytes([0x80 | 8, 0, 0])  # a stop record the machine adds
            continue
        kind, dx, dy = m
        ctrl = 0x80 | (1 if kind == "j" else 0) | (0x40 if dy < 0 else 0) | (0x20 if dx < 0 else 0)
        out += bytes([ctrl, abs(dy), abs(dx)])
    if machine_style:
        out += bytes([0x81, 0, 0]) * 4
    else:
        out += bytes([0x98, 0, 0])
    stitches = sum(1 for m in moves if m[0] == "s")
    colors = sum(1 for m in moves if m[0] == "c")
    return _header("/local/b" if machine_style else label, stitches, colors + (2 if machine_style else 0)) + bytes(out)
