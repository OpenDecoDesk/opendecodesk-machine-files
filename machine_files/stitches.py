"""Read .DST (Tajima) and .DSB (Barudan) stitch files into the same shape, so a file sent to a machine can
be compared with what the machine sends back. The machines store every design in their own form (a
DST-style header with Barudan records) — the bytes always differ from what was sent, so the check is on
the design: the needle positions, which moves are jumps, and where the colour changes fall.

Same decoding rules as the OpenDecoDesk website's previewer (checked against real files there).
"""


def _from_base64_if_needed(data):
    # A DST byte 2 is ':' ("LA:"). Some tools hand over a base64 text copy of the file instead.
    if len(data) >= 3 and data[2] != 0x3A:
        import base64
        import binascii
        try:
            return base64.b64decode(bytes(c for c in data if not chr(c).isspace()), validate=True)
        except (binascii.Error, ValueError):
            pass
    return data


def parse_dst(data):
    """Tajima .dst: 512-byte header, then 3-byte records (dx / dy in the standard bit layout; end =
    b2&0xF3==0xF3, colour change = b2&0xC3==0xC3, jump = b2&0x83==0x83)."""
    data = _from_base64_if_needed(bytes(data))
    if len(data) < 512:
        raise ValueError("Too short to be a DST file.")
    moves, x, y, color = [], 0, 0, 0
    for i in range(512, len(data) - 2, 3):
        b0, b1, b2 = data[i], data[i + 1], data[i + 2]
        if b2 & 0xF3 == 0xF3:
            break
        dx = dy = 0
        dx += (b0 & 0x01 and 1) - (b0 & 0x02 and 1) + (b0 & 0x04 and 9) - (b0 & 0x08 and 9)
        dy += (b0 & 0x80 and 1) - (b0 & 0x40 and 1) + (b0 & 0x20 and 9) - (b0 & 0x10 and 9)
        dx += (b1 & 0x01 and 3) - (b1 & 0x02 and 3) + (b1 & 0x04 and 27) - (b1 & 0x08 and 27)
        dy += (b1 & 0x80 and 3) - (b1 & 0x40 and 3) + (b1 & 0x20 and 27) - (b1 & 0x10 and 27)
        dx += (b2 & 0x04 and 81) - (b2 & 0x08 and 81)
        dy += (b2 & 0x20 and 81) - (b2 & 0x10 and 81)
        x += dx
        y += dy
        if b2 & 0xC3 == 0xC3:
            color += 1
            continue
        moves.append((x, y, b2 & 0x83 == 0x83, color))
    return _summary(moves)


def parse_dsb(data):
    """Barudan .dsb: the same 512-byte header, then records of a control byte, Y, X (whole steps, sign in
    the control byte: 0x40 → Y negative, 0x20 → X negative). Control low 5 bits: 0 stitch, 1 jump,
    7 trim, 8 stop, 9–23 needle change, 24 end."""
    data = bytes(data)
    if len(data) < 512:
        raise ValueError("Too short to be a DSB file.")
    moves, x, y, color, block_stitched = [], 0, 0, 0, False
    for i in range(512, len(data) - 2, 3):
        ctrl = data[i]
        low = ctrl & 0x1F
        if low in (0, 1):
            dy = -data[i + 1] if ctrl & 0x40 else data[i + 1]
            dx = -data[i + 2] if ctrl & 0x20 else data[i + 2]
            x += dx
            y += dy
            jump = low == 1
            moves.append((x, y, jump, color))
            if not jump:
                block_stitched = True
            continue
        if low == 24:
            break
        if low in (7, 8):
            continue
        if 9 <= low <= 23:
            if block_stitched:
                color += 1
                block_stitched = False
            continue
        break
    return _summary(moves)


def _summary(moves):
    stitches = [(x, y, c) for x, y, jump, c in moves if not jump]
    if not stitches:
        raise ValueError("No stitches found.")
    xs = [s[0] for s in stitches]
    ys = [s[1] for s in stitches]
    # Colours numbered by the blocks that actually stitch, so a doubled colour change doesn't count twice.
    order = {}
    renumbered = []
    for x, y, c in stitches:
        order.setdefault(c, len(order))
        renumbered.append((x, y, order[c]))
    return {"stitches": renumbered, "stitchCount": len(stitches), "colors": len(order),
            "size": (max(xs) - min(xs), max(ys) - min(ys))}


def parse(data, name):
    ext = str(name).lower().rsplit(".", 1)[-1]
    if ext == "dsb":
        return parse_dsb(data)
    if ext == "dst":
        return parse_dst(data)
    raise ValueError(f"Can't read {name}: only .DST and .DSB files can be compared.")


def compare(sent, sent_name, returned, returned_name="returned.DSB"):
    """Is the design that came back the one that was sent? Compares every needle position in order and
    the colour block each falls in (positions relative to the first stitch, so a different starting
    point doesn't count). Returns {match, summary, ...details}."""
    a = parse(sent, sent_name)
    b = parse(returned, returned_name)

    def rel(s):
        x0, y0 = s["stitches"][0][0], s["stitches"][0][1]
        return [(x - x0, y - y0, c) for x, y, c in s["stitches"]]

    ra, rb = rel(a), rel(b)
    first_diff = next((i for i, (p, q) in enumerate(zip(ra, rb)) if p != q), None)
    if first_diff is None and len(ra) != len(rb):
        first_diff = min(len(ra), len(rb))
    match = first_diff is None
    if match:
        summary = (f"Same design: {a['stitchCount']} stitches, {a['colors']} colour(s), "
                   f"{a['size'][0] / 10:.1f} × {a['size'][1] / 10:.1f} mm.")
    else:
        summary = (f"Different: sent {a['stitchCount']} stitches / {a['colors']} colour(s), came back "
                   f"{b['stitchCount']} / {b['colors']}; first difference at stitch {first_diff + 1}.")
    return {"match": match, "summary": summary, "sent": {k: a[k] for k in ("stitchCount", "colors", "size")},
            "returned": {k: b[k] for k in ("stitchCount", "colors", "size")}, "firstDifference": first_diff}
