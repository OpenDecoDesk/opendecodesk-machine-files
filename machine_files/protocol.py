"""
The SmartStitch embroidery machines' LAN protocol — discovery, file listing, upload.

These machines are NOT FTP servers. They speak two proprietary protocols, reverse-engineered from
Wireshark captures of the vendor's own software (full reference: PROTOCOL.md):
  - Discovery: a UDP broadcast to port 23 with a fixed 4-byte payload; each machine replies to
    the port the scan came from with its MAC, firmware and model (the vendor's app happens to use 60023).
  - Everything else: framed TCP on port 6000. Every frame, in both directions, is
        fa 01 <len16 LE = len(body)+1> <body> <lrc>
    where <lrc> is the XOR of every byte before it.

Reading a file back (download_file) is the upload run in reverse: the computer names the file
in a cf22 frame and the machine streams it in cf23 blocks, which the computer acks.

Standard library only. Every failure raises a RuntimeError whose message can be shown to the
operator as-is.
"""

import concurrent.futures
import json
import logging
import math
import socket
import threading
import time

log = logging.getLogger("machine_files.protocol")

# ---- Discovery (UDP) ---------------------------------------------------------------------
UDP_BROADCAST_IP = "255.255.255.255"
UDP_TARGET_PORT = 23      # the machines' discovery port
# The machines answer to whatever port the scan is sent FROM, so any free port works (0 = let the system
# pick). A fixed one (the vendor's app uses 60023) can be held by another program or reserved by Windows
# (Hyper-V reserves ranges of high ports), which made binding fail with WinError 10013.
UDP_LISTEN_PORT = 0
DISCOVERY_PAYLOAD = bytes.fromhex("ff 04 02 fb")
DISCOVERY_WINDOW_SECONDS = 2.5  # 1.5 s occasionally missed a machine
DISCOVERY_ROUNDS = 3            # the scan is sent this many times across the window: UDP can be lost (Wi-Fi)

# ---- File listing and transfers (TCP) ----------------------------------------------------
TCP_PORT = 6000

# The fallback when the broadcast scan gets no reply (a firewall that drops replies to a broadcast, or a
# network that doesn't pass broadcasts): connect to TCP 6000 on every address of this computer's networks.
# Connections this computer makes, and their answers, get through firewalls that drop the broadcast replies.
SWEEP_CONNECT_SECONDS = 0.6   # per address; a machine on the LAN accepts in a few ms
SWEEP_CHECK_SECONDS = 1.5     # for the "list files" answer that proves it's a machine
SWEEP_WORKERS = 128
LIST_CONNECT_TIMEOUT_SECONDS = 2.0
LIST_PAGE_READ_SECONDS = 2.0
MAX_LIST_PAGES = 64  # ~14 files a page; listing stops early once the declared `num` files are in

SEND_CONNECT_TIMEOUT_SECONDS = 3.0
SEND_ACK_TIMEOUT_SECONDS = 6.0
SEND_CHUNK_BYTES = 1024
_ERROR_STATUS_BYTE = 0xC9  # seen in a cf22 status reply when the machine rejects a transfer


# Files come back in the machine's own stored form: a DST-style header followed by Barudan
# (.DSB) stitch records, whatever format was uploaded.
DOWNLOAD_EXT = "DSB"
MAX_DOWNLOAD_BLOCKS = 20 * 1024  # 20 MB at 1024 bytes a block; far above any real design


def _lrc(frame_without_trailer):
    """The trailing check byte on every frame: XOR of all preceding bytes."""
    x = 0
    for b in frame_without_trailer:
        x ^= b
    return x


def _frame(body):
    """Wrap a body in the protocol's envelope: fa 01 <len16 LE> <body> <lrc>."""
    head = bytes([0xFA, 0x01]) + (len(body) + 1).to_bytes(2, "little") + body
    return head + bytes([_lrc(head)])


