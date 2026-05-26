#!/usr/bin/env python3
#Written by Qureshi Majad as part of projcts, at lut.fi
# test only script

import argparse
import sys

import serial

from mtk_uart_common import MtkProtocolError
from mtk_uart_sync import wait_for_brom_sync
from mtk_uart_cmds import get_hw_code, get_target_config
from mtk_uart_da import send_da_no_jump


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

    # Keep adapter control lines quiet.
    # Some USB-UART adapters toggle these lines when opening the port.
    ser.dtr = False
    ser.rts = False

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    return ser


def load_payload_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def parse_int(value: str) -> int:
    """
    Allows values like:
      0x201000
      2101248
    """
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Organized MTK UART BootROM loader test"
    )

    parser.add_argument(
        "port",
        help="Serial port, example: COM3 or /dev/ttyUSB0",
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="UART baud rate",
    )

    parser.add_argument(
        "--wait",
        type=float,
        default=0.05,
        help="Seconds to wait after each sync burst",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.005,
        help="Delay between sync bursts",
    )

    parser.add_argument(
        "--read-timeout",
        type=float,
        default=0.01,
        help="Serial read timeout",
    )

    parser.add_argument(
        "--show-noise",
        action="store_true",
        help="Print non-BootROM UART data while scanning",
    )

    parser.add_argument(
        "--no-hwcode",
        action="store_true",
        help="Do not run GET_HW_CODE after sync",
    )

    parser.add_argument(
        "--no-target-config",
        action="store_true",
        help="Do not run GET_TARGET_CONFIG after sync",
    )

    parser.add_argument(
        "--test-send-da",
        action="store_true",
        help="Upload a tiny dummy DA payload to RAM but do not jump/execute",
    )

    parser.add_argument(
        "--da-address",
        default="0x201000",
        help="RAM address for SEND_DA test, default: 0x201000",
    )

    parser.add_argument(
        "--payload",
        default=None,
        help="Optional payload file to upload for SEND_DA test",
    )

    parser.add_argument(
        "--dummy-size",
        type=int,
        default=16,
        help="Dummy payload size when --payload is not used",
    )

    args = parser.parse_args()

    ser = None

    try:
        ser = open_serial(
            port=args.port,
            baud=args.baud,
            read_timeout=args.read_timeout,
        )

        wait_for_brom_sync(
            ser=ser,
            wait=args.wait,
            delay=args.delay,
            show_noise=args.show_noise,
        )

        # Important:
        # Keep the same open serial object.
        # Do not close/reopen COM port between sync and commands.
        ser.reset_input_buffer()

        print("BootROM session is active.")
        print("Continuing with the same open COM port.")
        print()

        if not args.no_hwcode:
            get_hw_code(ser)

        if not args.no_target_config:
            get_target_config(ser)

        if args.test_send_da:
            address = parse_int(args.da_address)

            if args.payload is not None:
                payload = load_payload_file(args.payload)
                print(f"Loaded payload file: {args.payload}")
                print(f"Payload size: {len(payload)} byte(s)")
            else:
                payload = b"\x00" * args.dummy_size
                print(f"Using dummy payload: {len(payload)} zero byte(s)")

            print()

            send_da_no_jump(
                ser=ser,
                address=address,
                payload=payload,
                sig_len=0,
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

    except FileNotFoundError as e:
        print(f"File not found: {e}")
        return 1

    except ValueError as e:
        print(f"Invalid value: {e}")
        return 1

    finally:
        if ser is not None:
            ser.close()


if __name__ == "__main__":
    raise SystemExit(main())