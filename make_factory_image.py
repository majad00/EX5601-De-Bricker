#!/usr/bin/env python3
"""
Written by Qureshi Majad as part of projcts, at lut.fi

better dont use this script is there for test only not to edit factory image

Create an emergency EX5601-T0 factory image from a donor factory backup.

This does NOT create RF calibration from scratch.
It only copies a donor factory partition and replaces detected MAC addresses.

Usage:
  python make_factory_image.py donor_factory.bin factory_randomized.bin

Output:
  factory_randomized.bin, exactly 0x200000 bytes
"""

import argparse
import os
import random
import re
import sys
from pathlib import Path


FACTORY_SIZE = 0x200000


def make_laa_mac() -> bytes:
    mac = bytearray(os.urandom(6))

    # Clear multicast bit, set locally-administered bit.
    mac[0] = (mac[0] & 0xFE) | 0x02

    return bytes(mac)


def mac_to_text(mac: bytes) -> str:
    return ":".join(f"{b:02x}" for b in mac)


def mac_to_ascii_variants(mac: bytes):
    lower_colon = mac_to_text(mac)
    upper_colon = lower_colon.upper()
    lower_dash = lower_colon.replace(":", "-")
    upper_dash = lower_dash.upper()

    return [
        lower_colon.encode("ascii"),
        upper_colon.encode("ascii"),
        lower_dash.encode("ascii"),
        upper_dash.encode("ascii"),
    ]


def looks_like_mac(raw: bytes) -> bool:
    if len(raw) != 6:
        return False

    # Reject multicast.
    if raw[0] & 0x01:
        return False

    # Reject all zero / all FF.
    if raw == b"\x00" * 6:
        return False

    if raw == b"\xff" * 6:
        return False

    # Reject very common non-real patterns.
    if raw in {
        b"\x11\x22\x33\x44\x55\x66",
        b"\x00\x11\x22\x33\x44\x55",
        b"\x12\x34\x56\x78\x9a\xbc",
    }:
        return False

    return True


def find_binary_macs(data: bytes):
    hits = []

    for i in range(0, len(data) - 6 + 1):
        raw = data[i:i + 6]

        if looks_like_mac(raw):

            # contain lots of binary data. We only report, not auto-replace all.
            hits.append((i, raw))

    return hits


def find_ascii_macs(data: bytes):
    pattern = re.compile(
        rb"(?i)\b[0-9a-f]{2}[:-][0-9a-f]{2}[:-][0-9a-f]{2}[:-]"
        rb"[0-9a-f]{2}[:-][0-9a-f]{2}[:-][0-9a-f]{2}\b"
    )

    return [(m.start(), m.group(0)) for m in pattern.finditer(data)]


def replace_ascii_macs(data: bytearray, base_mac: bytes):
    """
    Replace ASCII MAC strings only.

    This is safer than replacing every binary-looking 6-byte pattern.
    Binary calibration areas may contain checksummed structures.
    """
    replacements = []

    ascii_hits = find_ascii_macs(bytes(data))

    generated = [
        base_mac,
        increment_mac(base_mac, 1),
        increment_mac(base_mac, 2),
        increment_mac(base_mac, 3),
        increment_mac(base_mac, 4),
        increment_mac(base_mac, 5),
    ]

    for idx, (offset, old_text) in enumerate(ascii_hits):
        new_mac = generated[idx % len(generated)]

        if b"-" in old_text:
            new_text = mac_to_text(new_mac).replace(":", "-").encode("ascii")
        else:
            new_text = mac_to_text(new_mac).encode("ascii")

        if old_text.isupper():
            new_text = new_text.upper()

        data[offset:offset + len(old_text)] = new_text
        replacements.append((offset, old_text.decode(), new_text.decode()))

    return replacements


def increment_mac(mac: bytes, n: int) -> bytes:
    value = int.from_bytes(mac, "big")
    value = (value + n) & 0xFFFFFFFFFFFF

    out = bytearray(value.to_bytes(6, "big"))
    out[0] = (out[0] & 0xFE) | 0x02

    return bytes(out)


def main():
    parser = argparse.ArgumentParser(
        description="Create randomized emergency factory image from donor backup"
    )

    parser.add_argument("input", help="Input donor factory image, 2 MiB")
    parser.add_argument("output", help="Output randomized factory image, 2 MiB")
    parser.add_argument(
        "--base-mac",
        help="Optional base MAC, example 02:11:22:33:44:50",
    )
    parser.add_argument(
        "--replace-ascii-only",
        action="store_true",
        default=True,
        help="Replace ASCII MAC strings only. This is the safe default.",
    )

    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)

    data = bytearray(in_path.read_bytes())

    if len(data) != FACTORY_SIZE:
        print(
            f"ERROR: input size is {len(data)} bytes, expected "
            f"{FACTORY_SIZE} bytes / 0x{FACTORY_SIZE:X}"
        )
        return 1

    if args.base_mac:
        clean = args.base_mac.replace(":", "").replace("-", "")

        if len(clean) != 12:
            print("ERROR: invalid --base-mac")
            return 1

        base_mac = bytes.fromhex(clean)
        base_mac = bytes([(base_mac[0] & 0xFE) | 0x02]) + base_mac[1:]
    else:
        base_mac = make_laa_mac()

    print("Base randomized MAC:", mac_to_text(base_mac))
    print("Suggested sequence:")
    print("  LAN:     ", mac_to_text(base_mac))
    print("  WAN:     ", mac_to_text(increment_mac(base_mac, 1)))
    print("  WiFi 2G: ", mac_to_text(increment_mac(base_mac, 2)))
    print("  WiFi 5G: ", mac_to_text(increment_mac(base_mac, 3)))
    print()

    ascii_hits = find_ascii_macs(bytes(data))
    binary_hits = find_binary_macs(bytes(data))

    print(f"ASCII MAC-like strings found: {len(ascii_hits)}")
    print(f"Binary MAC-like patterns found: {len(binary_hits)}")
    print()

    replacements = replace_ascii_macs(data, base_mac)

    if replacements:
        print("ASCII replacements:")
        for offset, old, new in replacements:
            print(f"  0x{offset:06X}: {old} -> {new}")
    else:
        print("No ASCII MAC strings replaced.")
        print("This factory image may store MACs in binary calibration structures.")
        print("Do not blindly replace binary patterns unless offsets are known.")

    out_path.write_bytes(data)

    print()
    print(f"Wrote: {out_path}")
    print(f"Size:  {len(data)} bytes")
    print()
    print("IMPORTANT:")
    print("This is not a true per-device factory restore.")
    print("It keeps donor RF calibration and only changes visible ASCII MACs.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())