def _recv_frame(sock):
    """Read one whole frame (fa 01 <len16> <body+lrc>). Returns the frame bytes, or None if the
    machine sent nothing or an unparseable header."""
    header = b""
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            return None
        header += chunk
    if header[0] != 0xFA or header[1] != 0x01:
        return None
    remaining = header[2] | (header[3] << 8)
    body = b""
    while len(body) < remaining:
        chunk = sock.recv(remaining - len(body))
        if not chunk:
            break
        body += chunk
    return header + body


def _reply_is_error(frame):
    """A cf22 status reply signals rejection with the error byte in its status slot."""
    if not frame or len(frame) < 10:
        return False
    if frame[6:8] != bytes([0xCF, 0x22]):
        return False
    return _ERROR_STATUS_BYTE in frame[8:11]


def _cstr(buf, start):
    """A NUL-terminated ASCII string starting at `start` (the discovery reply's fields are
    variable length, so they must never be sliced at a fixed end offset)."""
    if start >= len(buf):
        return ""
    return buf[start:].split(b"\x00")[0].decode("utf-8", errors="ignore").strip()


def parse_discovery_reply(data):
    """MAC at bytes 9:15, firmware NUL-terminated from byte 19, model NUL-terminated from byte 51."""
    mac = ":".join(f"{b:02X}" for b in data[9:15]) if len(data) >= 15 else ""
    return {"mac": mac, "firmware": _cstr(data, 19), "model": _cstr(data, 51) or "SmartStitch"}


