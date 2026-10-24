"""Offline failure-path tests: no monitor I/O occurs in this suite."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('mode', Path(__file__).with_name('h27p3-mode.py'))
mode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mode)


class FakeMonitor:
    def __init__(self, current='work'):
        self.data = bytearray(256)
        self.data[0] = 0x74
        self.data[0x58] = 0x41
        self.data[0x8d] = 30
        self.data[0x9b], self.data[0xbe] = mode.MODES[current]
        self.current = current
        self.version = b'EUR-1.0.1\0'
        self.unlocked = False
        self.writes = []
        self.restarts = 0
        self.fail_once = False
        self.corrupt_on_restart = False
        self.interrupt_once = False

    def identity(self):
        return {'serial': mode.SERIALS[self.current]}

    def xfer(self, payload):
        if payload == mode.READ_VERSION:
            return self.version
        if payload == mode.READ_BUILD:
            return b'KSDA25040451\0'
        raise AssertionError('Unexpected request')

    def settings(self):
        return bytes(self.data)

    def factory(self, command):
        self.unlocked = command == mode.FACTORY_UNLOCK

    def write_mode_byte(self, offset, value):
        if not self.unlocked:
            raise AssertionError('Write without unlock')
        if offset not in (0x9b, 0xbe):
            raise AssertionError('Write outside the two mode bytes')
        self.writes.append((offset, value))
        self.data[offset] = value
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError('Simulated lost write acknowledgment')
        if self.interrupt_once:
            self.interrupt_once = False
            raise KeyboardInterrupt()

    def read_mode_pair(self):
        return self.data[0x9b], self.data[0xbe]

    def restart(self):
        self.restarts += 1
        self.current = 'game' if self.data[0xbe] else 'work'
        if self.corrupt_on_restart:
            self.data[0x8d] = 60

    def wait_for_mode(self, requested):
        if self.current != requested:
            raise RuntimeError('Wrong mode after restart')


def discard(*args, **kwargs):
    pass


class ModeSafety(unittest.TestCase):
    def test_round_trip_and_preserved_brightness(self):
        monitor = FakeMonitor()
        initial = monitor.settings()
        self.assertEqual(mode.switch(monitor, 'game', discard), ('game', True))
        self.assertEqual(mode.switch(monitor, 'work', discard), ('work', True))
        self.assertEqual(monitor.settings(), initial)
        self.assertFalse(monitor.unlocked)

    def test_no_writes_for_status_or_current_mode(self):
        monitor = FakeMonitor()
        mode.switch(monitor, 'status', discard)
        mode.switch(monitor, 'work', discard)
        self.assertEqual(monitor.writes, [])
        self.assertEqual(monitor.restarts, 0)

    def test_unknown_firmware_refused_before_unlock(self):
        monitor = FakeMonitor()
        monitor.version = b'V1.0.2A\0'
        with self.assertRaisesRegex(RuntimeError, 'Only firmware'):
            mode.switch(monitor, 'game', discard)
        self.assertFalse(monitor.unlocked)
        self.assertEqual(monitor.writes, [])

    def test_inconsistent_edid_and_eeprom_refused(self):
        monitor = FakeMonitor()
        monitor.current = 'game'
        with self.assertRaisesRegex(RuntimeError, 'do not agree'):
            mode.switch(monitor, 'toggle', discard)
        self.assertEqual(monitor.writes, [])

    def test_lost_ack_rolls_back_even_if_write_took_effect(self):
        monitor = FakeMonitor()
        initial = monitor.settings()
        monitor.fail_once = True
        with self.assertRaisesRegex(RuntimeError, 'lost write'):
            mode.switch(monitor, 'game', discard)
        self.assertEqual(monitor.settings(), initial)
        self.assertFalse(monitor.unlocked)
        self.assertEqual(monitor.restarts, 1)

    def test_keyboard_interrupt_cleans_up(self):
        monitor = FakeMonitor()
        initial = monitor.settings()
        monitor.interrupt_once = True
        with self.assertRaises(KeyboardInterrupt):
            mode.switch(monitor, 'game', discard)
        self.assertEqual(monitor.settings(), initial)
        self.assertFalse(monitor.unlocked)

    def test_unexpected_setting_change_not_reported_as_success(self):
        monitor = FakeMonitor()
        monitor.corrupt_on_restart = True
        with self.assertRaisesRegex(RuntimeError, 'other settings changed'):
            mode.switch(monitor, 'game', discard)
        self.assertFalse(monitor.unlocked)

    def test_arbitrary_write_rejected_before_io(self):
        monitor = mode.Monitor.__new__(mode.Monitor)
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            monitor.xfer(bytes.fromhex('c0 02 01 00 00 00'))
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            monitor.xfer(bytes.fromhex('cc f7'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
