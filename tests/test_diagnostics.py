import logging
import os
import tempfile
import unittest
from unittest import mock

from machine_files import __version__, diagnostics, protocol
from tests.stubs import StubDiscoveryMachine
from tests.test_protocol import free_udp_port


class DirectedBroadcastTests(unittest.TestCase):
    def test_one_per_network_link_local_skipped(self):
        self.assertEqual(protocol.directed_broadcasts(["192.168.0.108", "10.8.0.2", "169.254.3.4", "192.168.0.5"]),
                         ["192.168.0.255", "10.8.0.255"])
        self.assertEqual(protocol.directed_broadcasts([]), [])

    def test_scan_also_goes_to_each_network_and_logs_every_step(self):
        stub = StubDiscoveryMachine()
        try:
            # Stand in for "this computer's networks": the stub is reachable only through the extra target,
            # so finding it proves the scan went there too; 255.255.255.255 may or may not send here.
            with mock.patch.object(protocol, "local_ipv4_addresses", return_value=["127.0.0.1"]), \
                 mock.patch.object(protocol, "directed_broadcasts", return_value=["127.0.0.1"]), \
                 self.assertLogs("machine_files.protocol", level="DEBUG") as logs:
                found = protocol.discover_machines(target_port=stub.port, listen_port=free_udp_port(), window_seconds=0.5)
        finally:
            stub.stop()
        self.assertEqual([m["mac"] for m in found], ["AA:BB:CC:DD:EE:FF"])
        text = "\n".join(logs.output)
        self.assertIn("this computer's IPv4 addresses: 127.0.0.1", text)
        self.assertIn(f"round 1 sent to 127.0.0.1:{stub.port}", text)
        self.assertIn(f"round 3 sent to 127.0.0.1:{stub.port}", text)
        self.assertIn("listening on UDP", text)
        self.assertIn("reply from 127.0.0.1", text)
        self.assertIn("done — 1 machine(s)", text)

    def test_no_reply_says_what_to_check(self):
        with self.assertLogs("machine_files.protocol", level="INFO") as logs:
            protocol.discover_machines(target_port=free_udp_port(), listen_port=free_udp_port(),
                                       window_seconds=0.3, broadcast_ip="127.0.0.1")
        self.assertIn("no replies", "\n".join(logs.output))
        self.assertIn("firewall", "\n".join(logs.output))


class ReportTests(unittest.TestCase):
    def test_report_has_version_state_system_and_log(self):
        logging.getLogger("machine_files.test").addHandler(diagnostics._MemoryHandler())
        diagnostics._memory.append("2026-10-23 12:00:00 INFO machine_files.protocol: scan: done — 0 machine(s)")
        report = diagnostics.build_report([("This window", "Folder shown: /x")])
        for part in (f"OpenDecoDesk Machine Files {__version__}", "===== This window =====", "Folder shown: /x",
                     "===== App =====", "===== System =====", "This computer's IPv4 addresses",
                     "Where the scan is sent", "255.255.255.255", "scan: done — 0 machine(s)"):
            self.assertIn(part, report)

    def test_log_file_and_memory(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "app.log")
            with mock.patch.object(diagnostics, "_configured", False):
                logger = diagnostics.setup(path)
                logging.getLogger("machine_files.protocol").info("scan: hello from the test")
                for h in list(logger.handlers):
                    h.flush()
                    if isinstance(h, logging.FileHandler):
                        h.close()
                    logger.removeHandler(h)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn(f"OpenDecoDesk Machine Files {__version__} starting", text)
            self.assertIn("scan: hello from the test", text)
            self.assertIn("scan: hello from the test", "\n".join(diagnostics.recent_log()))

    def test_firewall_rules_kept_only_for_this_app(self):
        netsh = ("Rule Name: Other app\nProgram: C:\\Other\\x.exe\nAction: Allow\n\n"
                 "Rule Name: OpenDecoDesk Machine Files\nProgram: C:\\Tools\\OpenDecoDesk-Machine-Files-Windows.exe\n"
                 "Profiles: Private\nAction: Block\n\n")
        out = diagnostics._firewall_rules_for_this_app(netsh, "C:\\Tools\\OpenDecoDesk-Machine-Files-Windows.exe")
        self.assertIn("Action: Block", out)
        self.assertNotIn("Other app", out)
        self.assertEqual(diagnostics._firewall_rules_for_this_app("Rule Name: x\n", "C:\\a.exe"),
                         "(no firewall rule mentions this app)")

    def test_remote_desktop_noted_on_windows(self):
        with mock.patch.object(diagnostics.os, "name", "nt"), mock.patch.dict(os.environ, {"SESSIONNAME": "RDP-Tcp#3"}):
            self.assertEqual(diagnostics._rdp_note(), "yes (RDP-Tcp#3)")
        with mock.patch.object(diagnostics.os, "name", "nt"), mock.patch.dict(os.environ, {"SESSIONNAME": "Console"}):
            self.assertEqual(diagnostics._rdp_note(), "no (Console)")

    def test_windows_sections_run_the_right_commands(self):
        ran = []
        with mock.patch.object(diagnostics.os, "name", "nt"), \
             mock.patch.object(diagnostics, "_run", side_effect=lambda cmd, timeout=8: ran.append(cmd[0]) or "ok"):
            titles = [t for t, _ in diagnostics.system_report()]
        self.assertTrue(any("Network category" in t for t in titles))
        self.assertIn("Firewall rules for this app (inbound)", titles)
        self.assertEqual(ran, ["powershell", "netsh", "netsh", "ipconfig", "route"])


if __name__ == "__main__":
    unittest.main()