def _host_ipv4_addresses(timeout=1.0):
    """The IPv4 addresses this computer's own name resolves to — every adapter's, on Windows. The
    lookup can hang on a network with slow name service (macOS especially), so it gets `timeout`
    seconds in a background thread; [] if it doesn't answer by then."""
    result = []

    def lookup():
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                result.append(info[4][0])
        except OSError:
            pass

    t = threading.Thread(target=lookup, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        log.warning("looking up this computer's own addresses took over %.0f s — skipped", timeout)
        return []
    return list(result)


def local_ipv4_addresses():
    """This computer's IPv4 addresses (not loopback): every address the host name resolves to, and
    the one used to reach the internet (a UDP connect sends nothing)."""
    found = _host_ipv4_addresses()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    out = []
    for ip in found:
        if not ip.startswith("127.") and ip not in out:
            out.append(ip)
    return out


def directed_broadcasts(addresses):
    """Each network's own broadcast address, taking every network as a /24 (192.168.0.20 →
    192.168.0.255). Windows sends 255.255.255.255 out of ONE network adapter only — on a PC with
    VPN, Hyper-V or Remote Desktop adapters that can be the wrong one — so the scan goes to these too.
    Link-local (169.254.x.x) addresses are skipped."""
    out = []
    for ip in addresses:
        parts = ip.split(".")
        if len(parts) != 4 or ip.startswith("169.254."):
            continue
        b = ".".join(parts[:3] + ["255"])
        if b not in out:
            out.append(b)
    return out


def discover_machines(target_port=UDP_TARGET_PORT, listen_port=UDP_LISTEN_PORT,
                      window_seconds=DISCOVERY_WINDOW_SECONDS, broadcast_ip=UDP_BROADCAST_IP):
    """Broadcast the discovery payload and collect replies for `window_seconds`. Returns
    [{ip, mac, model, firmware}], one per IP that answered. The port / address arguments exist
    for the tests' stub machine; the defaults are the real values. With the default broadcast
    address it also sends to each local network's own broadcast address (directed_broadcasts)."""
    targets = [broadcast_ip]
    if broadcast_ip == UDP_BROADCAST_IP:
        addresses = local_ipv4_addresses()
        log.info("scan: this computer's IPv4 addresses: %s", ", ".join(addresses) or "(none)")
        targets += [b for b in directed_broadcasts(addresses) if b not in targets]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass  # not on every platform; SO_REUSEADDR is enough there
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        # Windows turns an ICMP "port unreachable" into an error on the NEXT receive, which
        # would end the scan; switch that off.
        try:
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except OSError as e:
            log.warning("scan: couldn't switch off Windows' UDP connection-reset errors: %s", e)
    try:
        sock.bind(("", listen_port))
    except OSError as e:
        sock.close()
        log.error("scan: can't listen on UDP port %s: %s", listen_port, e)
        raise RuntimeError(f"Could not open a network port to hear the machines' replies: {e}")
    log.info("scan: listening on UDP %s:%s for %.1f s", *sock.getsockname()[:2], window_seconds)

    machines = []
    seen = set()
    resets = ignored = 0
    def send_round(n):
        sent = 0
        for target in targets:
            try:
                sock.sendto(DISCOVERY_PAYLOAD, (target, target_port))
                sent += 1
                log.info("scan: round %d sent to %s:%s", n, target, target_port)
            except OSError as e:
                log.warning("scan: round %d couldn't send to %s:%s: %s", n, target, target_port, e)
        return sent

    try:
        if not send_round(1):
            raise RuntimeError("Could not send the machine scan on this network — see Save diagnostics.")
        start = time.time()
        deadline = start + window_seconds
        resend_at = [start + window_seconds * k / DISCOVERY_ROUNDS for k in range(1, DISCOVERY_ROUNDS)]
        while True:
            now = time.time()
            remaining = deadline - now
            if remaining <= 0:
                break
            if resend_at and now >= resend_at[0]:
                resend_at.pop(0)
                send_round(DISCOVERY_ROUNDS - len(resend_at))
                continue
            sock.settimeout(max(0.01, min(remaining, (resend_at[0] - now) if resend_at else remaining)))
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue  # time for the next round, or the end of the window (checked above)
            except ConnectionResetError:
                resets += 1  # Windows' ICMP "port unreachable", if the switch above wasn't honoured
                continue
            if data == DISCOVERY_PAYLOAD:
                ignored += 1
                log.debug("scan: our own scan came back from %s (ignored)", addr[0])
                continue
            if addr[0] in seen:
                continue
            seen.add(addr[0])
            info = parse_discovery_reply(data)
            log.info("scan: reply from %s, %d bytes: model %s, MAC %s, firmware %s",
                     addr[0], len(data), info["model"], info["mac"], info["firmware"])
            machines.append({"ip": addr[0], **info})
    finally:
        sock.close()
    log.info("scan: done — %d machine(s)%s%s", len(machines),
             f", {resets} connection-reset error(s)" if resets else "",
             f", {ignored} echo(es) of our own scan" if ignored else "")
    if not machines:
        log.warning("scan: no replies. Usual causes: this computer isn't on the machines' network, or a "
                    "firewall blocks the replies (UDP from the machines' port 23).")
    return machines


def sweep_targets(addresses):
    """Every other address on each of this computer's networks, taken as /24s (192.168.1.173 →
    192.168.1.1 … .254 without .173). Link-local and loopback addresses are skipped."""
    out, mine = [], set(addresses)
    for ip in addresses:
        parts = ip.split(".")
        if len(parts) != 4 or ip.startswith(("169.254.", "127.")):
            continue
        for n in range(1, 255):
            host = ".".join(parts[:3] + [str(n)])
            if host not in mine and host not in out:
                out.append(host)
    return out


def check_machine(ip, port=TCP_PORT, connect_timeout=SWEEP_CONNECT_SECONDS, check_timeout=SWEEP_CHECK_SECONDS):
    """True if something at ip:port answers the "list files" request the way these machines do (a
    framed cf26 page) — so a printer or anything else that happens to accept on that port isn't
    taken for a machine. Read-only: one page of the file list, then the connection is closed."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(connect_timeout)
    try:
        sock.connect((ip, port))
    except (socket.timeout, OSError):
        sock.close()
        return False
    try:
        log.info("sweep: %s accepts on port %s — checking it's a machine", ip, port)
        time.sleep(0.15)  # the vendor app's pause between connect and the first command
        sock.settimeout(check_timeout)
        sock.sendall(_build_list_command(1))
        frame = _recv_frame(sock)
        ok = bool(frame) and len(frame) >= 8 and frame[6:8] == bytes([0xCF, 0x26])
        log.info("sweep: %s %s", ip, "answers like a machine" if ok else "isn't a machine (wrong answer)")
        return ok
    except (socket.timeout, OSError) as e:
        log.info("sweep: %s isn't a machine (%s)", ip, e)
        return False
    finally:
        sock.close()


def ask_identity(ip, target_port=UDP_TARGET_PORT, timeout=1.0):
    """Ask one address directly for its model / MAC / firmware (the scan's packet, sent straight to it;
    its answer comes from the address asked, which firewalls let through). None if no answer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("", 0))
        sock.settimeout(timeout)
        sock.sendto(DISCOVERY_PAYLOAD, (ip, target_port))
        deadline = time.time() + timeout
        while time.time() < deadline:
            sock.settimeout(max(0.01, deadline - time.time()))
            data, addr = sock.recvfrom(1024)
            if addr[0] == ip and data != DISCOVERY_PAYLOAD:
                return parse_discovery_reply(data)
    except (socket.timeout, OSError):
        pass
    finally:
        sock.close()
    return None


