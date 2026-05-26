#!/usr/bin/env python3
#Written by Qureshi Majad at lut.fi
"""
Written as part of De bricker projct, at lut.fi


EX5601-T0 boot-chain repair script.

This script uses the RAM-loaded de-bricker FIP/U-Boot.

Examples:

  Probe only:
    python repair_bootchain.py COM3 --probe

  Repair FIP partition:
    python repair_bootchain.py COM3 --write-fip repair_fip.bin --yes

  Repair BL2 partition:
    python repair_bootchain.py COM3 --write-bl2 repair_bl2.bin --yes

  Repair both, FIP first, BL2 second:
    python repair_bootchain.py COM3 --write-fip repair_fip.bin --write-bl2 repair_bl2.bin --yes

Required in same directory:
  loader.py
  payload_bl2.bin
  paylaod_FIP.bin

Repair files:
  repair_fip.bin
  repair_bl2.bin
"""

import argparse
import re
import sys
import time
import zlib
from pathlib import Path

import serial

from loader import (
    BL2_FILENAME,
    FIP_FILENAME,
    RESCUE_FIT_LOAD_ADDR,
    UBOOT_LOADY_BAUD,
    MtkProtocolError,
    boot_bl2_and_fip,
    finish_loady_and_return_to_prompt,
    load_file_from_script_dir,
    open_serial,
    stop_uboot_autoboot_to_prompt,
    uboot_run_command,
    ymodem_send_file,
)


LOAD_ADDR = RESCUE_FIT_LOAD_ADDR
VERIFY_ADDR = 0x47000000

FACTORY_PARTITION_SIZE = 0x200000
FACTORY_RESTORE_FILENAME = "factory.bin"

PARTITION_LIMITS = {
    "bl2": 0x100000,
    "fip": 0x1C0000,
}

PROTECTED_PARTITIONS = {
    "factory",
    "zloader",
    "ubi",
    "u-boot-env",
}


def read_file(path_text: str) -> bytes:
    from paths import find_payload_file

    path = Path(path_text)

    if not path.exists():
        path = find_payload_file(path_text)

    path = path.resolve()

    if not path.exists():
        raise FileNotFoundError(f"Repair image not found: {path}")

    print(f"Using repair image: {path}")

    with open(path, "rb") as f:
        return f.read()
        
        


def hexdump_preview(data: bytes, length: int = 16) -> str:
    return " ".join(f"{b:02X}" for b in data[:length])


def require_safe_partition(partition: str):
    p = partition.lower()

    if p in PROTECTED_PARTITIONS:
        raise MtkProtocolError(
            f"Refusing to write protected partition: {partition}"
        )

    if p not in PARTITION_LIMITS:
        raise MtkProtocolError(
            f"Unsupported repair partition: {partition}. "
            f"Allowed: {', '.join(PARTITION_LIMITS)}"
        )


def check_image_size(partition: str, data: bytes):
    limit = PARTITION_LIMITS[partition.lower()]

    if len(data) > limit:
        raise MtkProtocolError(
            f"{partition} image is too large: {len(data)} bytes, "
            f"partition limit is {limit} bytes"
        )


def local_crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def parse_uboot_crc32(output: bytes):
    """
    U-Boot crc32 output should look like:
      crc32 for 46000000 ... 46032acf ==> 5cb02fde

    Only accept the value after '==>'.
    Do not guess from random 8-digit hex numbers, because the end address
    is also printed as 8 hex digits.
    """
    text = output.decode("utf-8", errors="replace")

    m = re.search(r"==>\s*([0-9a-fA-F]{8})", text)

    if not m:
        return None

    return int(m.group(1), 16)


def output_has_marker(output: bytes, marker: str) -> bool:
    return marker.encode("ascii") in output


def run_checked_command(ser, command: str, ok_marker: str, timeout: float = 60.0):
    failed_marker = f"FAILED_{ok_marker}"

    wrapped = f"{command} && echo {ok_marker} || echo {failed_marker}"

    output = uboot_run_command(
        ser,
        wrapped,
        timeout=timeout,
    )

    text = output.decode("utf-8", errors="replace")

    # U-Boot echoes the full command line, so both OK and FAILED markers may
    # appear in the echoed command text. Only trust markers printed alone
    # on their own output line.
    clean_lines = []

    for raw_line in text.replace("\r", "\n").split("\n"):
        line = raw_line.strip()

        if not line:
            continue

        # Ignore echoed command lines.
        if command in line:
            continue

        clean_lines.append(line)

    if failed_marker in clean_lines:
        raise MtkProtocolError(
            f"U-Boot reported failure for command: {command}"
        )

    if ok_marker not in clean_lines:
        raise MtkProtocolError(
            f"Command failed or OK marker not found: {command}"
        )

    return output


