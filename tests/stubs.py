"""Throwaway stand-ins for a SmartStitch machine, speaking the real frame shapes on loopback."""

import json
import socket
import threading
import time


def lrc(b):
    x = 0
    for c in b:
        x ^= c
    return x


def frame(body):
    head = b"\xfa\x01" + (len(body) + 1).to_bytes(2, "little") + body
    return head + bytes([lrc(head)])


def read_frame(conn):
    header = b""
    while len(header) < 4:
        c = conn.recv(4 - len(header))
        if not c:
            return None
        header += c
    remaining = header[2] | (header[3] << 8)
    body = b""
    while len(body) < remaining:
        c = conn.recv(remaining - len(body))
        if not c:
            break
        body += c
    return header + body


class StubDiscoveryMachine:
    """Answers the 4-byte discovery payload with a reply in the real layout (MAC at 9:15,
    NUL-terminated firmware from 19, model from 51)."""

    PAYLOAD = bytes.fromhex("ff 04 02 fb")

    def __init__(self, mac="AA:BB:CC:DD:EE:FF", model="1501ultra", firmware="HH1S20260508.2.1.2"):
        self.mac, self.model, self.firmware = mac, model, firmware
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._serve, daemon=True).start()

    def reply(self):
        packet = bytearray(84)
        packet[9:15] = bytes(int(b, 16) for b in self.mac.split(":"))
        fw = self.firmware.encode()[:31]
        packet[19:19 + len(fw)] = fw
        model = self.model.encode()[:29]
        packet[51:51 + len(model)] = model
        return bytes(packet)

    def _serve(self):
        self.sock.settimeout(0.2)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            if data == self.PAYLOAD:
                self.sock.sendto(self.reply(), addr)

    def stop(self):
        self.running = False
        time.sleep(0.05)
        self.sock.close()


class _TcpStub:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._guarded, args=(conn,), daemon=True).start()

    def _guarded(self, conn):
        try:
            conn.settimeout(3.0)
            self.handle(conn)
        except (OSError, ConnectionError):
            pass
        finally:
            conn.close()

    def stop(self):
        self.running = False
        self.sock.close()


class StubListingMachine(_TcpStub):
    """Sends the listing as one JSON document split into pages at arbitrary byte boundaries (a
    small CHUNK so it splits mid-object), then a header-only frame meaning "no more pages"."""

    CHUNK = 64

    def __init__(self, files):
        self.doc = json.dumps({"num": len(files), "files": files}).encode()
        super().__init__()

    def handle(self, conn):
        while True:
            cmd = conn.recv(11)
            if not cmd or len(cmd) < 11:
                return
            page = cmd[8]
            chunk = self.doc[(page - 1) * self.CHUNK:page * self.CHUNK]
            body = bytes([0x01, 0x00, 0xCF, 0x26]) + page.to_bytes(2, "little")
            if chunk:
                body += bytes([0x02, 0x00]) + chunk
            conn.sendall(frame(body))


class StubReceivingMachine(_TcpStub):
    """Receives an upload: checks every frame's LRC, echoes the cf25 registration, acks every
    cf23 block but the last (as the real machine does), and keeps the bytes."""

    def __init__(self, refuse_name=False):
        self.refuse_name = refuse_name
        self.received = bytearray()
        self.registered_name = None
        self.bad_lrc = False
        self.block_count_fields = set()
        self.pages = []
        super().__init__()

    def handle(self, conn):
        total = None
        while True:
            f = read_frame(conn)
            if not f:
                return
            if lrc(f[:-1]) != f[-1]:
                self.bad_lrc = True
            op = f[6:8]
            if op == bytes([0xCF, 0x25]):
                self.registered_name = f[9:-1].decode("ascii", "ignore")
                status = bytes([0xC9, 0x00, 0x00]) if self.refuse_name else bytes([0x01, 0x00, 0x00])
                conn.sendall(frame(bytes([0x01, 0x00, 0xCF, 0x22]) + status + f[9:-1]))
            elif op == bytes([0xCF, 0x23]):
                page = f[8] | (f[9] << 8)
                total = f[10] | (f[11] << 8)
                self.pages.append(page)
                self.block_count_fields.add(total)
                self.received += f[12:-1]
                if page < total:
                    conn.sendall(frame(bytes([0x01, 0x00, 0xCF, 0x22, (page + 1) & 0xFF, 0x00])))


class StubSendingMachine(_TcpStub):
    """Sends a file back the way the real machine does: on 01 00 cf 22 01 00 00 <name> it streams
    cf23 blocks (page16, total16, ≤1024 bytes) and waits for the computer's cf22 <page+1> ack
    before each next block. An unknown name gets cf e0. `corrupt_page` breaks one block's LRC."""

    def __init__(self, files, corrupt_page=None):
        self.files = files
        self.corrupt_page = corrupt_page
        self.requested = None
        self.acks = []
        super().__init__()

    def handle(self, conn):
        f = read_frame(conn)
        if not f or f[6:11] != bytes([0xCF, 0x22, 0x01, 0x00, 0x00]):
            return
        self.requested = f[11:-1].decode("utf-8")
        data = self.files.get(self.requested)
        if data is None:
            conn.sendall(frame(bytes([0x01, 0x00, 0xCF, 0xE0])))
            return
        total = (len(data) + 1023) // 1024
        for page in range(1, total + 1):
            out = frame(bytes([0x01, 0x00, 0xCF, 0x23]) + page.to_bytes(2, "little")
                        + total.to_bytes(2, "little") + data[(page - 1) * 1024:page * 1024])
            if page == self.corrupt_page:
                out = out[:-1] + bytes([out[-1] ^ 0xFF])
            conn.sendall(out)
            if page < total:
                ack = read_frame(conn)
                if not ack:
                    return
                self.acks.append(ack[6:-1].hex())