def sweep_for_machines(hosts=None, port=TCP_PORT, target_port=UDP_TARGET_PORT, workers=SWEEP_WORKERS,
                       progress=None):
    """The fallback scan: try TCP `port` on every address in `hosts` (default: this computer's
    networks, sweep_targets), many at once; keep the ones that answer like a machine, then ask each
    for its model / MAC / firmware. Returns [{ip, mac, model, firmware, foundBy}]."""
    if hosts is None:
        hosts = sweep_targets(local_ipv4_addresses())
    started = time.time()
    log.info("sweep: trying port %s on %d address(es)", port, len(hosts))
    if progress:
        progress(f"No reply to the scan — checking {len(hosts)} addresses on port {port}…")
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, len(hosts) or 1))) as pool:
        for ip, ok in zip(hosts, pool.map(lambda h: check_machine(h, port), hosts)):
            if ok:
                found.append(ip)
    machines = []
    for ip in found:
        info = ask_identity(ip, target_port) or {"mac": "", "firmware": "", "model": "SmartStitch"}
        machines.append({"ip": ip, **info, "foundBy": f"port {port}"})
    log.info("sweep: done in %.1f s — %d machine(s)%s", time.time() - started, len(machines),
             (": " + ", ".join(f"{m['model']} {m['ip']}" for m in machines)) if machines else "")
    return machines


def find_machines(progress=None):
    """The scan the app runs: the broadcast first; if nothing answers, the port-6000 sweep."""
    found = discover_machines()
    if found:
        return found
    return sweep_for_machines(progress=progress)


def _build_list_command(page_num):
    """The 11-byte frame requesting one page of the file listing."""
    frame = [0xFA, 0x01, 0x07, 0x00, 0x01, 0x00, 0xCF, 0x21, page_num & 0xFF, 0x00]
    return bytes(frame + [_lrc(frame)])


def _connect(machine_ip, machine_port, timeout):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((machine_ip, machine_port))
    except (socket.timeout, OSError) as e:
        sock.close()
        log.warning("connect to %s:%s failed: %s", machine_ip, machine_port, e)
        raise RuntimeError(f"Could not connect to the machine at {machine_ip}: {e}")
    return sock


