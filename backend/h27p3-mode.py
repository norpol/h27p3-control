#!/usr/bin/env python3
"""Switch the tested KTC H27P3 EUR-1.0.1 between work and gaming modes.

Writes two mode-setting bytes, then restarts the monitor (about 5 seconds blank).
Requires Python stdlib, ddcutil for discovery, and access to the monitor I2C bus.
No firmware flashing, arbitrary register writes, or downloaded code execution.
"""
import argparse
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

READER_PATH = Path(__file__).resolve().with_name('h27p3-read-eeprom.py')
_spec = importlib.util.spec_from_file_location('h27p3_reader', READER_PATH)
reader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reader)

MODES = {'work': (1, 0), 'game': (2, 1)}
SERIALS = {'work': '0000000000000', 'game': '0000000000001'}
READ_VERSION = bytes.fromhex('03 7b 39 3b')
READ_BUILD = bytes.fromhex('03 7b 39 3a')
FACTORY_QUERY = bytes.fromhex('c0 11 01')
FACTORY_UNLOCK = bytes.fromhex('c0 11 03')
FACTORY_CLEAR = bytes.fromhex('c0 11 00')
RESTART = bytes.fromhex('cc fb')


def discover_bus():
    result = subprocess.run(['ddcutil', 'detect', '--brief'], capture_output=True,
                            text=True, timeout=30, check=True)
    candidates = []
    bus = None
    for line in result.stdout.splitlines():
        match = re.search(r'I2C bus:\s+/dev/i2c-(\d+)', line)
        if match:
            bus = int(match.group(1))
        if re.search(r'Monitor:\s+SKG:H27P3:', line) and bus is not None:
            candidates.append(bus)
            bus = None
    if len(candidates) != 1:
        raise RuntimeError('Expected exactly one H27P3; use --bus only if you know its DDC bus')
    return candidates[0]


def mode_from(identity, settings):
    for mode, pair in MODES.items():
        if identity['serial'] == SERIALS[mode] and (settings[0x9b], settings[0xbe]) == pair:
            return mode
    raise RuntimeError('EDID and stored mode do not agree; wake the screen and retry')


class Monitor:
    def __init__(self, bus, record):
        self.fd = os.open(f'/dev/i2c-{bus}', os.O_RDWR)
        self.record = record

    def close(self):
        os.close(self.fd)

    def identity(self):
        return reader.read_identity(self.fd)

    def xfer(self, payload):
        fixed = {READ_VERSION, READ_BUILD, FACTORY_QUERY, FACTORY_UNLOCK, FACTORY_CLEAR, RESTART}
        mode_write = (len(payload) == 6 and payload[:4] == b'\xc0\x02\x01\x00' and
                      ((payload[4] == 0x9b and payload[5] in (1, 2)) or
                       (payload[4] == 0xbe and payload[5] in (0, 1))))
        if payload not in fixed and not mode_write:
            raise ValueError('Command is outside the fixed mode-switch allowlist')
        packet = bytes((0x51, 0x80 | len(payload))) + payload
        packet += bytes((reader.xor(packet, 0x6e),))
        fcntl.ioctl(self.fd, 0x0703, 0x37)
        self.record('request', packet=packet.hex())
        if os.write(self.fd, packet) != len(packet):
            raise RuntimeError('Short DDC write')
        time.sleep(0.2)
        raw = os.read(self.fd, 32)
        if len(raw) < 3:
            raise RuntimeError('Short DDC response')
        frame = raw[:(raw[1] & 127) + 3]
        if len(frame) != (raw[1] & 127) + 3 or frame[0] != 0x6e or reader.xor(frame, 0x50):
            raise RuntimeError('Invalid DDC response checksum/length')
        self.record('response', frame=frame.hex())
        return frame[2:-1]

    def factory(self, command):
        expected = b'\xc1\x11' + (b'\x03' if command == FACTORY_UNLOCK else b'\x00')
        if self.xfer(command) != expected:
            raise RuntimeError('Factory-access state was not confirmed')

    def write_mode_byte(self, offset, value):
        if self.xfer(bytes((0xc0, 2, 1, 0, offset, value))) != bytes.fromhex('c1 02 01'):
            raise RuntimeError('Mode-setting write was not acknowledged')

    def read_mode_pair(self):
        a, _ = reader.read_chunk(self.fd, 0x90, 16)
        b, _ = reader.read_chunk(self.fd, 0xb0, 16)
        return a[11], b[14]

    def settings(self):
        data = bytearray()
        for offset in range(0, 256, 16):
            block, packet = reader.read_chunk(self.fd, offset, 16)
            data.extend(block)
        return bytes(data)

    def restart(self):
        try:
            self.xfer(RESTART)
        except OSError as error:
            # The restart may interrupt the response. Success is decided by
            # the subsequent identity and EEPROM reads, never transmission.
            self.record('restart_response_interrupted', error=str(error))

    def wait_for_mode(self, mode):
        time.sleep(3)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                identity = self.identity()
                if identity['serial'] == SERIALS[mode]:
                    self.record('identity_after', identity=identity)
                    return
            except (OSError, RuntimeError):
                pass
            time.sleep(0.5)
        raise RuntimeError('Monitor did not return with the requested EDID within 23 seconds')