def parse_mtd_labels(output: bytes):
    """
    Return mapping:
      lowercase_name -> exact_name_from_uboot

    Example:
      "factory" -> "Factory"

    This keeps partition-name matching case-insensitive.
    """
    text = output.decode("utf-8", errors="replace")
    labels = {}

    pattern = re.compile(
        r"0x([0-9a-fA-F]+)-0x([0-9a-fA-F]+)\s*:\s*\"([^\"]+)\""
    )

    for _start_hex, _end_hex, name in pattern.findall(text):
        exact = name.strip()
        lower = exact.lower()

        if lower == "spi-nand0":
            continue

        labels[lower] = exact

    return labels


def resolve_mtd_label_case_insensitive(ser, requested_name: str) -> str:
    output = uboot_run_command(
        ser,
        "mtd list",
        timeout=30.0,
    )

    labels = parse_mtd_labels(output)
    wanted = requested_name.lower()

    if wanted not in labels:
        raise MtkProtocolError(
            f'MTD partition "{requested_name}" not found in mtd list'
        )

    return labels[wanted]


def restore_factory_partition(ser, filename: str, data: bytes):
    """
    Dedicated last-resort Factory/factory restore.

    This intentionally does not use repair_mtd_partition(), because factory
    must stay protected from normal repair actions.
    """
    if len(data) != FACTORY_PARTITION_SIZE:
        raise MtkProtocolError(
            f"Refusing factory restore: {filename} is {len(data)} bytes, "
            f"but factory partition must be exactly "
            f"{FACTORY_PARTITION_SIZE} bytes / 0x{FACTORY_PARTITION_SIZE:X}."
        )

    actual_label = resolve_mtd_label_case_insensitive(ser, "factory")
    expected_crc = local_crc32(data)

    print()
    print("=" * 70)
    print("FACTORY PARTITION RESTORE")
    print("=" * 70)
    print(f"Image file:       {filename}")
    print(f"Image size:       {len(data)} byte(s)")
    print(f"Expected size:    {FACTORY_PARTITION_SIZE} byte(s)")
    print(f"Image CRC:        0x{expected_crc:08X}")
    print(f"MTD label:        {actual_label}")
    print(f"Header preview:   {hexdump_preview(data)}")
    print("=" * 70)
    print()

    upload_file_to_ram_with_loady(
        ser=ser,
        filename=filename,
        data=data,
    )

    verify_ram_crc(
        ser=ser,
        addr=LOAD_ADDR,
        size=FACTORY_PARTITION_SIZE,
        expected_crc=expected_crc,
        label="uploaded factory.bin in RAM",
    )

    print()
    print("=" * 70)
    print("Writing Factory/factory partition")
    print("=" * 70)

    write_cmd = (
        f"mtd erase {actual_label} && "
        f"mtd write {actual_label} 0x{LOAD_ADDR:08x}"
    )

    run_checked_command(
        ser,
        write_cmd,
        ok_marker="WRITE_FACTORY_OK",
        timeout=180.0,
    )

    print()
    print("=" * 70)
    print("Read-back verification for Factory/factory")
    print("=" * 70)

    readback_cmd = (
        f"mtd read {actual_label} 0x{VERIFY_ADDR:08x} "
        f"0x0 0x{FACTORY_PARTITION_SIZE:x}"
    )

    run_checked_command(
        ser,
        readback_cmd,
        ok_marker="READBACK_FACTORY_OK",
        timeout=180.0,
    )

    verify_ram_crc(
        ser=ser,
        addr=VERIFY_ADDR,
        size=FACTORY_PARTITION_SIZE,
        expected_crc=expected_crc,
        label="read-back factory partition",
    )

    print()
    print("=" * 70)
    print("Factory restore completed and verified.")
    print("=" * 70)


def wait_for_loady_enter_request(ser, timeout: float = 10.0):
    """
    U-Boot loady with a baud argument prints:

      ## Switch baudrate to XXXXX bps and press ENTER

    This waits for that text after the host has switched to the same baud.
    """
    old_timeout = ser.timeout
    ser.timeout = 0.05

    deadline = time.monotonic() + timeout
    buf = bytearray()

    try:
        while time.monotonic() < deadline:
            b = ser.read(1)

            if not b:
                continue

            buf += b

            if len(buf) > 4096:
                del buf[:-2048]

            print(b.decode("utf-8", errors="replace"), end="", flush=True)

            lower = bytes(buf).lower()

            if b"press enter" in lower:
                return

    finally:
        ser.timeout = old_timeout

    raise MtkProtocolError(
        "Timeout waiting for U-Boot loady ENTER request."
    )


