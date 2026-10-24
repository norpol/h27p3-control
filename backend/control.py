#!/usr/bin/env python3
"""Allowlisted H27P3 control service. Stdlib only; tab-separated GUI protocol."""
import argparse
from dataclasses import dataclass
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location('features', Path(__file__).with_name('h27p3-features.py'))
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)
mode = features.mode


@dataclass(frozen=True)
class Feature:
    code: int
    group: str
    label: str
    minimum: int = 0
    maximum: int = 100
    step: int = 1
    options: tuple = ()
    description: str = ''
    reply_max: int = None

    def validate(self, value):
        if type(value) is not int or not self.minimum <= value <= self.maximum:
            raise ValueError(f'{self.label}: value outside supported range')
        if (value - self.minimum) % self.step or (self.options and value not in dict(self.options)):
            raise ValueError(f'{self.label}: unsupported value')


OFF_ON = ((0, 'Off'), (1, 'On'))
CATALOG = {
    'brightness': Feature(0x10, 'Display', 'Brightness'),
    'contrast': Feature(0x12, 'Display', 'Contrast'),
    'sharpness': Feature(0x87, 'Display', 'Sharpness', maximum=10),
    'dcr': Feature(0xf3, 'Display', 'Dynamic contrast', maximum=1, options=OFF_ON,
                   description='Automatically adjusts brightness to the picture.'),
    'hdr': Feature(0xe9, 'Display', 'HDR', maximum=1, options=((0, 'Off'), (1, 'Auto')),
                   description='Auto allows HDR when the computer sends an HDR signal.'),
    'aspect': Feature(0xdb, 'Display', 'Aspect ratio', maximum=2,
                      options=((0, 'Full screen'), (1, '16:9'), (2, '4:3'))),
    'crosshair': Feature(0xd8, 'Gaming', 'Crosshair', maximum=6,
                        options=((0, 'Off'),) + tuple((i, f'Style {i}') for i in range(1, 7)),
                        description='Choosing a crosshair replaces the timer and refresh-rate counter.'),
    'fps': Feature(0xe4, 'Gaming', 'Refresh-rate counter', maximum=1, options=OFF_ON,
                   description='Shows monitor refresh rate, not the game’s rendered FPS.'),
    'timer': Feature(0xaf, 'Gaming', 'Countdown timer', maximum=5,
                     options=((0, 'Off'), (1, '15 minutes'), (2, '30 minutes'), (3, '45 minutes'),
                              (4, '60 minutes'), (5, '90 minutes')),
                     description='Applying a duration restarts the timer and replaces other overlays.'),
    'black-equalizer': Feature(0x2e, 'Gaming', 'Black equalizer', step=5,
                               description='Adjusts visibility in dark scenes. Neutral: 50.'),
    'overdrive': Feature(0xe0, 'Gaming', 'Overdrive', minimum=1, maximum=5,
                        options=((1, 'Off'), (2, 'Standard'), (3, 'Advanced'), (4, 'Ultra fast'), (5, 'Auto')),
                        description='Higher settings may introduce bright trails around moving objects.'),
    'adaptive-sync': Feature(0xcf, 'Gaming', 'Adaptive Sync', maximum=1, options=OFF_ON,
                             description='May briefly blank the screen and change overdrive. Also enable VRR on the computer.'),
    'color-space': Feature(0xf1, 'Color', 'Color space', maximum=4, reply_max=5,
                          options=((0, 'Native'), (1, 'sRGB'), (2, 'Adobe RGB'), (3, 'DCI-P3'), (4, 'BT.2020')),
                          description='Select Native to adjust gamma, temperature, and color channels.'),
    'gamma': Feature(0x72, 'Color', 'Gamma', maximum=4,
                    options=((0, 'Off'), (1, '1.8'), (2, '2.0'), (3, '2.2'), (4, '2.4'))),
    'color-temp': Feature(0x14, 'Color', 'Color temperature', minimum=1, maximum=4,
                         options=((1, 'Normal'), (2, 'Warm'), (3, 'Cool'), (4, 'User'))),
    'red-gain': Feature(0x16, 'Color', 'Red gain', description='Available with User color temperature.'),
    'green-gain': Feature(0x18, 'Color', 'Green gain', description='Available with User color temperature.'),
    'blue-gain': Feature(0x1a, 'Color', 'Blue gain', description='Available with User color temperature.'),
    'blue-light': Feature(0xfb, 'Color', 'Low blue light', step=25,
                         description='Reduces blue light in four steps.'),
    'volume': Feature(0x63, 'Audio & OSD', 'Headphone volume'),
    'mute': Feature(0x8d, 'Audio & OSD', 'Mute', maximum=1, options=OFF_ON),
    'osd-h': Feature(0x90, 'Audio & OSD', 'Menu horizontal position'),
    'osd-v': Feature(0xad, 'Audio & OSD', 'Menu vertical position'),
    'osd-timeout': Feature(0x3f, 'Audio & OSD', 'Menu timeout', minimum=15, maximum=60, step=15,
                          options=((15, '15 seconds'), (30, '30 seconds'), (45, '45 seconds'), (60, '60 seconds'))),
    'osd-transparency': Feature(0xa7, 'Audio & OSD', 'Menu transparency', maximum=5,
                               options=tuple((i, f'Level {i}') for i in range(6))),
    'osd-style': Feature(0xbe, 'Audio & OSD', 'Menu color', maximum=3, reply_max=5,
                        options=((0, 'Orange'), (1, 'Blue'), (2, 'Red'), (3, 'Green'))),
    'indicator': Feature(0xea, 'Audio & OSD', 'Power indicator', maximum=1, options=OFF_ON),
}
for channel, hue, saturation in [('Red', 0x9b, 0x59), ('Green', 0x9d, 0x5b), ('Blue', 0x9f, 0x5d),
                                 ('Cyan', 0x9e, 0x5c), ('Magenta', 0xa0, 0x5e), ('Yellow', 0x9c, 0x5a)]:
    for adjustment, code in [('hue', hue), ('saturation', saturation)]:
        CATALOG[f'{channel.lower()}-{adjustment}'] = Feature(
            code, 'Six-axis color', f'{channel} {adjustment}', description='Neutral: 50. Select Native color space first.')

