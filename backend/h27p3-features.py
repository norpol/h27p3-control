#!/usr/bin/env python3
"""Control the tested H27P3 gaming overlays and black equalizer over DDC/CI.

Uses normal Set/Get VCP commands. No factory access, EEPROM writes, or restart.
Selecting crosshair, refresh-rate counter, or timer disables the other overlays.
"""
import argparse
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

_spec = importlib.util.spec_from_file_location(
    'h27p3_mode', Path(__file__).resolve().with_name('h27p3-mode.py'))
mode = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mode)

# Only controls whose handlers were located in official H27P3 firmware.
FEATURES = {'crosshair': (0xd8, 6), 'fps': (0xe4, 1),
            'timer': (0xaf, 5), 'black-equalizer': (0x2e, 100)}
TIMER_VALUES = (0, 15, 30, 45, 60, 90)
OVERLAYS = ('crosshair', 'fps', 'timer')


def target_value(feature, value, current):
    if feature == 'crosshair':
        if value == 'toggle':
            return 0 if current else 1
        if value == 'cycle':
            return (current + 1) % 7
        value = {'off': '0', 'on': '1'}.get(value, value)
    elif feature == 'fps':
        if value == 'toggle':
            return 0 if current else 1
        value = {'off': '0', 'on': '1'}.get(value, value)
    elif feature == 'timer':
        value = '0' if value == 'off' else value
    number = int(value)
    if feature == 'timer':
        if number not in TIMER_VALUES:
            raise ValueError('Timer must be off, 15, 30, 45, 60, or 90 minutes')
        return TIMER_VALUES.index(number)
    if not 0 <= number <= FEATURES[feature][1]:
        raise ValueError(f'{feature} must be between 0 and {FEATURES[feature][1]}')
    if feature == 'black-equalizer' and number % 5:
        raise ValueError('Black equalizer uses steps of 5 (0, 5, ..., 100)')
    return number


class Monitor(mode.Monitor):
    def packet(self, payload):
        packet = bytes((0x51, 0x80 | len(payload))) + payload
        packet += bytes((mode.reader.xor(packet, 0x6e),))
        fcntl.ioctl(self.fd, 0x0703, 0x37)
        self.record('vcp_request', packet=packet.hex())
        if os.write(self.fd, packet) != len(packet):
            raise RuntimeError('Short DDC request')
        time.sleep(0.2)

    def get_feature(self, feature):
        code, expected_max = FEATURES[feature]
        for attempt in range(3):
            self.packet(bytes((1, code)))
            raw = os.read(self.fd, 32)
            if len(raw) < 3:
                raise RuntimeError('Short DDC response')
            frame = raw[:(raw[1] & 127) + 3]
            if (len(frame) != (raw[1] & 127) + 3 or frame[0] != 0x6e or
                    mode.reader.xor(frame, 0x50)):
                raise RuntimeError('Invalid DDC response checksum/length')
            self.record('vcp_response', feature=feature, frame=frame.hex())
            if frame == bytes.fromhex('6e80be'):
                time.sleep(0.3)
                continue  # Only retry idempotent reads, never writes.
            data = frame[2:-1]
            if len(data) != 8 or data[0] != 2 or data[1] != 0 or data[2] != code:
                raise RuntimeError(f'{feature}: unsupported or mismatched VCP reply')
            maximum = int.from_bytes(data[4:6], 'big')
            current = int.from_bytes(data[6:8], 'big')
            if maximum != expected_max or not 0 <= current <= maximum:
                raise RuntimeError(f'{feature}: unexpected range/current {current}/{maximum}')
            return current
        raise RuntimeError('Monitor returned only null replies; wake the screen and retry')

    def set_feature(self, feature, value):
        code, maximum = FEATURES[feature]
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError('Value outside feature allowlist')
        if feature == 'black-equalizer' and value % 5:
            raise ValueError('Black equalizer uses steps of 5')
        self.packet(bytes((3, code, 0, value)))
        time.sleep(0.3)
        actual = self.get_feature(feature)
        if actual != value:
            raise RuntimeError(f'{feature}: requested {value}, monitor reports {actual}')

    def status(self):
        return {feature: self.get_feature(feature) for feature in FEATURES}


