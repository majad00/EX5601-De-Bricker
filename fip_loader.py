#!/usr/bin/env python3
#Written as part of De bricker projct, at lut.fi
# by Qureshi Majad at lut.fi


import argparse
import os
import time

import serial

from mtk_uart_common import (
    MtkProtocolError,
    hexdump,
    read_exact,
    send_and_expect_echo,
)
from mtk_uart_sync import wait_for_brom_sync
from mtk_uart_cmds import get_hw_code, get_target_config
from mtk_uart_da import send_da
from mtk_uart_baud import set_brom_uart_baud


CMD_JUMP_DA64 = bytes.fromhex("DE")


def open_serial(port: str, baud: int, read_timeout: float):
    print(f"Opening serial port: {port}")
    print(f"Baud: {baud}, mode: 8N1")
    print()

    ser = serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=read_timeout,
        write_timeout=1,
    )

    ser.dtr = False
    ser.rts = False
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    return ser


def read_u16be(ser, timeout=2.0) -> int:
    data = read_exact(ser, 2, timeout=timeout)
    if len(data) != 2:
        raise MtkProtocolError(f"Expected 2 bytes, got {len(data)}: {hexdump(data)}")
    return int.from_bytes(data, "big")


def read_u32be(ser, timeout=2.0) -> int:
    data = read_exact(ser, 4, timeout=timeout)
    if len(data) != 4:
        raise MtkProtocolError(f"Expected 4 bytes, got {len(data)}: {hexdump(data)}")
    return int.from_bytes(data, "big")


def jump_da64(ser, address: int):
    """
    mtk_uartboot AArch64 jump flow:
      echo DE
      echo address
      echo 01        # 1 = 64-bit
      read status
      echo 64        # decimal 100 magic
      read status
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

    # Magic value checked before resetting CPU to AArch64.
    send_and_expect_echo(ser, bytes([100]), timeout=1.0)

    status = read_u16be(ser, timeout=2.0)
    print(f"JUMP_DA64 magic status: 0x{status:04X}")

    if status != 0:
        raise MtkProtocolError(f"JUMP_DA64 magic failed, status=0x{status:04X}")

    print("JUMP_DA64 accepted. BL2 should start now.")
    print()


def wait_for_text(ser, pattern: bytes, timeout: float = 15.0):
    """
    Wait for a full UART line containing pattern.

    Important:
    This consumes the entire line before returning, like mtk_uartboot's read_line().
    That prevents leftover bytes such as " ...\\r\\n" from corrupting the BL2 handshake.
    """
    print(f"Waiting for BL2 text: {pattern.decode(errors='replace')}")
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
            print("Matched BL2 message.")
            print()
            return True, bytes(collected)

        print("Timeout waiting for BL2 message.")
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
    Drain leftover BL2 banner text before starting the binary mudl/TF-A handshake.

    We need this because BL2 prints:
      NOTICE: Starting UART download handshake ...
      ==================================

    If we start sending 'm' while that text is still arriving, the handshake desyncs.
    """
    print("Draining trailing BL2 UART text before handshake...")

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

        print("UART is quiet. Starting BL2 handshake.")
        print()

    finally:
        ser.timeout = old_timeout        


def bl2_handshake(ser):
    """
    BL2 UART download handshake:
      host sends: mudl
      BL2 replies: TF-A

    Correct sequence:
      m -> T
      u -> F
      d -> -
      l -> A
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
                # Ignore leftover/non-protocol bytes and retry same byte.
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
        print(f"Incorrect checksum: got 0x{real_checksum:04X}, expected 0x{checksum:04X}")
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

    # Final packet. Retry until accepted.
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


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MTK UART BootROM BL2+FIP loader"
    )

    parser.add_argument("port", help="Serial port, example: COM3")
    parser.add_argument("-p", "--payload", default="payload.bin", help="BL2 payload file")
    parser.add_argument("-f", "--fip", required=True, help="FIP file, example: bl31-uboot.fip")
    parser.add_argument("-l", "--load-addr", default="0x201000", help="BL2 load address")
    parser.add_argument("--initial-baud", type=int, default=115200)
    parser.add_argument("--brom-load-baudrate", type=int, default=460800)
    parser.add_argument("--bl2-load-baudrate", type=int, default=921600)
    parser.add_argument("--read-timeout", type=float, default=0.01)
    parser.add_argument("--wait", type=float, default=0.05)
    parser.add_argument("--delay", type=float, default=0.005)
    parser.add_argument("--show-noise", action="store_true")
    parser.add_argument("--bl2-wait-timeout", type=float, default=20.0)
    parser.add_argument("--post-go-listen", type=float, default=20.0)

    args = parser.parse_args()

    if not os.path.exists(args.payload):
        print(f"Error: BL2 payload not found: {args.payload}")
        return 1

    if not os.path.exists(args.fip):
        print(f"Error: FIP file not found: {args.fip}")
        return 1

    address = parse_int(args.load_addr)
    ser = None

    try:
        ser = open_serial(args.port, args.initial_baud, args.read_timeout)

        wait_for_brom_sync(
            ser=ser,
            wait=args.wait,
            delay=args.delay,
            show_noise=args.show_noise,
        )

        ser.reset_input_buffer()

        print("BootROM session is active.")
        print()

        get_hw_code(ser)
        get_target_config(ser)

 
        set_brom_uart_baud(ser, args.brom_load_baudrate)

        print(f"Verifying BootROM communication at {args.brom_load_baudrate}")
        get_hw_code(ser)

        with open(args.payload, "rb") as f:
            bl2_payload = f.read()

        print(f"Loaded BL2 payload: {args.payload}")
        print(f"BL2 payload size: {len(bl2_payload)} byte(s)")
        print()

        send_da(
            ser=ser,
            address=address,
            payload=bl2_payload,
            sig_len=0,
        )


        set_brom_uart_baud(ser, 115200)

        jump_da64(ser, address)

        ok, _ = wait_for_text(
            ser,
            b"Starting UART download handshake",
            timeout=args.bl2_wait_timeout,
        )

        if not ok:
            print("BL2 did not announce UART download mode.")
            print("Possible causes:")
            print("  - payload is not BL2 with UART download support")
            print("  - wrong load address")
            print("  - BL2 needs a different UART")
            print("  - payload is not the matching one for this router")
            return 1

        bl2_handshake(ser)
        bl2_version(ser)

  
        bl2_set_baudrate(ser, args.bl2_load_baudrate)

        # mtk  handshakes again after BL2 baud switch.
        bl2_handshake(ser)

        with open(args.fip, "rb") as f:
            fip = f.read()

        bl2_send_fip(ser, fip)
        bl2_go(ser)

        wait_for_text(
            ser,
            b"Received FIP",
            timeout=args.post_go_listen,
        )

        print("Done.")
        return 0

    except KeyboardInterrupt:
        print()
        print("Stopped by user.")
        return 130

    except serial.SerialException as e:
        print(f"Serial error: {e}")
        return 1

    except MtkProtocolError as e:
        print(f"MTK protocol error: {e}")
        return 1

    finally:
        if ser is not None:
            ser.close()


if __name__ == "__main__":
    raise SystemExit(main())