"""The fallback scan: TCP 6000 on every address, kept only if it answers like a machine."""

import socket
import threading
import time
import unittest
from unittest import mock

from machine_files import protocol
from tests.stubs import StubDiscoveryMachine, StubListingMachine


class _OtherServer:
    """Something else listening on the port: answers with junk, or with nothing at all."""

    def __init__(self, reply):
        self.reply = reply
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
            try:
                conn.recv(64)
                if self.reply:
                    conn.sendall(self.reply)
                time.sleep(2)
            except OSError:
                pass
            finally:
                conn.close()

    def stop(self):
        self.running = False
        self.sock.close()


class SweepTargetTests(unittest.TestCase):
    def test_every_other_address_of_each_network(self):
        hosts = protocol.sweep_targets(["192.168.1.173", "169.254.9.9", "10.0.0.5"])
        self.assertEqual(len(hosts), 253 + 253)
        self.assertNotIn("192.168.1.173", hosts)
        self.assertIn("192.168.1.1", hosts)
        self.assertIn("192.168.1.254", hosts)
        self.assertNotIn("192.168.1.0", hosts)
        self.assertNotIn("192.168.1.255", hosts)
        self.assertFalse(any(h.startswith("169.254.") for h in hosts))
        self.assertEqual(protocol.sweep_targets([]), [])


class SweepTests(unittest.TestCase):
    def test_finds_a_machine_and_asks_it_who_it_is(self):
        listing = StubListingMachine([{"name": "LOGO.001", "size": 10, "date": "2026-10-01"}])
        identity = StubDiscoveryMachine(model="1201plus", firmware="JD20260617.1.3.1")
        try:
            with self.assertLogs("machine_files.protocol", level="INFO") as logs:
                found = protocol.sweep_for_machines(hosts=["127.0.0.1"], port=listing.port, target_port=identity.port)
        finally:
            listing.stop()
            identity.stop()
        self.assertEqual(found, [{"ip": "127.0.0.1", "mac": "AA:BB:CC:DD:EE:FF", "model": "1201plus",
                                  "firmware": "JD20260617.1.3.1", "foundBy": f"port {listing.port}"}])
        self.assertIn("answers like a machine", "\n".join(logs.output))

    def test_a_machine_that_wont_say_who_it_is_is_still_found(self):
        listing = StubListingMachine([])
        silent_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        silent_udp.bind(("127.0.0.1", 0))
        try:
            found = protocol.sweep_for_machines(hosts=["127.0.0.1"], port=listing.port,
                                                target_port=silent_udp.getsockname()[1])
        finally:
            listing.stop()
            silent_udp.close()
        self.assertEqual([(m["ip"], m["model"]) for m in found], [("127.0.0.1", "SmartStitch")])

    def test_something_else_on_the_port_is_not_a_machine(self):
        for reply in (b"HTTP/1.1 400 Bad Request\r\n\r\n", b""):
            other = _OtherServer(reply)
            try:
                started = time.time()
                found = protocol.sweep_for_machines(hosts=["127.0.0.1"], port=other.port)
                took = time.time() - started
            finally:
                other.stop()
            self.assertEqual(found, [], reply)
            self.assertLess(took, 5, "a silent server is given up on, not waited for")

    def test_many_addresses_quickly(self):
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        # Plain timing check against closed ports on loopback: refused at once, all in parallel.
        started = time.time()
        found = protocol.sweep_for_machines(hosts=[f"127.0.0.{n}" for n in range(2, 60)], port=closed_port)
        self.assertEqual(found, [])
        self.assertLess(time.time() - started, 5)


class FindMachinesTests(unittest.TestCase):
    def test_sweep_only_when_the_broadcast_finds_nothing(self):
        hit = [{"ip": "10.0.0.5", "mac": "x", "model": "1501ultra", "firmware": ""}]
        with mock.patch.object(protocol, "discover_machines", return_value=hit), \
             mock.patch.object(protocol, "sweep_for_machines") as sweep:
            self.assertEqual(protocol.find_machines(), hit)
            sweep.assert_not_called()
        said = []
        with mock.patch.object(protocol, "discover_machines", return_value=[]), \
             mock.patch.object(protocol, "sweep_for_machines", return_value=["swept"]) as sweep:
            self.assertEqual(protocol.find_machines(said.append), ["swept"])
            sweep.assert_called_once()
            self.assertEqual(sweep.call_args.kwargs["progress"], said.append)


if __name__ == "__main__":
    unittest.main()
