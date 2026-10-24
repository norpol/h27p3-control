"""Safety and dependency tests; these never open the I2C device."""
import unittest
from unittest.mock import patch
import control


class FakeMonitor:
    def __init__(self):
        self.version = b'EUR-1.0.1\0'
        self.writes = []

    def identity(self):
        return {'serial': control.mode.SERIALS['work']}

    def xfer(self, command):
        return self.version if command == control.mode.READ_VERSION else b'KSDA25040451\0'

    def set_feature(self, key, value):
        self.writes.append((key, value))


class ControlSafety(unittest.TestCase):
    def test_reject_unsafe_values_before_io(self):
        monitor = control.Monitor.__new__(control.Monitor)
        with patch.object(monitor, 'packet') as packet:
            for key, value in [('overdrive', 0), ('crosshair', 7), ('blue-light', 10),
                               ('osd-style', 5), ('color-space', 5), ('brightness', 101)]:
                with self.assertRaises(ValueError):
                    monitor.set_feature(key, value)
            with self.assertRaises(KeyError):
                monitor.set_feature('firmware-upgrade', 1)
            packet.assert_not_called()

    def test_wrong_firmware_is_rejected(self):
        monitor = FakeMonitor()
        monitor.version = b'V1.0.2A\0'
        with self.assertRaisesRegex(RuntimeError, 'EUR-1.0.1'):
            control.guard(monitor)
        self.assertEqual(monitor.writes, [])

    def test_overlay_selection_clears_stale_competitors_first(self):
        monitor = FakeMonitor()
        control.apply(monitor, 'crosshair', 1, {'crosshair': 1, 'fps': 1, 'timer': 2}, lambda *a, **k: None)
        self.assertEqual(monitor.writes, [('fps', 0), ('timer', 0), ('crosshair', 1)])

    def test_missing_readback_or_color_dependency_prevents_write(self):
        monitor = FakeMonitor()
        cases = [('brightness', 40, {}), ('red-gain', 45, {'red-gain': 50, 'color-space': 0, 'color-temp': 1}),
                 ('gamma', 2, {'gamma': 3, 'color-space': 1}),
                 ('brightness', 40, {'brightness': 30, 'dcr': 1})]
        for key, value, before in cases:
            with self.assertRaises(RuntimeError):
                control.apply(monitor, key, value, before, lambda *a, **k: None)
        self.assertEqual(monitor.writes, [])

    def test_status_read_rejects_wrong_feature_and_bad_checksum(self):
        monitor = control.Monitor.__new__(control.Monitor)
        monitor.fd = 0
        monitor.record = lambda *a, **k: None
        # Valid checksum, but reply is for contrast while brightness was requested.
        packet = bytes.fromhex('6e880200120000640032')
        packet += bytes([control.mode.reader.xor(packet, 0x50)])
        with patch.object(monitor, 'packet'), patch.object(control.os, 'read', return_value=packet):
            with self.assertRaisesRegex(RuntimeError, 'supported reply'):
                monitor.get_feature('brightness')
        with patch.object(monitor, 'packet'), patch.object(control.os, 'read', return_value=packet[:-1] + bytes([packet[-1] ^ 1])):
            with self.assertRaisesRegex(RuntimeError, 'Invalid DDC'):
                monitor.get_feature('brightness')


if __name__ == '__main__':
    unittest.main()