OVERLAYS = ('crosshair', 'fps', 'timer')
COLOR_ADJUSTMENTS = {'gamma', 'color-temp', 'red-gain', 'green-gain', 'blue-gain', 'blue-light'} | {
    name for name, item in CATALOG.items() if item.group == 'Six-axis color'}


def emit(*parts):
    print('\t'.join(str(part).replace('\t', ' ').replace('\n', ' ') for part in parts), flush=True)


def catalog():
    for key, item in CATALOG.items():
        emit('feature', key, item.group, item.label, item.minimum, item.maximum, item.step,
             '|'.join(f'{value}={label}' for value, label in item.options), item.description)


class Monitor(features.Monitor):
    def get_feature(self, key):
        item = CATALOG[key]
        for attempt in range(3):
            self.packet(bytes((1, item.code)))
            raw = os.read(self.fd, 32)
            if len(raw) < 3:
                raise RuntimeError('Short DDC response')
            frame = raw[:(raw[1] & 127) + 3]
            if (len(frame) != (raw[1] & 127) + 3 or frame[0] != 0x6e or
                    mode.reader.xor(frame, 0x50)):
                raise RuntimeError('Invalid DDC response')
            self.record('vcp_response', feature=key, frame=frame.hex())
            if frame == bytes.fromhex('6e80be'):
                time.sleep(0.3)
                continue
            data = frame[2:-1]
            if len(data) != 8 or data[0] != 2 or data[1] != 0 or data[2] != item.code:
                raise RuntimeError('Control did not return a supported reply')
            maximum = int.from_bytes(data[4:6], 'big')
            value = int.from_bytes(data[6:8], 'big')
            if maximum != (item.reply_max if item.reply_max is not None else item.maximum):
                raise RuntimeError(f'Unexpected monitor range: {maximum}')
            item.validate(value)
            return value
        raise RuntimeError('Screen is not responding; wake it and refresh')

    def set_feature(self, key, value):
        item = CATALOG[key]  # Unknown keys fail before any I/O.
        item.validate(value)
        self.packet(bytes((3, item.code, value >> 8, value & 255)))
        time.sleep(0.3)
        actual = self.get_feature(key)
        if actual != value:
            raise RuntimeError(f'{item.label}: requested {value}, monitor reports {actual}. The active picture mode may restrict it.')

    def snapshot(self):
        values, errors = {}, {}
        for key in CATALOG:
            try:
                values[key] = self.get_feature(key)
            except (OSError, RuntimeError, ValueError) as error:
                errors[key] = str(error)
                if not values and len(errors) >= 3:
                    raise RuntimeError('Monitor is not responding. Wake the screen and refresh.') from error
        return values, errors


