"""Read .DST (Tajima) and .DSB (Barudan) stitch files into the same shape, so a file sent to a machine can
be compared with what the machine sends back. The machines store every design in their own form (a
DST-style header with Barudan records) — the bytes always differ from what was sent, so the check is on
the design: every needle position, and where the machine pauses (a colour change).

A pause is any colour change in a .DST, and any needle change OR stop in a .DSB once stitching has begun
— a .DST can't tell a stop from a colour change (both make the machine pause), and the machines turn
one into the other: a .DST's colour changes come back as needle changes, a .DSB's same-needle stops
stay stops. So both count as the same thing here, and a lost stop shows up as a difference.

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
    7 trim, 8 stop, 9–23 needle change, 24 end — the top bits vary (0xE8 and 0x88 are both a stop; the
    machines write 0x88), so only the low 5 bits are read. Needle changes and stops both start a new
    block (a pause; see the module notes)."""
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
        if low == 7:
            continue
        if low == 8 or 9 <= low <= 23:
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


# ---- Converting the machine's .DSB to .DST -----------------------------------------------------------

DST_MAX_STEP = 121  # the most one .DST record can move (0.1 mm units) along each axis


def _dsb_events(data):
    """A .DSB as a list of ("s"|"j", dx, dy) moves and ("c",) colour changes, in order. A needle change or
    a stop becomes a colour change once something has been stitched (the machine starts every file with a
    needle change; a .DST has no separate stop — its colour change is how a machine is told to pause).
    Trims move nothing and aren't carried over (a .DST has no trim)."""
    data = bytes(data)
    if len(data) < 512:
        raise ValueError("Too short to be a DSB file.")
    events, stitched, pending_change = [], False, False
    for i in range(512, len(data) - 2, 3):
        ctrl = data[i]
        low = ctrl & 0x1F
        if low in (0, 1):
            dy = -data[i + 1] if ctrl & 0x40 else data[i + 1]
            dx = -data[i + 2] if ctrl & 0x20 else data[i + 2]
            if low == 0:
                if pending_change:
                    events.append(("c",))
                    pending_change = False
                stitched = True
            events.append(("j" if low == 1 else "s", dx, dy))
            continue
        if low == 24:
            break
        if low == 7:
            continue
        if low == 8 or 9 <= low <= 23:
            if stitched:
                pending_change = True
            continue
        break
    while events and events[-1][0] == "j":
        events.pop()  # jumps after the last stitch go nowhere
    return events


def _dst_digits(v):
    out = []
    for _ in range(5):
        r = ((v + 1) % 3) - 1
        out.append(r)
        v = (v - r) // 3
    return out


def _dst_record(dx, dy, kind):
    x, y = _dst_digits(dx), _dst_digits(dy)
    pick = lambda d, pos, neg: pos if d > 0 else neg if d < 0 else 0
    b0 = pick(x[0], 0x01, 0x02) | pick(x[2], 0x04, 0x08) | pick(y[0], 0x80, 0x40) | pick(y[2], 0x20, 0x10)
    b1 = pick(x[1], 0x01, 0x02) | pick(x[3], 0x04, 0x08) | pick(y[1], 0x80, 0x40) | pick(y[3], 0x20, 0x10)
    b2 = 0x03 | pick(x[4], 0x04, 0x08) | pick(y[4], 0x20, 0x10)
    if kind == "j":
        b2 |= 0x83
    elif kind == "c":
        b2 |= 0xC3
    return bytes([b0, b1, b2])


def dsb_to_dst(data, label="DESIGN"):
    """The same design as a Tajima .DST: every stitch at the same place, jumps kept, each needle change a
    colour change. Moves longer than one .DST record allows are split into jumps first. Returns bytes."""
    events = _dsb_events(data)
    if not any(e[0] == "s" for e in events):
        raise ValueError("No stitches found.")
    body = bytearray()
    x = y = 0
    min_x = min_y = max_x = max_y = 0
    records = colors = 0
    for e in events:
        if e[0] == "c":
            body += _dst_record(0, 0, "c")
            records += 1
            colors += 1
            continue
        kind, dx, dy = e
        # Split a long move: jumps until the rest fits in one record.
        while abs(dx) > DST_MAX_STEP or abs(dy) > DST_MAX_STEP:
            sx = max(-DST_MAX_STEP, min(DST_MAX_STEP, dx))
            sy = max(-DST_MAX_STEP, min(DST_MAX_STEP, dy))
            body += _dst_record(sx, sy, "j")
            records += 1
            x, y, dx, dy = x + sx, y + sy, dx - sx, dy - sy
        body += _dst_record(dx, dy, kind)
        records += 1
        x, y = x + dx, y + dy
        min_x, max_x, min_y, max_y = min(min_x, x), max(max_x, x), min(min_y, y), max(max_y, y)
    body += bytes([0x00, 0x00, 0xF3])
    safe = "".join(c if 32 <= ord(c) < 127 else "_" for c in str(label))[:16]
    header = (f"LA:{safe:<16}\rST:{records:>7}\rCO:{colors:>3}\r+X:{max_x:>5}\r-X:{-min_x:>5}\r"
              f"+Y:{max_y:>5}\r-Y:{-min_y:>5}\rAX:{'+' if x >= 0 else '-'}{abs(x):>5}\r"
              f"AY:{'+' if y >= 0 else '-'}{abs(y):>5}\rMX:+    0\rMY:+    0\rPD:******\r\x1a")
    return header.encode("ascii").ljust(512, b" ") + bytes(body)

