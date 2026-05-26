#!/usr/bin/env python3
#Written by Qureshi Majad as part of projcts, at lut.fi
# Test run
import argparse
import os
import time

import serial

from mtk_uart_common import MtkProtocolError
from mtk_uart_sync import wait_for_brom_sync
from mtk_uart_cmds import get_hw_code, get_target_config
from mtk_uart_da import send_da, send_da_no_jump, jump_da, listen_after_jump
from mtk_uart_baud import set_brom_uart_baud, baud_roundtrip_test


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


def load_payload_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def parse_int(value: str) -> int:
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
        help="Initial UART baud rate",
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
        "--test-baud",
        type=int,
        default=None,
        help="Only test BootROM baud switch, example: --test-baud 460800",
    )

    parser.add_argument(
        "--test-send-da",
        action="store_true",
        help="Upload payload to RAM",
    )

    parser.add_argument(
        "--jump-da",
        action="store_true",
        help="Jump to uploaded payload after SEND_DA",
    )

    parser.add_argument(
        "--pre-upload-baud",
        type=int,
        default=None,
        help="Set BootROM + host baud before SEND_DA upload",
    )

    parser.add_argument(
        "--pre-jump-baud",
        type=int,
        default=None,
        help="Set BootROM + host baud after upload but before JUMP_DA",
    )

    parser.add_argument(
        "--after-jump-baud",
        type=int,
        default=None,
        help="Change only host baud after JUMP_DA before listening",
    )

    parser.add_argument(
        "--da-address",
        default="0x201000",
        help="RAM address for SEND_DA/JUMP_DA, default: 0x201000",
    )

    parser.add_argument(
        "--payload",
        default=None,
        help="Payload file to upload. Example: payload.bin",
    )

    parser.add_argument(
        "--dummy-size",
        type=int,
        default=16,
        help="Dummy payload size when --payload is not used",
    )

    parser.add_argument(
        "--listen-after-jump",
        type=float,
        default=5.0,
        help="Seconds to listen for UART output after JUMP_DA",
    )

    args = parser.parse_args()

    ser = None

    try:
        if args.jump_da and not args.test_send_da:
            print("Error: --jump-da requires --test-send-da.")
            return 1

        if args.jump_da and args.payload is None:
            print("Error: --jump-da requires a real --payload file.")
            print("Never jump to the dummy zero payload.")
            return 1

        if args.payload is not None and not os.path.exists(args.payload):
            print(f"Error: payload file not found: {args.payload}")
            return 1

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

        ser.reset_input_buffer()

        print("BootROM session is active.")
        print("Continuing with the same open COM port.")
        print()

        if not args.no_hwcode:
            get_hw_code(ser)

        if not args.no_target_config:
            get_target_config(ser)

        # Pure baud-switch test. No payload upload.
        if args.test_baud is not None:
            baud_roundtrip_test(
                ser=ser,
                baud=args.test_baud,
                get_hw_code_func=get_hw_code,
            )

            print("Baud switch test completed.")
            print("No payload was uploaded.")
            return 0

        # Optional baud switch before DA upload.
        if args.pre_upload_baud is not None:
            set_brom_uart_baud(ser, args.pre_upload_baud)

            print(f"Verifying BootROM communication at {args.pre_upload_baud}")
            get_hw_code(ser)

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

            if args.jump_da:
                send_da(
                    ser=ser,
                    address=address,
                    payload=payload,
                    sig_len=0,
                )

                # Optional baud switch after upload but before jump.
                # This is the important 
                if args.pre_jump_baud is not None:
                    set_brom_uart_baud(ser, args.pre_jump_baud)

                    print(f"Verifying BootROM communication at {args.pre_jump_baud}")
                    get_hw_code(ser)

                jump_da(
                    ser=ser,
                    address=address,
                )

                # This changes only the PC side after payload starts.
                if args.after_jump_baud is not None:
                    print()
                    print(f"Changing host baud after jump to {args.after_jump_baud}")
                    ser.baudrate = args.after_jump_baud
                    time.sleep(0.10)
                    ser.reset_input_buffer()

                listen_after_jump(
                    ser=ser,
                    seconds=args.listen_after_jump,
                )
            else:
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