def upload_file_to_ram_with_loady(ser, filename: str, data: bytes):
    print()
    print("=" * 70)
    print("Uploading repair image to RAM with U-Boot loady/YMODEM")
    print(f"Address: 0x{LOAD_ADDR:08X}")
    print(f"File:    {filename}")
    print(f"Size:    {len(data)} byte(s)")
    print(f"CRC32:   0x{local_crc32(data):08X}")
    print("=" * 70)
    print()

    ser.baudrate = 115200
    time.sleep(0.20)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    if UBOOT_LOADY_BAUD is None:
        cmd = f"loady 0x{LOAD_ADDR:08x}"
        print("[U-Boot cmd]", cmd)
        ser.write(cmd.encode("ascii") + b"\r")
        ser.flush()

    else:
        cmd = f"loady 0x{LOAD_ADDR:08x} {UBOOT_LOADY_BAUD}"
        print("[U-Boot cmd]", cmd)
        ser.write(cmd.encode("ascii") + b"\r")
        ser.flush()

        time.sleep(0.30)

        print(f"Changing host COM baud to {UBOOT_LOADY_BAUD}")
        ser.baudrate = UBOOT_LOADY_BAUD
        time.sleep(0.50)

        wait_for_loady_enter_request(ser)

        print()
        print("Sending ENTER to start YMODEM receiver")
        ser.write(b"\r")
        ser.flush()
        time.sleep(0.30)

    ymodem_send_file(
        ser,
        filename=filename,
        data=data,
    )

    if UBOOT_LOADY_BAUD is not None:
        finish_loady_and_return_to_prompt(
            ser=ser,
            transfer_baud=UBOOT_LOADY_BAUD,
        )
    else:
        print("Waiting for U-Boot prompt after YMODEM upload")
        from loader import wait_for_prompt
        wait_for_prompt(ser, timeout=120.0)

    ser.baudrate = 115200
    time.sleep(0.20)

    uboot_run_command(
        ser,
        f"setenv loadaddr 0x{LOAD_ADDR:08x}",
        timeout=10.0,
    )

    # loady should set filesize automatically, but set it explicitly too.
    uboot_run_command(
        ser,
        f"setenv filesize 0x{len(data):x}",
        timeout=10.0,
    )


def verify_ram_crc(ser, addr: int, size: int, expected_crc: int, label: str):
    print()
    print(f"Checking CRC32 for {label}")
    print(f"Address: 0x{addr:08X}")
    print(f"Size:    0x{size:X}")
    print(f"Expect:  0x{expected_crc:08X}")

    output = uboot_run_command(
        ser,
        f"crc32 0x{addr:08x} 0x{size:x}",
        timeout=30.0,
    )

    got = parse_uboot_crc32(output)

    if got is None:
        print("Warning: could not parse U-Boot crc32 output.")
        print("Continuing, but CRC was not script-verified.")
        return

    print(f"Got:     0x{got:08X}")

    if got != expected_crc:
        raise MtkProtocolError(
            f"CRC mismatch for {label}: "
            f"expected 0x{expected_crc:08X}, got 0x{got:08X}"
        )

    print("CRC OK.")


def probe_flash(ser):
    print()
    print("=" * 70)
    print("Probing de-bricker U-Boot / flash layout")
    print("=" * 70)

    uboot_run_command(ser, "version", timeout=30.0)
    uboot_run_command(ser, "printenv loadaddr filesize bootdelay bootcmd", timeout=30.0)
    uboot_run_command(ser, "printenv debrick_info debrick_write_fip debrick_write_bl2", timeout=30.0)
    uboot_run_command(ser, "mtd list", timeout=30.0)

    # These may fail on a badly broken UBI, but command output is still useful.
    uboot_run_command(ser, "ubi part ubi", timeout=60.0)
    uboot_run_command(ser, "ubi info", timeout=30.0)


