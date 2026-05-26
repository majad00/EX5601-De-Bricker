#!/usr/bin/env python3
#Written by Qureshi Majad at lut.fi
"""
Written as part of T-56 de bricker project
EX5601-T0 de-bricker loader.

Default RAM rescue mode:
  python loader.py COM3

Optional NAND/default-boot monitor mode:
  python loader.py COM3 --nand-boot

Required files in same directory for default RAM rescue mode:
  payload_bl2.bin
  paylaod_FIP.bin
  payload_RESCUE.itb

What the default RAM rescue mode does:
  1. Catch MTK BootROM over UART.
  2. Upload BL2.
  3. Send FIP to BL2.
  4. Start U-Boot from RAM.
  5. Stop U-Boot autoboot/menu.
  6. Upload payload_RESCUE.itb to RAM with U-Boot loady/YMODEM.
  7. Run bootm 0x46000000.
  8. Listen/analyze forever.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import serial

from mtk_uart_common import (
    MtkProtocolError,
    hexdump,
    read_exact,
    read_u16be,
    send_and_expect_echo,
)
from mtk_uart_sync import wait_for_brom_sync
from mtk_uart_cmds import get_hw_code, get_target_config
from mtk_uart_da import send_da
from mtk_uart_baud import set_brom_uart_baud


ROUTER_MODEL = "Zyxel EX5601-T0 / T56 MT7986"

BL2_FILENAME = "payload_bl2.bin"

FIP_FILENAME = "paylaod_FIP.bin"

RESCUE_FIT_FILENAME = "payload_RESCUE.itb"

INITIAL_BAUD = 115200
BROM_LOAD_BAUDRATE = 460800
BL2_LOAD_BAUDRATE = 921600

BL2_LOAD_ADDR = 0x00201000

#  RAM address is important.
RESCUE_FIT_LOAD_ADDR = 0x46000000

SERIAL_READ_TIMEOUT = 0.01
SYNC_WAIT = 0.05
SYNC_DELAY = 0.005

CMD_JUMP_DA64 = bytes.fromhex("DE")

# Safer  test
# if, after basic YMODEM works, you can try 921600 by setting:
 #UBOOT_LOADY_BAUD = 921600
UBOOT_LOADY_BAUD = 460800
#UBOOT_LOADY_BAUD = 921600

def open_serial(port: str):
    print(f"Opening serial port: {port}")
    print(f"Router target: {ROUTER_MODEL}")
    print(f"Initial baud: {INITIAL_BAUD}, mode: 8N1")
    print()

    ser = serial.Serial(
        port=port,
        baudrate=INITIAL_BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=SERIAL_READ_TIMEOUT,
        write_timeout=3,
    )

    ser.dtr = False
    ser.rts = False

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    return ser


def load_file_from_script_dir(filename: str) -> bytes:
    from paths import read_payload_file, find_payload_file

    path = find_payload_file(filename)
    data = read_payload_file(filename)

    print(f"Using file: {path}")

    return data


def read_u32be(ser, timeout=2.0) -> int:
    data = read_exact(ser, 4, timeout=timeout)

    if len(data) != 4:
        raise MtkProtocolError(
            f"Expected 4 bytes, got {len(data)}: {hexdump(data)}"
        )

    return int.from_bytes(data, "big")

 
def jump_da64(ser, address: int):
    """
    AArch64 jump sequence used for MT7986 BL2.

    Protocol:
      DE
      address
      01      # AArch64
      status
      64      # magic decimal 100
      status
    """
    print()
    print("JUMP_DA64")
    print("Command: DE")
    print(f"Address: 0x{address:08X}")

    send_and_expect_echo(ser, CMD_JUMP_DA64, timeout=1.0)
    send_and_expect_echo(ser, address.to_bytes(4, "big"), timeout=1.0)

    send_and_expect_echo(ser, bytes([1]), timeout=1.0)

    status = read_u16be(ser, timeout=2.0)
    print(f"JUMP_DA64 status: 0x{status:04X}")

    if status != 0:
        raise MtkProtocolError(f"JUMP_DA64 failed, status=0x{status:04X}")

    send_and_expect_echo(ser, bytes([100]), timeout=1.0)

    status = read_u16be(ser, timeout=2.0)
    print(f"JUMP_DA64 magic status: 0x{status:04X}")

    if status != 0:
        raise MtkProtocolError(f"JUMP_DA64 magic failed, status=0x{status:04X}")

    print("JUMP_DA64 accepted. BL2 should start now.")
    print()

 
def wait_for_text_line(ser, pattern: bytes, timeout: float = 30.0):
    """
    Wait for a full UART line containing pattern.
    Consumes the full line to avoid corrupting the following BL2 handshake.
    """
    print(f"Waiting for text: {pattern.decode(errors='replace')}")
    print("UART output:")
    print("=" * 70)

    old_timeout = ser.timeout
    ser.timeout = 0.5

    deadline = time.monotonic() + timeout
    collected = bytearray()
    current_line = bytearray()
    matched = False

    try:
        while time.monotonic() < deadline:
            b = ser.read(1)

            if not b:
                continue

            collected += b
            current_line += b

            print(b.decode("utf-8", errors="replace"), end="", flush=True)

            if b in (b"\n", b"\r"):
                if pattern in current_line:
                    matched = True
                    break

                current_line.clear()

        print()
        print("=" * 70)

        if matched:
            print("Matched expected text.")
            print()
            return True, bytes(collected)

        print("Timeout waiting for expected text.")
        print(f"Collected {len(collected)} byte(s).")

        if collected:
            print("Collected hex:")
            print(hexdump(bytes(collected)))

        print()
        return False, bytes(collected)

    finally:
        ser.timeout = old_timeout


def drain_uart_until_quiet(ser, quiet_time: float = 0.75, max_time: float = 4.0):
    """
    Drain leftover BL2 banner text before starting binary mudl/TF-A handshake.
    """
    print("Draining trailing UART text before handshake...")

    old_timeout = ser.timeout
    ser.timeout = 0.05

    start = time.monotonic()
    last_rx = time.monotonic()
    drained = bytearray()

    try:
        while time.monotonic() - start < max_time:
            data = ser.read(1)

            if data:
                drained += data
                last_rx = time.monotonic()
                print(data.decode("utf-8", errors="replace"), end="", flush=True)
            else:
                if time.monotonic() - last_rx >= quiet_time:
                    break

        if drained:
            print()
            print(f"Drained {len(drained)} trailing byte(s).")
        else:
            print("No trailing bytes to drain.")

        print("UART is quiet. Starting handshake.")
        print()

    finally:
        ser.timeout = old_timeout


def bl2_handshake(ser):
    """
    BL2 UART download handshake:
      host sends: mudl
      BL2 replies: TF-A
    """
    drain_uart_until_quiet(ser)

    print("Starting BL2 UART-download handshake: mudl -> TF-A")

    req = b"mudl"
    resp = b"TF-A"

    old_timeout = ser.timeout
    ser.timeout = 0.5

    matched = 0
    deadline = time.monotonic() + 10.0
    rx_log = bytearray()

    try:
        while matched < len(req) and time.monotonic() < deadline:
            tx = req[matched:matched + 1]

            ser.write(tx)
            ser.flush()

            rx = ser.read(1)

            if not rx:
                print(
                    f"BL2 handshake byte {matched}: "
                    f"TX {tx.hex(' ').upper()} -> RX timeout"
                )
                continue

            rx_log += rx

            print(
                f"BL2 handshake byte {matched}: "
                f"TX {tx.hex(' ').upper()} -> RX {rx.hex(' ').upper()}"
            )

            if rx == resp[matched:matched + 1]:
                matched += 1
            else:
                time.sleep(0.05)

        if matched != len(req):
            raise MtkProtocolError(
                "BL2 handshake failed. "
                f"Matched {matched}/4 bytes. RX log: {hexdump(bytes(rx_log))}"
            )

        time.sleep(0.20)
        ser.reset_input_buffer()

        print("BL2 handshake OK.")
        print()

    finally:
        ser.timeout = old_timeout


def bl2_version(ser) -> int:
    print("Reading BL2 UART-DL version")

    send_and_expect_echo(ser, bytes([1]), timeout=2.0)

    ver = ser.read(1)

    if len(ver) != 1:
        raise MtkProtocolError("Failed to read BL2 version byte.")

    version = ver[0]

    print(f"BL2 UART-DL version: 0x{version:02X}")
    print()

    return version


def bl2_set_baudrate(ser, baud: int):
    print(f"Setting BL2 UART baud to {baud}")

    send_and_expect_echo(ser, bytes([2]), timeout=2.0)
    send_and_expect_echo(ser, baud.to_bytes(4, "big"), timeout=2.0)

    time.sleep(0.05)

    ser.baudrate = baud

    time.sleep(0.10)

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print(f"Host COM baud changed to {baud}")
    print()


def fip_packet_checksum(data: bytes) -> int:
    csum = 0
    p = 0

    while len(data) - p > 1:
        csum += int.from_bytes(data[p:p + 2], "big")
        p += 2

    if len(data) != p:
        csum += data[-1] << 8

    while csum >> 16:
        csum = ((csum >> 16) & 0xFFFF) + (csum & 0xFFFF)

    return csum & 0xFFFF


def bl2_send_fip_packet(ser, idx: int, chunk: bytes) -> bool:
    checksum = fip_packet_checksum(chunk)

    send_and_expect_echo(ser, idx.to_bytes(4, "big"), timeout=2.0)
    send_and_expect_echo(ser, len(chunk).to_bytes(2, "big"), timeout=2.0)
    send_and_expect_echo(ser, checksum.to_bytes(2, "big"), timeout=2.0)

    ser.write(chunk)
    ser.flush()

    expected_idx = read_u32be(ser, timeout=5.0)
    real_checksum = read_u16be(ser, timeout=5.0)

    if expected_idx != idx:
        print(f"Incorrect packet index: got {expected_idx}, expected {idx}")
        return False

    if real_checksum != checksum:
        print(
            f"Incorrect checksum: got 0x{real_checksum:04X}, "
            f"expected 0x{checksum:04X}"
        )
        return False

    return True


def bl2_send_fip(ser, fip: bytes):
    print("Sending FIP to BL2")
    print(f"FIP size: {len(fip)} byte(s)")

    send_and_expect_echo(ser, bytes([3]), timeout=2.0)
    send_and_expect_echo(ser, len(fip).to_bytes(4, "big"), timeout=2.0)

    idx = 0
    pkt_len = 128
    pos = 0

    while len(fip) - pos > pkt_len:
        chunk = fip[pos:pos + pkt_len]

        print(f"FIP packet {idx}: offset={pos}, size={len(chunk)}")

        ok = bl2_send_fip_packet(ser, idx, chunk)

        if ok:
            idx += 1
            pos += pkt_len

            if pkt_len < 32768:
                pkt_len *= 2
            elif pkt_len < 65536 - 1024:
                pkt_len += 1024
        else:
            print("Retrying same packet...")

    final_chunk = fip[pos:]

    while True:
        print(f"FIP final packet {idx}: offset={pos}, size={len(final_chunk)}")

        ok = bl2_send_fip_packet(ser, idx, final_chunk)

        if ok:
            break

        print("Retrying final packet...")

    print("FIP sent.")
    print()


def bl2_go(ser):
    print("Sending BL2 GO command")

    send_and_expect_echo(ser, bytes([4]), timeout=2.0)

    print("BL2 GO sent.")
    print()
    
# U-Boot controller starting


PROMPT_PATTERNS = [
    b"EX5601-DEBRICKER>",
    b"EX5601>",
    b"MT7986>",
    b"OpenWrt>",
    b"ZHAL>",
]

def has_prompt(buf: bytes) -> bool:
    tail = strip_ansi_for_match(buf[-1024:])
    tail = tail.rstrip()

    for pattern in PROMPT_PATTERNS:
        if tail.endswith(pattern):
            return True

    return False
    
    
def strip_ansi_for_match(data: bytes) -> bytes:
    """
    Not a full ANSI parser. Just keeps matching simple by removing ESC bytes.
    """
    return data.replace(b"\x1b", b"")


def stop_uboot_autoboot_to_prompt(ser, timeout: float = 45.0):
    """
    Stop OpenWrt U-Boot autoboot/menu and reach the EX5601> prompt.

    On this router, ESC exits the OpenWrt U-Boot bootmenu.
    Do not send '0' automatically, because if ESC already exited,
    U-Boot will treat '0' as a command.
    """
    print()
    print("=" * 70)
    print("RAM rescue mode: stopping U-Boot autoboot/menu.")
    print("=" * 70)

    ser.baudrate = 115200

    old_timeout = ser.timeout
    ser.timeout = 0.05

    deadline = time.monotonic() + timeout
    start = time.monotonic()
    last_esc = 0.0
    announced_menu = False
    buf = bytearray()

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()

            # Keep pressing ESC during the early U-Boot/menu window.
            if now - start < 15.0 and now - last_esc > 0.35:
                ser.write(b"\x1b")
                ser.flush()
                last_esc = now

            data = ser.read(1)

            if not data:
                continue

            buf += data

            if len(buf) > 16384:
                del buf[:-8192]

            print(data.decode("utf-8", errors="replace"), end="", flush=True)

            clean = strip_ansi_for_match(bytes(buf)).lower()

            if not announced_menu and (
                b"press up/down" in clean
                or b"run default boot command" in clean
                or b"hit any key to stop autoboot" in clean
            ):
                announced_menu = True
                print()
                print("[controller] U-Boot menu/autoboot detected. Sending ESC until prompt appears.")
                print()

            if has_prompt(bytes(buf)):
                print()
                print("U-Boot prompt detected.")
                print()
                return

        raise MtkProtocolError(
            "Could not reach U-Boot prompt after stopping autoboot/menu."
        )

    finally:
        ser.timeout = old_timeout



def wait_for_prompt(ser, timeout: float = 15.0) -> bytes:
    old_timeout = ser.timeout
    ser.timeout = 0.05

    deadline = time.monotonic() + timeout
    buf = bytearray()

    try:
        while time.monotonic() < deadline:
            data = ser.read(1)

            if not data:
                continue

            buf += data

            if len(buf) > 65536:
                del buf[:-32768]

            print(data.decode("utf-8", errors="replace"), end="", flush=True)

            if has_prompt(bytes(buf)):
                return bytes(buf)

        raise MtkProtocolError("Timeout waiting for U-Boot prompt.")

    finally:
        ser.timeout = old_timeout


def uboot_run_command(ser, command: str, timeout: float = 15.0) -> bytes:
    print()
    print(f"[U-Boot cmd] {command}")
    ser.write(command.encode("ascii") + b"\r")
    ser.flush()

    return wait_for_prompt(ser, timeout=timeout)



SOH = 0x01
STX = 0x02
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18
CRCCHR = ord("C")


def crc16_xmodem(data: bytes) -> int:
    crc = 0

    for b in data:
        crc ^= b << 8

        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF

    return crc & 0xFFFF


def build_ymodem_packet(block_num: int, payload: bytes, packet_size: int) -> bytes:
    if packet_size not in (128, 1024):
        raise ValueError("YMODEM packet size must be 128 or 1024")

    start = SOH if packet_size == 128 else STX

    if len(payload) > packet_size:
        raise ValueError("Payload larger than packet size")

    pad_byte = 0x00 if block_num == 0 else 0x1A
    padded = payload + bytes([pad_byte]) * (packet_size - len(payload))

    crc = crc16_xmodem(padded)

    blk = block_num & 0xFF

    return (
        bytes([start, blk, 0xFF - blk])
        + padded
        + crc.to_bytes(2, "big")
    )


def ymodem_wait_for(ser, allowed: set[int], timeout: float, label: str) -> int:
    old_timeout = ser.timeout
    ser.timeout = 0.2

    deadline = time.monotonic() + timeout
    recent = bytearray()

    try:
        while time.monotonic() < deadline:
            b = ser.read(1)

            if not b:
                continue

            value = b[0]
            recent += b

            if len(recent) > 32:
                del recent[:-32]

            if value in allowed:
                return value

            if value == CAN:
                raise MtkProtocolError(f"YMODEM cancelled by receiver during {label}")

            # Print unexpected readable text from U-Boot, but keep binary quiet.
            if value in (10, 13) or 32 <= value <= 126:
                print(b.decode("utf-8", errors="replace"), end="", flush=True)

        raise MtkProtocolError(
            f"YMODEM timeout waiting for {label}. "
            f"Recent RX: {hexdump(bytes(recent))}"
        )

    finally:
        ser.timeout = old_timeout


def ymodem_send_packet_with_retry(
    ser,
    packet: bytes,
    label: str,
    max_retries: int = 10,
    allowed_extra=None,
):
    if allowed_extra is None:
        allowed_extra = set()

    allowed = {ACK, NAK, CAN} | allowed_extra

    for attempt in range(1, max_retries + 1):
        ser.write(packet)
        ser.flush()

        resp = ymodem_wait_for(
            ser,
            allowed=allowed,
            timeout=20.0,
            label=f"response for {label}",
        )

        if resp == ACK:
            return ACK

        # Some U-Boot YMODEM receivers send 'C' after the final data block,

        if resp == CRCCHR and CRCCHR in allowed_extra:
            return CRCCHR

        if resp == NAK:
            print(f"YMODEM {label}: NAK, retry {attempt}/{max_retries}")
            continue

    raise MtkProtocolError(f"YMODEM failed sending {label}")


def ymodem_send_file(ser, filename: str, data: bytes):
    """
    Minimal YMODEM sender compatible with U-Boot loady.

    Faster / cleaner version:
      - prints progress every 256 KiB instead of every 1 KiB
      - handles U-Boot sending 'C' near the final data block
      - still uses 1 KiB YMODEM blocks
    """
    print()
    print("Starting YMODEM transfer")
    print(f"File: {filename}")
    print(f"Size: {len(data)} byte(s)")
    print()

    print("Waiting for U-Boot YMODEM receiver request: C")
    ymodem_wait_for(
        ser,
        allowed={CRCCHR, CAN},
        timeout=30.0,
        label="initial C",
    )

    basename = Path(filename).name.encode("ascii", errors="ignore")
    size_ascii = str(len(data)).encode("ascii")

    header_payload = basename + b"\0" + size_ascii + b"\0"
    header_packet = build_ymodem_packet(0, header_payload, 128)

    print("Sending YMODEM header packet")
    ymodem_send_packet_with_retry(ser, header_packet, "header")

    print("Waiting for receiver C after header")
    ymodem_wait_for(
        ser,
        allowed={CRCCHR, CAN},
        timeout=30.0,
        label="C after header",
    )

    block_num = 1
    pos = 0
    total = len(data)
    last_progress = -1
    receiver_already_wants_final_header = False

    while pos < total:
        chunk = data[pos:pos + 1024]
        packet = build_ymodem_packet(block_num, chunk, 1024)

        percent = int((pos * 100) / total) if total else 100

        # Print only occasionally; printing every block makes transfer slower.
        if block_num == 1 or percent != last_progress and percent % 5 == 0:
            print(
                f"YMODEM progress: {percent:3d}% "
                f"offset={pos}/{total}, block={block_num}"
            )
            last_progress = percent

        is_final_data_block = pos + len(chunk) >= total

        resp = ymodem_send_packet_with_retry(
            ser,
            packet,
            label=f"block {block_num}",
            allowed_extra={CRCCHR} if is_final_data_block else None,
        )

        pos += len(chunk)
        block_num += 1

        if is_final_data_block and resp == CRCCHR:
            print("Receiver requested final YMODEM header after final data block.")
            receiver_already_wants_final_header = True
            break

    print("YMODEM data phase complete.")
    print(f"Transferred {pos} byte(s).")

    if not receiver_already_wants_final_header:
        print("Sending YMODEM EOT")

        eot_done = False

        for attempt in range(1, 6):
            ser.write(bytes([EOT]))
            ser.flush()

            resp = ymodem_wait_for(
                ser,
                allowed={ACK, NAK, CRCCHR, CAN},
                timeout=20.0,
                label="EOT response",
            )

            if resp == ACK:
                eot_done = True
                break

            if resp == CRCCHR:
                eot_done = True
                break

            if resp == NAK:
                print(f"EOT got NAK, retry {attempt}/5")
                continue

        if not eot_done:
            raise MtkProtocolError("YMODEM EOT was not accepted")

        print("Waiting for final receiver C")
        try:
            ymodem_wait_for(
                ser,
                allowed={CRCCHR, CAN},
                timeout=10.0,
                label="final C",
            )
        except MtkProtocolError:
            print("No final C seen. Sending final empty block anyway.")

    final_packet = build_ymodem_packet(0, b"", 128)

    print("Sending final empty YMODEM block")
    ymodem_send_packet_with_retry(
        ser,
        final_packet,
        "final empty header",
        allowed_extra={CRCCHR},
    )

    print()
    print("YMODEM transfer completed.")
    print()

def finish_loady_and_return_to_prompt(ser, transfer_baud: int):
    """
    After U-Boot loady finishes, EX5601-T0 U-Boot prints:

      ## Switch baudrate to 115200 bps and press ESC ...

    This message is still readable at the transfer baud.
    Then host must switch back to 115200 and send ESC.
    """
    print()
    print("Waiting for U-Boot loady completion / baud restore message")

    old_timeout = ser.timeout
    ser.timeout = 0.1

    buf = bytearray()
    deadline = time.monotonic() + 120.0

    try:
        while time.monotonic() < deadline:
            b = ser.read(1)

            if not b:
                continue

            buf += b

            if len(buf) > 8192:
                del buf[:-4096]

            print(b.decode("utf-8", errors="replace"), end="", flush=True)

            lower = bytes(buf).lower()

            if b"switch baudrate to 115200" in lower and b"press esc" in lower:
                print()
                print("U-Boot requests return to 115200 and ESC.")
                break
        else:
            raise MtkProtocolError(
                "Timeout waiting for U-Boot loady baud-restore message."
            )

    finally:
        ser.timeout = old_timeout

    print("Changing host COM baud back to 115200")
    ser.baudrate = 115200
    time.sleep(0.50)

    print("Sending ESC to confirm baud restore")
    ser.write(b"\x1b")
    ser.flush()
    time.sleep(0.50)

    print("Waiting for U-Boot prompt after loady")
    wait_for_prompt(ser, timeout=60.0)


def upload_rescue_fit_with_loady(ser, rescue_fit: bytes):
    """
    Use U-Boot loady to receive payload_RESCUE.itb into RAM.

    Important:
      On this EX5601-T0 U-Boot, after loady finishes, U-Boot appears
      to remain at the temporary loady baud. Do not switch back to
      115200 before detecting the U-Boot prompt.
    """
    print()
    print("=" * 70)
    print("Uploading rescue FIT to RAM using U-Boot loady/YMODEM")
    print(f"Address: 0x{RESCUE_FIT_LOAD_ADDR:08X}")
    print(f"File:    {RESCUE_FIT_FILENAME}")
    print(f"Size:    {len(rescue_fit)} byte(s)")
    print("=" * 70)
    print()

    ser.baudrate = 115200
    time.sleep(0.10)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    if UBOOT_LOADY_BAUD is None:
        cmd = f"loady 0x{RESCUE_FIT_LOAD_ADDR:08x}"
        print("[U-Boot cmd]", cmd)
        ser.write(cmd.encode("ascii") + b"\r")
        ser.flush()

    else:
        cmd = f"loady 0x{RESCUE_FIT_LOAD_ADDR:08x} {UBOOT_LOADY_BAUD}"
        print("[U-Boot cmd]", cmd)
        ser.write(cmd.encode("ascii") + b"\r")
        ser.flush()

        time.sleep(0.30)

        print(f"Changing host COM baud to {UBOOT_LOADY_BAUD}")
        ser.baudrate = UBOOT_LOADY_BAUD
        time.sleep(0.50)

        old_timeout = ser.timeout
        ser.timeout = 0.05
        drain_deadline = time.monotonic() + 2.0
        drained = bytearray()

        try:
            while time.monotonic() < drain_deadline:
                b = ser.read(1)

                if not b:
                    continue

                drained += b
                print(b.decode("utf-8", errors="replace"), end="", flush=True)

                if b"press ENTER" in drained or b"press enter" in drained.lower():
                    break
        finally:
            ser.timeout = old_timeout

        print()
        print("Sending ENTER to start U-Boot YMODEM receiver")
        ser.write(b"\r")
        ser.flush()
        time.sleep(0.30)

    ymodem_send_file(
        ser,
        filename=RESCUE_FIT_FILENAME,
        data=rescue_fit,
    )

    if UBOOT_LOADY_BAUD is not None:
        finish_loady_and_return_to_prompt(
            ser=ser,
            transfer_baud=UBOOT_LOADY_BAUD,
        )
    else:
        print("Waiting for U-Boot prompt after YMODEM upload")
        wait_for_prompt(ser, timeout=120.0)

    print()
    print("Checking uploaded FIT with iminfo")
    uboot_run_command(
        ser,
        f"iminfo 0x{RESCUE_FIT_LOAD_ADDR:08x}",
        timeout=90.0,
    )

def boot_rescue_fit_from_ram(ser):
    """
    Boot the RAM-loaded rescue FIT from U-Boot prompt.
    At this point U-Boot should already be back at 115200.
    """
    print()
    print("=" * 70)
    print("Booting rescue FIT from RAM")
    print(f"Command: bootm 0x{RESCUE_FIT_LOAD_ADDR:08x}")
    print("=" * 70)
    print()

    ser.baudrate = 115200
    time.sleep(0.20)

    ser.write(f"bootm 0x{RESCUE_FIT_LOAD_ADDR:08x}\r".encode("ascii"))
    ser.flush()


# Boot analyzer / monitor




class BootAnalyzer:
    """
    First-stage boot-chain analyzer.
    """

    def __init__(self):
        self.seen = set()
        self.line_count = 0

    def mark(self, key: str, message: str):
        if key not in self.seen:
            self.seen.add(key)
            print()
            print(f"[ANALYZER] {message}")
            print()

    def process_line(self, line: str):
        self.line_count += 1
        lower = line.lower()

        if "bl2:" in lower:
            self.mark("bl2", "BL2 stage detected.")

        if "received fip" in lower:
            self.mark("fip_received", "BL2 accepted FIP.")

        if "bl31" in lower:
            self.mark("bl31", "BL31 / ARM Trusted Firmware stage detected.")

        if "u-boot" in lower or "uboot" in lower:
            self.mark("uboot", "U-Boot stage detected.")

        if "fit image found" in lower:
            self.mark("fit", "FIT image detected.")

        if "starting kernel" in lower:
            self.mark("kernel", "Linux kernel start detected.")

        if "run /sbin/init" in lower or "init: console is alive" in lower:
            self.mark("init", "Linux init/userspace detected.")

        if "zhal>" in lower:
            self.mark("zhal", "ZHAL prompt detected.")

        if "openwrt" in lower:
            self.mark("openwrt", "OpenWrt/Linux boot detected.")

        if "busybox" in lower:
            self.mark("busybox", "Linux userspace shell detected.")

        if "kernel panic" in lower:
            self.mark("kernel_panic", "Kernel panic detected.")

        if "bad magic" in lower or "wrong image format" in lower:
            self.mark("bad_image", "Boot image format problem detected.")

        if ("ubi error" in lower) or ("ubi:" in lower and "error" in lower):
            self.mark("ubi_error", "UBI/NAND layout or rootfs problem detected.")

        if "mtd" in lower and "not found" in lower:
            self.mark("mtd_error", "MTD partition problem detected.")

        if "crc" in lower and "bad" in lower:
            self.mark("crc_bad", "Bad CRC detected.")


def listen_and_analyze_forever(ser):
    print()
    print("=" * 70)
    print("Entering continuous EX5601-T0 boot-chain monitor.")
    print("No user input is required. Press Ctrl+C to stop.")
    print("=" * 70)
    print()

    analyzer = BootAnalyzer()
    line_buf = bytearray()

    old_timeout = ser.timeout
    ser.timeout = 0.1

    try:
        while True:
            data = ser.read(1)

            if not data:
                continue

            print(data.decode("utf-8", errors="replace"), end="", flush=True)

            line_buf += data

            if data in (b"\n", b"\r"):
                try:
                    line = line_buf.decode("utf-8", errors="replace").strip()
                except Exception:
                    line = ""

                if line:
                    analyzer.process_line(line)

                line_buf.clear()

    finally:
        ser.timeout = old_timeout



def boot_bl2_and_fip(ser, bl2_payload: bytes, fip_payload: bytes):
    wait_for_brom_sync(
        ser=ser,
        wait=SYNC_WAIT,
        delay=SYNC_DELAY,
        show_noise=False,
    )

    ser.reset_input_buffer()

    print("BootROM session is active.")
    print()

    get_hw_code(ser)
    get_target_config(ser)

    set_brom_uart_baud(ser, BROM_LOAD_BAUDRATE)

    print(f"Verifying BootROM communication at {BROM_LOAD_BAUDRATE}")
    get_hw_code(ser)

    print(f"Uploading EX5601-T0 BL2 to 0x{BL2_LOAD_ADDR:08X}")
    print()

    send_da(
        ser=ser,
        address=BL2_LOAD_ADDR,
        payload=bl2_payload,
        sig_len=0,
    )

    set_brom_uart_baud(ser, INITIAL_BAUD)

    jump_da64(ser, BL2_LOAD_ADDR)

    ok, _ = wait_for_text_line(
        ser,
        b"Starting UART download handshake",
        timeout=30.0,
    )

    if not ok:
        raise MtkProtocolError("BL2 did not enter UART download mode.")

    bl2_handshake(ser)
    bl2_version(ser)

    bl2_set_baudrate(ser, BL2_LOAD_BAUDRATE)

    # BL2 requires a second handshake after its baud switch.
    bl2_handshake(ser)

    bl2_send_fip(ser, fip_payload)
    bl2_go(ser)

    ok, _ = wait_for_text_line(
        ser,
        b"Received FIP",
        timeout=30.0,
    )

    if not ok:
        raise MtkProtocolError("BL2 did not confirm FIP reception.")


def run_ex5601_loader(port: str, ram_rescue: bool):
    ser = None

    bl2_payload = load_file_from_script_dir(BL2_FILENAME)
    fip_payload = load_file_from_script_dir(FIP_FILENAME)

    rescue_payload = None

    if ram_rescue:
        rescue_payload = load_file_from_script_dir(RESCUE_FIT_FILENAME)

    print(f"Loaded BL2: {BL2_FILENAME} ({len(bl2_payload)} bytes)")
    print(f"Loaded FIP: {FIP_FILENAME} ({len(fip_payload)} bytes)")

    if ram_rescue:
        print(
            f"Loaded rescue FIT: {RESCUE_FIT_FILENAME} "
            f"({len(rescue_payload)} bytes)"
        )

    print()

    try:
        ser = open_serial(port)

        boot_bl2_and_fip(
            ser=ser,
            bl2_payload=bl2_payload,
            fip_payload=fip_payload,
        )

        if ram_rescue:
            stop_uboot_autoboot_to_prompt(ser)
            upload_rescue_fit_with_loady(ser, rescue_payload)
            boot_rescue_fit_from_ram(ser)
            listen_and_analyze_forever(ser)
        else:
            # Normal mode: allow U-Boot to continue its default NAND boot.
            ser.baudrate = 115200
            listen_and_analyze_forever(ser)

    finally:
        if ser is not None:
            ser.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="EX5601-T0 de-bricker first-stage loader"
    )

    parser.add_argument(
        "port",
        help="Serial port only, example: COM3 or /dev/ttyUSB0",
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--ram-rescue",
        dest="ram_rescue",
        action="store_true",
        default=True,
        help=(
            "Default mode. After BL2/FIP boot, stop U-Boot, upload "
            "payload_RESCUE.itb to RAM using loady/YMODEM, then bootm it."
        ),
    )

    mode.add_argument(
        "--nand-boot",
        dest="ram_rescue",
        action="store_false",
        help=(
            "Old test mode. Boot BL2/FIP, then let U-Boot continue its "
            "normal/default NAND boot and only monitor UART output."
        ),
    )

    args = parser.parse_args()

    try:
        run_ex5601_loader(
            port=args.port,
            ram_rescue=args.ram_rescue,
        )
        return 0

    except KeyboardInterrupt:
        print()
        print("Stopped by user.")
        return 130

    except FileNotFoundError as e:
        print(f"File error: {e}")
        return 1

    except serial.SerialException as e:
        print(f"Serial error: {e}")
        return 1

    except MtkProtocolError as e:
        print(f"MTK protocol error: {e}")
        return 1

    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())