def switch(monitor, requested, record):
    identity = monitor.identity()  # Strict live SKG/product/model guard.
    record('identity_before', identity=identity)
    if monitor.xfer(READ_VERSION) != b'EUR-1.0.1\x00' or monitor.xfer(READ_BUILD) != b'KSDA25040451\x00':
        raise RuntimeError('Only firmware EUR-1.0.1 / KSDA25040451 has been tested')
    original = monitor.settings()
    record('settings_before', hex=original.hex())  # Durable before any setting write.
    if len(original) != 256 or original[0] != 0x74 or original[0x58] & 2:
        raise RuntimeError('Unexpected settings layout or factory mode already active')
    current = mode_from(identity, original)
    target = ('game' if current == 'work' else 'work') if requested == 'toggle' else requested
    if requested == 'status' or target == current:
        return current, False
    if monitor.identity() != identity:
        raise RuntimeError('Monitor identity changed during capture; nothing was written')
    monitor.factory(FACTORY_QUERY)
    attempted_unlock = False
    attempted_write = False
    restart_attempted = False
    try:
        attempted_unlock = True
        monitor.factory(FACTORY_UNLOCK)
        attempted_write = True
        for offset, value in zip((0x9b, 0xbe), MODES[target]):
            monitor.write_mode_byte(offset, value)
        if monitor.read_mode_pair() != MODES[target]:
            raise RuntimeError('Stored mode failed read-back verification')
        monitor.factory(FACTORY_CLEAR)
        attempted_unlock = False
        restart_attempted = True
        monitor.restart()
    except BaseException:
        # Before a restart, restore only the two known mode bytes. Reboot to
        # discard the unlock handler's temporary brightness change. Do not
        # replay a whole snapshot or guess at recovery after a restart.
        if not restart_attempted and attempted_unlock:
            try:
                if attempted_write:
                    for offset in (0x9b, 0xbe):
                        monitor.write_mode_byte(offset, original[offset])
                    record('rollback_mode_bytes_complete')
            finally:
                try:
                    monitor.factory(FACTORY_CLEAR)
                finally:
                    monitor.restart()
        raise
    monitor.wait_for_mode(target)
    after = monitor.settings()
    changes = [{'offset': i, 'before': a, 'after': b}
               for i, (a, b) in enumerate(zip(original, after)) if a != b]
    record('settings_after', hex=after.hex(), changes=changes)
    if mode_from(monitor.identity(), after) != target:
        raise RuntimeError('Requested mode did not persist after restart')
    unexpected = [c for c in changes if c['offset'] not in {0x89, 0x8a, 0x8b, 0x8c, 0x9b, 0xbe}]
    if unexpected:
        raise RuntimeError(f'Mode switched but other settings changed: {unexpected}; inspect the saved snapshot')
    monitor.factory(FACTORY_QUERY)
    return target, True


def notify(message):
    try:
        subprocess.run(['notify-send', 'KTC H27P3', message], check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('status', 'work', 'game', 'toggle'), nargs='?', default='status')
    parser.add_argument('--bus', type=int, help='Override DDC bus discovery; identity checks still apply')
    parser.add_argument('--notify', action='store_true', help='Show the result as a desktop notification')
    args = parser.parse_args()
    if args.bus is not None and args.bus < 0:
        parser.error('--bus must be nonnegative')
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'h27p3'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Lock remains held through post-restart verification. No delayed toggles.
    with (state / 'switch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('A monitor command is already running.', file=sys.stderr)
            return 1
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        log_path = state / f'{stamp}.jsonl'
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(log_fd, 'w') as log:
            def record(event, **details):
                item = {'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event': event, **details}
                log.write(json.dumps(item) + '\n'); log.flush(); os.fsync(log.fileno())
            monitor = None
            try:
                bus = args.bus if args.bus is not None else discover_bus()
                monitor = Monitor(bus, record)
                record('start', bus=bus, requested=args.mode)
                mode, changed = switch(monitor, args.mode, record)
                description = '5K / 60 Hz' if mode == 'work' else '1440p / 120 Hz'
                message = f'{"Switched to" if changed else "Active:"} {description}.'
                record('success', mode=mode, changed=changed)
                print(message)
                if args.notify:
                    notify(message)
                return 0
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                record('error', error=str(error))
                message = f'Monitor command failed: {error}'
                print(message, file=sys.stderr)
                if args.notify:
                    notify(message)
                return 1
            finally:
                if monitor is not None:
                    monitor.close()
                print(f'Capture: {log_path}')


if __name__ == '__main__':
    sys.exit(main())