def list_machine_dir(machine_ip, machine_port=TCP_PORT):
    """The machine's files as [{name, size, modified}].

    The machine returns ONE JSON document ({"num":N,"files":[{name,size,date}]}) split across
    framed pages at arbitrary byte boundaries, so each page's 12-byte prefix and 1-byte trailing
    LRC are dropped, the chunks joined, and the whole parsed once."""
    log.info("list: connecting to %s:%s", machine_ip, machine_port)
    sock = _connect(machine_ip, machine_port, LIST_CONNECT_TIMEOUT_SECONDS)
    try:
        time.sleep(0.15)  # the vendor app's pause between connect and the first command
        sock.settimeout(LIST_PAGE_READ_SECONDS)
        chunks = []
        for page in range(1, MAX_LIST_PAGES + 1):
            sock.sendall(_build_list_command(page))
            try:
                frame = _recv_frame(sock)
            except socket.timeout:
                break
            if frame is None or len(frame) <= 13:  # header only = no more pages
                break
            chunks.append(frame[12:-1])
            try:
                parsed = json.loads(b"".join(chunks).decode("utf-8", errors="ignore"))
                if len(parsed.get("files", [])) >= parsed.get("num", 0):
                    break
            except ValueError:
                pass  # incomplete so far
        if not chunks:
            log.info("list: %s has no files", machine_ip)
            return []
        try:
            parsed = json.loads(b"".join(chunks).decode("utf-8", errors="ignore"))
        except ValueError:
            raise RuntimeError("The machine's file list came back incomplete — try again.")
        files = [{"name": f.get("name", ""), "size": f.get("size"), "modified": f.get("date")}
                 for f in parsed.get("files", [])]
        log.info("list: %s has %d file(s) (%d page(s))", machine_ip, len(files), len(chunks))
        return files
    except OSError as e:
        log.warning("list: connection to %s lost: %s", machine_ip, e)
        raise RuntimeError(f"Lost the connection to the machine while listing its files: {e}")
    finally:
        sock.close()


def send_design_to_machine(machine_ip, file_name, file_bytes, machine_port=TCP_PORT):
    """Upload one design: register the filename (cf25, echoed back in a cf22), then stream the
    bytes 1024 a block (cf23 <page16> <totalBlocks16> <bytes>). The machine cf22-acks every block
    but the last; after the last it just commits. Returns {fileName, bytes, blocks}."""
    try:
        name_bytes = file_name.encode("ascii")
    except UnicodeEncodeError:
        raise RuntimeError(f"“{file_name}”: machine file names must be plain ASCII — rename the file.")
    if not name_bytes:
        raise RuntimeError("No file name to give the machine.")
    if not file_bytes:
        raise RuntimeError(f"“{file_name}” is empty.")

    total = len(file_bytes)
    total_blocks = math.ceil(total / SEND_CHUNK_BYTES)
    log.info("send %s to %s:%s, %d bytes in %d blocks", file_name, machine_ip, machine_port,
             total, total_blocks)

    sock = _connect(machine_ip, machine_port, SEND_CONNECT_TIMEOUT_SECONDS)
    try:
        time.sleep(0.15)
        sock.settimeout(SEND_ACK_TIMEOUT_SECONDS)

        sock.sendall(_frame(bytes([0x01, 0x00, 0xCF, 0x25, 0x00]) + name_bytes))
        ack = _recv_frame(sock)
        if ack is None:
            raise RuntimeError("The machine didn't acknowledge the file name. Make sure the "
                               "vendor's app is closed and the machine is idle.")
        if _reply_is_error(ack):
            raise RuntimeError(f"The machine refused the file name “{file_name}”.")

        total_field = total_blocks.to_bytes(2, "little")
        for page in range(1, total_blocks + 1):
            chunk = file_bytes[(page - 1) * SEND_CHUNK_BYTES:page * SEND_CHUNK_BYTES]
            body = bytes([0x01, 0x00, 0xCF, 0x23]) + page.to_bytes(2, "little") + total_field + chunk
            sock.sendall(_frame(body))
            if page == total_blocks:
                # No ack after the last block; look briefly only for an explicit refusal.
                sock.settimeout(1.0)
                try:
                    tail = _recv_frame(sock)
                except socket.timeout:
                    tail = None
                if _reply_is_error(tail):
                    raise RuntimeError(f"The machine refused the last part of “{file_name}”.")
                break
            block_ack = _recv_frame(sock)
            if block_ack is None:
                raise RuntimeError(f"The transfer of “{file_name}” stalled after block {page}.")
            if _reply_is_error(block_ack):
                raise RuntimeError(f"The machine refused block {page} of “{file_name}”.")
        log.info("send: %s delivered (%d blocks)", file_name, total_blocks)
        return {"fileName": file_name, "bytes": total, "blocks": total_blocks}
    except RuntimeError as e:
        log.warning("send: %s failed: %s", file_name, e)
        raise
    except socket.timeout:
        log.warning("send: %s timed out", file_name)
        raise RuntimeError(f"Timed out waiting for the machine while sending “{file_name}”.")
    except OSError as e:
        raise RuntimeError(f"Lost the connection to the machine while sending “{file_name}”: {e}")
    finally:
        sock.close()