def guard(monitor):
    identity = monitor.identity()
    if (monitor.xfer(mode.READ_VERSION) != b'EUR-1.0.1\0' or
            monitor.xfer(mode.READ_BUILD) != b'KSDA25040451\0'):
        raise RuntimeError('This app supports H27P3 EUR-1.0.1 / KSDA25040451')
    if identity['serial'] not in mode.SERIALS.values():
        raise RuntimeError('Unrecognized H27P3 hardware mode')
    return identity


def apply(monitor, key, value, before, record):
    CATALOG[key].validate(value)
    if key not in before:
        raise RuntimeError('Control is unavailable; refresh the monitor first')
    if key in COLOR_ADJUSTMENTS and before.get('color-space') != 0:
        raise RuntimeError('Select Native color space before changing this control')
    if key.endswith('-gain') and before.get('color-temp') != 4:
        raise RuntimeError('Select User color temperature before adjusting RGB gain')
    if key == 'brightness' and before.get('dcr') == 1:
        raise RuntimeError('Turn off Dynamic contrast before adjusting brightness')
    record('setting_requested', feature=key, before=before[key], value=value)
    if key in OVERLAYS and value:
        for other in OVERLAYS:
            if other != key and before.get(other, 0):
                monitor.set_feature(other, 0)
    if key in OVERLAYS or before[key] != value:
        monitor.set_feature(key, value)


def run(args, record):
    bus = args.bus if args.bus is not None else mode.discover_bus()
    monitor = Monitor(bus, record)
    try:
        identity = guard(monitor)
        record('identity', identity=identity, bus=bus)
        if args.action == 'mode':
            selected, changed = mode.switch(monitor, args.key, record)
            record('mode_success', mode=selected, changed=changed)
            identity = guard(monitor)
        values, errors = monitor.snapshot()
        record('snapshot_before', values=values, errors=errors)
        if monitor.identity() != identity:
            raise RuntimeError('Monitor mode changed during the read; refresh and retry')
        if args.action == 'set':
            apply(monitor, args.key, args.value, values, record)
            values, errors = monitor.snapshot()
            record('snapshot_after', values=values, errors=errors)
            if values.get(args.key) != args.value:
                raise RuntimeError('The requested value did not remain active')
            if args.key in OVERLAYS and args.value and any(values.get(k, 0) for k in OVERLAYS if k != args.key):
                raise RuntimeError('The monitor kept a competing overlay enabled')
        emit('device', 'KTC H27P3', 'EUR-1.0.1', 'KSDA25040451', bus)
        emit('mode', next(key for key, serial in mode.SERIALS.items() if serial == monitor.identity()['serial']))
        for key, value in values.items():
            emit('value', key, value)
        for key, error in errors.items():
            emit('unavailable', key, error)
    finally:
        monitor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('catalog', 'status', 'set', 'mode'))
    parser.add_argument('key', nargs='?')
    parser.add_argument('value', nargs='?', type=int)
    parser.add_argument('--bus', type=int)
    args = parser.parse_args()
    if args.action == 'catalog':
        catalog()
        return 0
    if args.bus is not None and args.bus < 0:
        parser.error('Bus must be nonnegative')
    if args.action == 'set':
        if args.key not in CATALOG or args.value is None:
            parser.error('Select a known control and numeric value')
        try:
            CATALOG[args.key].validate(args.value)
        except ValueError as error:
            parser.error(str(error))
    elif args.action == 'mode' and args.key not in mode.MODES:
        parser.error('Mode must be work or game')
    elif args.action == 'status' and (args.key is not None or args.value is not None):
        parser.error('Status takes no value')
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'h27p3'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / 'switch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another monitor command is running. Try again when it finishes.', file=sys.stderr)
            return 1
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        log_path = state / f'{stamp}-app.jsonl'
        with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as log:
            def record(event, **details):
                log.write(json.dumps({'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                      'event': event, **details}) + '\n')
                log.flush()
                os.fsync(log.fileno())
            try:
                record('start', action=args.action, key=args.key, value=args.value)
                run(args, record)
                record('success')
                emit('capture', log_path)
                return 0
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                record('error', error=str(error))
                print(str(error), file=sys.stderr)
                print(f'Capture: {log_path}', file=sys.stderr)
                return 1


if __name__ == '__main__':
    sys.exit(main())
