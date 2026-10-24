#!/usr/bin/env python3
"""Read H27P3 EEPROM through the observed C0/09 vendor DDC command.

Python standard library only. No EEPROM-write, unlock, reset, update, or
arbitrary-command interface. This is a research data capture, not a backup.
Recheck the I2C bus before use; output is created exclusively, never overwritten.
"""

import argparse
import datetime
import fcntl
import functools
import hashlib
import json
import operator
import os
from pathlib import Path
import time


def xor(data, initial):
    return functools.reduce(operator.xor, data, initial)


def read_identity(fd):
    fcntl.ioctl(fd, 0x0703, 0x50)
    if os.write(fd, b"\x00") != 1:
        raise RuntimeError("EDID pointer transfer failed")
    edid = os.read(fd, 128)
    if len(edid) != 128 or edid[:8] != bytes.fromhex("00ffffffffffff00") or sum(edid) % 256:
        raise RuntimeError("Invalid EDID")
    code = int.from_bytes(edid[8:10], "big")
    manufacturer = "".join(chr(64 + ((code >> shift) & 31)) for shift in (10, 5, 0))
    product = int.from_bytes(edid[10:12], "little")
    descriptors = {}
    for start in range(54, 126, 18):
        descriptor = edid[start:start + 18]
        if descriptor[:3] == b"\x00\x00\x00":
            descriptors[descriptor[3]] = descriptor[5:18].decode("ascii", "replace").strip("\n\x00 ")
    if (manufacturer, product, descriptors.get(0xFC)) != ("SKG", 10098, "H27P3"):
        raise RuntimeError("Refusing a device other than SKG/H27P3/product 10098")
    return {"manufacturer": manufacturer, "product": product,
            "model": descriptors[0xFC], "serial": descriptors.get(0xFF),
            "edid_hex": edid.hex()}


def read_chunk(fd, offset, count):
    if count != 16 or offset % 16 or not 0 <= offset <= 0x7F0:
        raise ValueError("Read must be an aligned 16-byte block in the first 2 KiB")
    payload = bytes((0xC0, 0x09, count, offset >> 8, offset & 255))
    packet = bytes((0x51, 0x80 | len(payload))) + payload
    packet += bytes((xor(packet, 0x6E),))
    fcntl.ioctl(fd, 0x0703, 0x37)
    for attempt in range(1, 4):
        if os.write(fd, packet) != len(packet):
            raise RuntimeError("Short DDC request")
        time.sleep(0.3)
        raw = os.read(fd, 32)
        time.sleep(0.2)
        # An authenticated DDC null reply can occur after a mode transition.
        # Repeat only this idempotent read; reject other unexpected responses.
        if raw[:3] != bytes.fromhex("6e80be") or attempt == 3:
            break
        time.sleep(0.4)
    if len(raw) < 3 or raw[0] != 0x6E or raw[1] != (0x80 | (count + 2)):
        raise RuntimeError(f"Unexpected DDC response at {offset:#x}: {raw.hex()}")
    frame = raw[:(raw[1] & 0x7F) + 3]
    if len(frame) != count + 5 or xor(frame, 0x50) or frame[2:4] != b"\xc1\x09":
        raise RuntimeError(f"Invalid EEPROM response at {offset:#x}: {frame.hex()}")
    return frame[4:-1], {"offset": offset, "request": packet.hex(),
                        "frame": frame.hex(), "attempts": attempt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, required=True)
    parser.add_argument("--mode", choices=("work", "gaming"), required=True)
    parser.add_argument("--length", type=int, choices=(256, 2048), default=256)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.bus < 0 or args.output.exists():
        parser.error("Bus must be nonnegative and output must not exist")
    expected_serial = "000000000000" + ("0" if args.mode == "work" else "1")
    fd = os.open(f"/dev/i2c-{args.bus}", os.O_RDWR)
    try:
        before = read_identity(fd)
        if before["serial"] != expected_serial:
            raise RuntimeError(f"Expected {args.mode} EDID serial {expected_serial}, got {before['serial']}")
        data = bytearray()
        records = []
        for offset in range(0, args.length, 16):
            block, record = read_chunk(fd, offset, 16)
            data.extend(block)
            records.append(record)
        after = read_identity(fd)
        if before != after:
            raise RuntimeError("EDID changed during capture; discard this measurement")
    finally:
        os.close(fd)
    result = {"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "bus": args.bus, "mode": args.mode, "identity": before,
              "length": len(data), "sha256": hashlib.sha256(data).hexdigest(),
              "hex": data.hex(), "records": records,
              "note": "Read-only EEPROM snapshot; not a firmware backup"}
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(f"Captured {len(data)} bytes in {args.mode} mode: {result['sha256']}")
    print(args.output)


if __name__ == "__main__":
    main()