def repair_mtd_partition(ser, partition: str, filename: str, data: bytes):
    partition = partition.lower()

    require_safe_partition(partition)
    check_image_size(partition, data)

    partition_size = PARTITION_LIMITS[partition.lower()]

    expected_crc = local_crc32(data)
    size_hex = f"0x{len(data):x}"

    padded_data = data + (b"\xFF" * (partition_size - len(data)))
    padded_crc = local_crc32(padded_data)
    padded_size_hex = f"0x{partition_size:x}"

    print()
    print("=" * 70)
    print(f"Preparing to repair MTD partition: {partition}")
    print(f"Image file: {filename}")
    print(f"Image size:      {len(data)} byte(s)")
    print(f"Partition size:  {partition_size} byte(s)")
    print(f"Image CRC:       0x{expected_crc:08X}")
    print(f"Padded CRC:      0x{padded_crc:08X}")
    print(f"Header:     {hexdump_preview(data)}")
    print("=" * 70)
    print()

    upload_file_to_ram_with_loady(
    ser=ser,
    filename=filename,
    data=padded_data,
)

    verify_ram_crc(
    ser=ser,
    addr=LOAD_ADDR,
    size=partition_size,
    expected_crc=padded_crc,
    label=f"uploaded padded {partition} image in RAM",
)

    print()
    print("=" * 70)
    print(f"Writing {partition} partition")
    print("=" * 70)

    # This syntax matches the existing OpenWrt EX5601 U-Boot env style:
    #   mtd erase fip && mtd write fip $loadaddr
    #
    # loady sets $filesize, and we also set it explicitly above.
    write_cmd = (
        f"mtd erase {partition} && "
        f"mtd write {partition} 0x{LOAD_ADDR:08x}"
    )

    run_checked_command(
        ser,
        write_cmd,
        ok_marker=f"WRITE_{partition.upper()}_OK",
        timeout=120.0,
    )

    print()
    print("=" * 70)
    print(f"Read-back verification for {partition}")
    print("=" * 70)

    readback_cmd = (
       f"mtd read {partition} 0x{VERIFY_ADDR:08x} 0x0 {padded_size_hex}"
    )

    run_checked_command(
        ser,
        readback_cmd,
        ok_marker=f"READBACK_{partition.upper()}_OK",
        timeout=120.0,
    )

    verify_ram_crc(
    ser=ser,
    addr=VERIFY_ADDR,
    size=partition_size,
    expected_crc=padded_crc,
    label=f"read-back full padded {partition} partition",
)
    
    print()
    print("=" * 70)
    print(f"{partition} repair completed and verified.")
    print("=" * 70)


def boot_to_debricker_prompt(port: str):
    bl2_payload = load_file_from_script_dir(BL2_FILENAME)
    fip_payload = load_file_from_script_dir(FIP_FILENAME)

    print(f"Loaded BL2: {BL2_FILENAME} ({len(bl2_payload)} bytes)")
    print(f"Loaded FIP: {FIP_FILENAME} ({len(fip_payload)} bytes)")
    print()

    ser = open_serial(port)

    boot_bl2_and_fip(
        ser=ser,
        bl2_payload=bl2_payload,
        fip_payload=fip_payload,
    )

    stop_uboot_autoboot_to_prompt(ser)

    return ser


def main() -> int:
    parser = argparse.ArgumentParser(
        description="EX5601-T0 boot-chain repair using de-bricker FIP"
    )

    parser.add_argument(
        "port",
        help="Serial port, example: COM3 or /dev/ttyUSB0",
    )

    parser.add_argument(
        "--probe",
        action="store_true",
        help="Only boot to de-bricker U-Boot and print flash information.",
    )

    parser.add_argument(
        "--write-fip",
        metavar="FILE",
        help="Write FILE to the fip MTD partition.",
    )

    parser.add_argument(
        "--write-bl2",
        metavar="FILE",
        help="Write FILE to the bl2 MTD partition.",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required for write operations.",
    )

    args = parser.parse_args()

    wants_write = bool(args.write_fip or args.write_bl2)

    if wants_write and not args.yes:
        print("Refusing to write flash without --yes.")
        print()
        print("Example:")
        print("  python repair_bootchain.py COM3 --write-fip repair_fip.bin --yes")
        return 2

    if not args.probe and not wants_write:
        print("Nothing to do. Use --probe, --write-fip, or --write-bl2.")
        return 2

    ser = None

    try:
        ser = boot_to_debricker_prompt(args.port)

        probe_flash(ser)

        # Recommended order: FIP first, BL2 second.
        if args.write_fip:
            fip_data = read_file(args.write_fip)
            repair_mtd_partition(
                ser=ser,
                partition="fip",
                filename=Path(args.write_fip).name,
                data=fip_data,
            )

        if args.write_bl2:
            bl2_data = read_file(args.write_bl2)
            repair_mtd_partition(
                ser=ser,
                partition="bl2",
                filename=Path(args.write_bl2).name,
                data=bl2_data,
            )

        print()
        print("=" * 70)
        print("Repair script completed.")
        print("You can now power-cycle the router or run: reset")
        print("=" * 70)

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
        print(f"MTK/U-Boot protocol error: {e}")
        return 1

    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1

    finally:
        if ser is not None:
            ser.close()


if __name__ == "__main__":
    raise SystemExit(main())