def download_file(machine_ip, stored_name, machine_port=TCP_PORT):
    """Read one file back off the machine by the name it lists (e.g. "LOGO.012").

    Request: body 01 00 cf 22 01 00 00 <stored name> (the shape of the machine's reply to an
    upload registration). The machine answers with cf23 blocks 01 00 cf 23 <page16> <total16>
    <up to 1024 bytes>, pages from 1; the computer acks each with 01 00 cf 22 <page+1 16>, except
    the last. Returns {"bytes": data, "ext": "DSB"}."""
    try:
        name_bytes = stored_name.encode("utf-8")  # the listing JSON is UTF-8
    except UnicodeEncodeError:
        raise RuntimeError(f"Can't ask the machine for “{stored_name}”.")
    if not name_bytes:
        raise RuntimeError("No file name to ask the machine for.")

    log.info("read: asking %s:%s for %s", machine_ip, machine_port, stored_name)
    sock = _connect(machine_ip, machine_port, SEND_CONNECT_TIMEOUT_SECONDS)
    try:
        time.sleep(0.15)
        sock.settimeout(SEND_ACK_TIMEOUT_SECONDS)
        sock.sendall(_frame(bytes([0x01, 0x00, 0xCF, 0x22, 0x01, 0x00, 0x00]) + name_bytes))

        data = bytearray()
        page = 1
        while True:
            frame = _recv_frame(sock)
            if frame is None:
                if page == 1:
                    raise RuntimeError(f"The machine didn't send “{stored_name}”. Make sure the "
                                       "vendor's app is closed and the machine is idle.")
                raise RuntimeError(f"The machine stopped sending “{stored_name}” after block {page - 1}.")
            if len(frame) < 13 or frame[6:8] != bytes([0xCF, 0x23]):
                raise RuntimeError(f"The machine refused to send “{stored_name}”.")
            if _lrc(frame[:-1]) != frame[-1]:
                raise RuntimeError(f"Block {page} of “{stored_name}” arrived damaged — try again.")
            got_page = frame[8] | (frame[9] << 8)
            total = frame[10] | (frame[11] << 8)
            if got_page != page or not 1 <= total <= MAX_DOWNLOAD_BLOCKS:
                raise RuntimeError(f"The machine sent “{stored_name}” out of order — try again.")
            data += frame[12:-1]
            if page >= total:
                break
            sock.sendall(_frame(bytes([0x01, 0x00, 0xCF, 0x22]) + (page + 1).to_bytes(2, "little")))
            page += 1
        if not data:
            raise RuntimeError(f"The machine sent “{stored_name}” empty.")
        log.info("read: %s — %d bytes in %d block(s)", stored_name, len(data), page)
        return {"bytes": bytes(data), "ext": DOWNLOAD_EXT}
    except RuntimeError as e:
        log.warning("read: %s failed: %s", stored_name, e)
        raise
    except socket.timeout:
        log.warning("read: %s timed out", stored_name)
        raise RuntimeError(f"Timed out waiting for the machine while copying “{stored_name}”.")
    except OSError as e:
        raise RuntimeError(f"Lost the connection to the machine while copying “{stored_name}”: {e}")
    finally:
        sock.close()
