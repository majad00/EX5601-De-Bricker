#!/usr/bin/env python3
# by Qureshi Majad at lut.fi
"""
Written as part of De bricker project
EX5601-T0 interactive de-bricker controller.

This script combines other script to do 
  - BootROM UART loading
  - RAM de-bricker FIP boot
  - hardware / MTD probing
  - safe layout verification
  - BL2 repair
  - FIP repair
  - optional reset

It imports your already-working loader.py and repair_bootchain.py.
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import serial

from loader import (
    MtkProtocolError,
    uboot_run_command,
)

from repair_bootchain import (
    boot_to_debricker_prompt,
    probe_flash,
    read_file,
    repair_mtd_partition,
    restore_factory_partition,
)

REPAIR_BL2_FILE = "repair_bl2.bin"
REPAIR_FIP_FILE = "repair_fip.bin"
FACTORY_FILE = "factory.bin"

EXPECTED_UBOOTMOD_LAYOUT = {
    "bl2": (0x000000, 0x100000),
    "u-boot-env": (0x100000, 0x180000),
    "factory": (0x180000, 0x380000),
    "fip": (0x380000, 0x540000),
    "zloader": (0x540000, 0x580000),
    "ubi": (0x580000, 0x1E000000),
}


def app_root_dir() -> Path:
    """
    Root directory means:
      - folder containing the EXE when compiled
      - folder containing this script when running as Python
    """
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


def factory_file_path() -> Path:
    return app_root_dir() / FACTORY_FILE

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def pause():
    input("\nPress ENTER to continue...")


def yes_no(prompt: str) -> bool:
    answer = input(f"{prompt} Type YES to continue: ").strip()
    return answer == "YES"


def parse_mtd_list(output: bytes):
    """
    Parse U-Boot mtd list output.

    Example line:
      - 0x000000380000-0x000000540000 : "fip"

    Partition names are normalized lowercase.
    This intentionally handles Factory/factory case-insensitively.
    """
    text = output.decode("utf-8", errors="replace")
    parts = {}

    pattern = re.compile(
        r"0x([0-9a-fA-F]+)-0x([0-9a-fA-F]+)\s*:\s*\"([^\"]+)\""
    )

    for start_hex, end_hex, name in pattern.findall(text):
        label = name.strip().lower()

        # Skip the whole-chip parent device.
        if label == "spi-nand0":
            continue

        parts[label] = (int(start_hex, 16), int(end_hex, 16))

    return parts



def restore_factory_from_backup(ser, layout_ok: bool):
    if not layout_ok:
        print("Refusing factory restore because layout is not verified.")
        return

    path = factory_file_path()

    print()
    print("=" * 70)
    print("RESTORE FACTORY FROM BACKUP")
    print("=" * 70)
    print()
    print("This option restores the Factory/factory partition from:")
    print()
    print(f"  {path}")
    print()
    print("Place factory.bin in the root directory beside this script/EXE.")
    print()
    print("WARNING:")
    print("Factory/factory contains device-specific calibration and identity data.")
    print("Only use a valid factory backup for this router or a known-good")
    print("emergency image for this exact model.")
    print()

    if not path.exists():
        print("factory.bin was not found.")
        print()
        print("Expected location:")
        print(f"  {path}")
        return

    data = path.read_bytes()

    print(f"Found: {path}")
    print(f"Size:  {len(data)} byte(s)")
    print()

    if len(data) != 0x200000:
        print("Refusing restore.")
        print("factory.bin must be exactly 0x200000 bytes / 2097152 bytes / 2 MiB.")
        return

    print("This will ERASE and WRITE the Factory/factory partition.")
    print()
    confirm = input("Type YES to restore Factory/factory from factory.bin: ").strip()

    if confirm != "YES":
        print("Cancelled.")
        return

    restore_factory_partition(
        ser=ser,
        filename=FACTORY_FILE,
        data=data,
    )


def check_ubootmod_layout(parts: dict):
    problems = []

    for name, expected_range in EXPECTED_UBOOTMOD_LAYOUT.items():
        got = parts.get(name)

        if got is None:
            problems.append(f'Missing partition "{name}"')
            continue

        if got != expected_range:
            problems.append(
                f'Partition "{name}" mismatch: '
                f"got 0x{got[0]:x}-0x{got[1]:x}, "
                f"expected 0x{expected_range[0]:x}-0x{expected_range[1]:x}"
            )

    return problems


def print_layout(parts: dict):
    print()
    print("Detected MTD layout:")
    print("-" * 70)

    for name in ["bl2", "u-boot-env", "factory", "fip", "zloader", "ubi"]:
        if name not in parts:
            print(f"{name:12s}  MISSING")
            continue

        start, end = parts[name]
        size = end - start
        print(
            f"{name:12s}  "
            f"0x{start:08x}-0x{end:08x}  "
            f"size 0x{size:x}"
        )

    print("-" * 70)


def probe_and_validate_layout(ser):
    print()
    print("=" * 70)
    print("Probing flash layout")
    print("=" * 70)

    output = uboot_run_command(ser, "mtd list", timeout=30.0)
    parts = parse_mtd_list(output)

    print_layout(parts)

    problems = check_ubootmod_layout(parts)

    if problems:
        print()
        print("LAYOUT CHECK FAILED")
        print("=" * 70)

        for p in problems:
            print(" -", p)

        print()
        print("Writes are BLOCKED because the flash layout is not the")
        print("expected EX5601-T0 ubootmod layout.")
        return False, parts

    print()
    print("Layout check: OK")
    print("Writes are allowed.")
    return True, parts


def show_banner(layout_ok: bool):
    clear_screen()

    print("=" * 72)
    print("        Zyxel EX5601-T0 / T56 MT7986 RAM De-bricker Menu")
    print("=" * 72)

    if layout_ok:
        print("Status: layout verified, repair actions enabled")
    else:
        print("Status: layout not verified, write actions disabled")

    print("=" * 72)
    print()


def show_menu(layout_ok: bool):
    show_banner(layout_ok)

    print("  1. Probe hardware / print U-Boot and MTD info")
    print("  2. Re-check partition layout")
    print()

    if layout_ok:
        print("  3. Repair FIP partition")
        print("  4. Repair BL2 partition")
        print("  5. Repair both: FIP first, then BL2")
    else:
        print("  3. Repair FIP partition        [disabled]")
        print("  4. Repair BL2 partition        [disabled]")
        print("  5. Repair both                 [disabled]")

    if layout_ok:
        print("  6. Restore Factory from backup  [DANGEROUS]")
    else:
        print("  6. Restore Factory from backup  [disabled]")

    print("  7. Show current U-Boot environment")
    print("  8. Reset / reboot router")
    print("  9. Open manual U-Boot command prompt")
    print()
    print("  0. Exit")
    print()


def repair_fip(ser, layout_ok: bool):
    if not layout_ok:
        print("Refusing FIP repair because layout is not verified.")
        return

    path = Path(REPAIR_FIP_FILE)

    if not path.exists():
        print(f"Missing file: {REPAIR_FIP_FILE}")
        return

    print()
    print("Selected: Repair FIP")
    print(f"File: {REPAIR_FIP_FILE}")
    print()
    print("This will erase and rewrite only the fip partition.")
    print("It will NOT touch Factory/factory, zloader, or ubi.")

    if not yes_no("Continue with FIP repair?"):
        print("Cancelled.")
        return

    data = read_file(REPAIR_FIP_FILE)

    repair_mtd_partition(
        ser=ser,
        partition="fip",
        filename=REPAIR_FIP_FILE,
        data=data,
    )


def repair_bl2(ser, layout_ok: bool):
    if not layout_ok:
        print("Refusing BL2 repair because layout is not verified.")
        return

    path = Path(REPAIR_BL2_FILE)

    if not path.exists():
        print(f"Missing file: {REPAIR_BL2_FILE}")
        return

    print()
    print("Selected: Repair BL2")
    print(f"File: {REPAIR_BL2_FILE}")
    print()
    print("WARNING: BL2 is the earliest flash boot stage.")
    print("Only continue if this is the correct EX5601-T0/T56 preloader.")

    if not yes_no("Continue with BL2 repair?"):
        print("Cancelled.")
        return

    data = read_file(REPAIR_BL2_FILE)

    repair_mtd_partition(
        ser=ser,
        partition="bl2",
        filename=REPAIR_BL2_FILE,
        data=data,
    )


def show_env(ser):
    uboot_run_command(
        ser,
        "printenv",
        timeout=60.0,
    )


def reset_router(ser):
    print()
    print("Sending reset command to U-Boot.")
    uboot_run_command(ser, "reset", timeout=5.0)


def manual_prompt(ser):
    print()
    print("=" * 70)
    print("Manual U-Boot command mode")
    print("Type 'exit' to return to menu.")
    print("Dangerous commands are not blocked here.")
    print("=" * 70)

    while True:
        cmd = input("EX5601-DEBRICKER(manual)> ").strip()

        if not cmd:
            continue

        if cmd.lower() in {"exit", "quit", "menu"}:
            return

        uboot_run_command(
            ser,
            cmd,
            timeout=120.0,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Interactive EX5601-T0 RAM de-bricker menu"
    )

    parser.add_argument(
        "port",
        help="Serial port, example COM3 or /dev/ttyUSB0",
    )

    args = parser.parse_args()

    ser = None

    try:
        print()
        print("=" * 70)
        print("Booting RAM de-bricker FIP")
        print("=" * 70)

        ser = boot_to_debricker_prompt(args.port)

        print()
        print("=" * 70)
        print("Initial probe")
        print("=" * 70)

        probe_flash(ser)
        layout_ok, parts = probe_and_validate_layout(ser)

        while True:
            show_menu(layout_ok)
            choice = input("Select option: ").strip().lower()

            try:
                if choice == "1":
                    probe_flash(ser)
                    pause()

                elif choice == "2":
                    layout_ok, parts = probe_and_validate_layout(ser)
                    pause()

                elif choice == "3":
                    repair_fip(ser, layout_ok)
                    pause()

                elif choice == "4":
                    repair_bl2(ser, layout_ok)
                    pause()

                elif choice == "5":
                    if not layout_ok:
                        print("Refusing repair because layout is not verified.")
                        pause()
                        continue

                    print()
                    print("Selected: Repair both FIP and BL2.")
                    print("Order: FIP first, BL2 second.")

                    if not yes_no("Continue with both repairs?"):
                        print("Cancelled.")
                        pause()
                        continue

                    fip_data = read_file(REPAIR_FIP_FILE)
                    repair_mtd_partition(
                        ser=ser,
                        partition="fip",
                        filename=REPAIR_FIP_FILE,
                        data=fip_data,
                    )

                    bl2_data = read_file(REPAIR_BL2_FILE)
                    repair_mtd_partition(
                        ser=ser,
                        partition="bl2",
                        filename=REPAIR_BL2_FILE,
                        data=bl2_data,
                    )

                    pause()

                elif choice == "6":
                    restore_factory_from_backup(ser, layout_ok)
                    pause()

                elif choice == "7":
                    show_env(ser)
                    pause()

                elif choice == "8":
                    if yes_no("Reboot router?"):
                        reset_router(ser)
                        return 0
                    pause()

                elif choice == "9":
                    manual_prompt(ser)
                    pause()

                elif choice == "0":
                    print("Exiting without reboot.")
                    return 0

                else:
                    print("Unknown option.")
                    pause()

            except MtkProtocolError as e:
                print()
                print(f"MTK/U-Boot protocol error: {e}")
                pause()

            except FileNotFoundError as e:
                print()
                print(f"File error: {e}")
                pause()

    except KeyboardInterrupt:
        print()
        print("Stopped by user.")
        return 130

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