def describe(feature, value):
    if feature == 'crosshair':
        return 'off' if not value else f'style {value}'
    if feature == 'fps':
        return 'on (refresh-rate counter)' if value else 'off'
    if feature == 'timer':
        return f'{TIMER_VALUES[value]} minutes' if value else 'off'
    return str(value)


def operate(monitor, feature, value, record):
    identity = monitor.identity()
    record('identity', identity=identity)
    if (monitor.xfer(mode.READ_VERSION) != b'EUR-1.0.1\x00' or
            monitor.xfer(mode.READ_BUILD) != b'KSDA25040451\x00'):
        raise RuntimeError('Only firmware EUR-1.0.1 / KSDA25040451 has been tested')
    before = monitor.status()
    record('features_before', values=before)
    if feature == 'status':
        return before
    target = target_value(feature, value, before[feature])
    if feature not in OVERLAYS and before[feature] == target:
        return before
    if monitor.identity() != identity:
        raise RuntimeError('Monitor identity changed; nothing was written')
    # Firmware keeps old overlay selections even when another overlay replaces
    # them. Normalize these preferences, and reassert explicit overlay requests
    # even if read-back already matches: stored selection is not visual proof.
    if feature in OVERLAYS and target:
        for other in OVERLAYS:
            if other != feature and before[other]:
                monitor.set_feature(other, 0)
    monitor.set_feature(feature, target)
    after = monitor.status()
    record('features_after', values=after)
    if feature in OVERLAYS and target and any(after[other] for other in OVERLAYS if other != feature):
        raise RuntimeError('Competing overlay remained enabled; inspect the saved capture')
    return after


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Examples: monitor-features crosshair 1; monitor-features crosshair off; '
        'monitor-features fps on; monitor-features timer 15; '
        'monitor-features black-equalizer 50'))
    parser.add_argument('feature', nargs='?', default='status', choices=['status', *FEATURES])
    parser.add_argument('value', nargs='?', help=(
        'crosshair: off/on/toggle/cycle/0..6; fps: off/on/toggle; '
        'timer: off/15/30/45/60/90; black-equalizer: 0..100 in steps of 5'))
    parser.add_argument('--bus', type=int)
    parser.add_argument('--notify', action='store_true')
    args = parser.parse_args()
    if ((args.feature == 'status') != (args.value is None) or
            (args.bus is not None and args.bus < 0)):
        parser.error('Give a feature and value, or use status without a value; bus must be nonnegative')
    if args.value is not None:
        try:
            target_value(args.feature, args.value, 0)
        except ValueError as error:
            parser.error(str(error))
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'h27p3'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / 'switch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('A monitor command is already running.', file=sys.stderr)
            return 1
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        log_path = state / f'{stamp}-features.jsonl'
        with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as log:
            def record(event, **details):
                log.write(json.dumps({'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                      'event': event, **details}) + '\n')
                log.flush()
                os.fsync(log.fileno())
            monitor = None
            try:
                bus = args.bus if args.bus is not None else mode.discover_bus()
                monitor = Monitor(bus, record)
                record('start', bus=bus, feature=args.feature, value=args.value)
                result = operate(monitor, args.feature, args.value, record)
                names = FEATURES if args.feature == 'status' else [args.feature]
                message = '\n'.join(f'{name}: {describe(name, result[name])}' for name in names)
                print(message)
                record('success', values=result)
                if args.notify:
                    mode.notify(message)
                return 0
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                record('error', error=str(error))
                message = f'Monitor feature command failed: {error}'
                print(message, file=sys.stderr)
                if args.notify:
                    mode.notify(message)
                return 1
            finally:
                if monitor is not None:
                    monitor.close()
                print(f'Capture: {log_path}')


if __name__ == '__main__':
    sys.exit(main())
