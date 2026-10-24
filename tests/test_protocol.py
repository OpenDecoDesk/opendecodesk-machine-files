import socket
import unittest

from machine_files import protocol
from tests.stubs import (StubDiscoveryMachine, StubListingMachine, StubReceivingMachine,
                         StubSendingMachine, lrc)


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FrameTests(unittest.TestCase):
    def test_lrc_matches_a_captured_registration_frame(self):
        # The real captured "register CRM_TEST.DST" frame ends in 0x7A.
        body = bytes([0x01, 0x00, 0xCF, 0x25, 0x00]) + b"CRM_TEST.DST"
        self.assertEqual(protocol._frame(body).hex(),
                         "fa011200" + body.hex() + "7a")

    def test_list_command_lrc_for_every_page(self):
        for page in range(1, 20):
            cmd = protocol._build_list_command(page)
            self.assertEqual(len(cmd), 11)
            self.assertEqual(cmd[-1], lrc(cmd[:-1]))
            self.assertEqual(cmd[-1], 0x13 ^ page)


class DiscoveryTests(unittest.TestCase):
    def test_finds_the_stub_machine(self):
        stub = StubDiscoveryMachine()
        try:
            found = protocol.discover_machines(target_port=stub.port, listen_port=free_udp_port(),
                                               window_seconds=0.5, broadcast_ip="127.0.0.1")
        finally:
            stub.stop()
        self.assertEqual(len(found), 1)
        m = found[0]
        self.assertEqual(m["ip"], "127.0.0.1")
        self.assertEqual(m["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(m["model"], "1501ultra")
        self.assertEqual(m["firmware"], "HH1S20260508.2.1.2")

    def test_reply_fields_are_nul_terminated_not_fixed_slices(self):
        stub = StubDiscoveryMachine(model="1201plus", firmware="JD20260617.1.3.1")
        parsed = protocol.parse_discovery_reply(stub.reply())
        stub.stop()
        self.assertEqual(parsed["firmware"], "JD20260617.1.3.1")
        self.assertEqual(parsed["model"], "1201plus")

    def test_replies_come_back_to_any_free_port_even_with_60023_taken(self):
        # A Windows PC where another program (or a Hyper-V reservation) holds 60023 made the scan fail
        # with WinError 10013. The machines answer the port the scan came from, so the scan uses any free port.
        self.assertEqual(protocol.UDP_LISTEN_PORT, 0)
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            blocker.bind(("", 60023))
        except OSError:
            blocker.close()
            blocker = None  # something on this computer already holds it — the same situation
        stub = StubDiscoveryMachine()
        try:
            found = protocol.discover_machines(target_port=stub.port, window_seconds=0.5, broadcast_ip="127.0.0.1")
        finally:
            stub.stop()
            if blocker:
                blocker.close()
        self.assertEqual([m["mac"] for m in found], ["AA:BB:CC:DD:EE:FF"])

    def test_no_machine_answers(self):
        found = protocol.discover_machines(target_port=free_udp_port(), listen_port=free_udp_port(),
                                           window_seconds=0.3, broadcast_ip="127.0.0.1")
        self.assertEqual(found, [])


class ListingTests(unittest.TestCase):
    def test_reassembles_json_split_mid_object_across_pages(self):
        files = [{"name": f"DESIGN_{i:02d}.{i:03d}", "size": 300 + i, "date": "2026-10-01"}
                 for i in range(1, 30)]
        stub = StubListingMachine(files)
        try:
            listing = protocol.list_machine_dir("127.0.0.1", machine_port=stub.port)
        finally:
            stub.stop()
        self.assertEqual([f["name"] for f in listing], [f["name"] for f in files])
        self.assertEqual(listing[0]["size"], 301)

    def test_empty_machine(self):
        stub = StubListingMachine([])
        try:
            self.assertEqual(protocol.list_machine_dir("127.0.0.1", machine_port=stub.port), [])
        finally:
            stub.stop()

    def test_unreachable_machine_is_a_plain_error(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(RuntimeError) as ctx:
            protocol.list_machine_dir("127.0.0.1", machine_port=port)
        self.assertIn("Could not connect", str(ctx.exception))


class SendTests(unittest.TestCase):
    def _send(self, data, name="LOGO.DST", **stub_args):
        stub = StubReceivingMachine(**stub_args)
        try:
            result = protocol.send_design_to_machine("127.0.0.1", name, data, machine_port=stub.port)
        finally:
            stub.stop()
        return stub, result

    def test_multi_block_upload_arrives_byte_exact(self):
        data = bytes((i * 7) % 256 for i in range(3000))  # 3 blocks, last one short
        stub, result = self._send(data)
        self.assertEqual(stub.registered_name, "LOGO.DST")
        self.assertFalse(stub.bad_lrc)
        self.assertEqual(bytes(stub.received), data)
        self.assertEqual(stub.pages, [1, 2, 3])
        self.assertEqual(stub.block_count_fields, {3})
        self.assertEqual(result, {"fileName": "LOGO.DST", "bytes": 3000, "blocks": 3})

    def test_exact_multiple_of_a_block(self):
        data = b"\x00" * 2048
        stub, result = self._send(data)
        self.assertEqual(result["blocks"], 2)
        self.assertEqual(bytes(stub.received), data)

    def test_refused_name(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._send(b"x" * 2000, refuse_name=True)
        self.assertIn("refused the file name", str(ctx.exception))

    def test_non_ascii_name_refused_before_connecting(self):
        with self.assertRaises(RuntimeError):
            protocol.send_design_to_machine("127.0.0.1", "CAFÉ.DST", b"x" * 2000, machine_port=1)

    def test_empty_file_refused(self):
        with self.assertRaises(RuntimeError):
            protocol.send_design_to_machine("127.0.0.1", "A.DST", b"", machine_port=1)


class DownloadTests(unittest.TestCase):
    DESIGN = b"LA:sample" + bytes(503) + bytes((i * 11) % 256 for i in range(12234))  # 12746 B, 13 blocks

    def _read(self, name, files=None, **stub_args):
        stub = StubSendingMachine(files if files is not None else {"Sample Patch.002": self.DESIGN},
                                  **stub_args)
        try:
            return stub, protocol.download_file("127.0.0.1", name, machine_port=stub.port)
        finally:
            stub.stop()

    def test_reads_every_block_and_acks_all_but_the_last(self):
        stub, got = self._read("Sample Patch.002")
        self.assertEqual(got, {"bytes": self.DESIGN, "ext": "DSB"})
        self.assertEqual(stub.requested, "Sample Patch.002")
        # cf22 <page+1 LE16> after blocks 1..12, nothing after block 13 (as in the capture)
        self.assertEqual(stub.acks, ["cf22" + (p + 1).to_bytes(2, "little").hex() for p in range(1, 13)])

    def test_request_frame_shape(self):
        # As captured from the vendor's app: fa 01 <len> 01 00 cf 22 01 00 00 <stored name> <lrc>.
        name = b"Sample Patch.002"
        f = protocol._frame(bytes([0x01, 0x00, 0xCF, 0x22, 0x01, 0x00, 0x00]) + name)
        self.assertEqual(f[:11].hex(), "fa01" + (len(name) + 8).to_bytes(2, "little").hex() + "0100cf22010000")
        self.assertEqual(f[-1], lrc(f[:-1]))

    def test_single_block_and_non_ascii_names(self):
        stub, got = self._read("马头机~1.008", files={"马头机~1.008": b"LA:x" + bytes(600)})
        self.assertEqual(len(got["bytes"]), 604)
        self.assertEqual(stub.requested, "马头机~1.008")
        self.assertEqual(stub.acks, [])

    def test_unknown_file_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._read("Missing.009")
        self.assertIn("refused to send", str(ctx.exception))

    def test_damaged_block_is_reported(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._read("Sample Patch.002", corrupt_page=4)
        self.assertIn("Block 4", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
