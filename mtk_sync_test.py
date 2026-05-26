#!/usr/bin/env python3
#Written by Qureshi Majad as part of projcts, at lut.fi
# test file testing mtk protocol


import argparse
import time
import serial

SYNC = bytes.fromhex("A0 0A 50 05")
EXPECTED = bytes.fromhex("5F F5 AF FA")
GET_HW_CODE = bytes.fromhex("FD")

OS_KEYWORDS = [
    b"BusyBox",
    b"OpenWrt",
    b"Linux",
    b"built-in shell",
    b"ash",
    b"login:",
    b"root@",
    b"/bin/ash",
    b"Press the",
    b"U-Boot",
]


def hexdump(data: bytes) -> str:
    return data.hex(" ").upper() if data else "<none>"


def printable(data: bytes) -> str:
    out = []
    for b in data:
        if 32 <= b <= 126:
            out.append(chr(b))
        elif b in (10, 13):
            out.append("\n")
        else:
            out.append(".")
    return "".join(out)


def looks_like_os(data: bytes) -> bool:
    lower_data = data.lower()

    for keyword in OS_KEYWORDS:
        if keyword.lower() in lower_data:
            return True

    return False


def read_available(ser, wait_time=0.10) -> bytes:
    deadline = time.monotonic() + wait_time
    data = bytearray()

    while time.monotonic() < deadline:
        waiting = ser.in_waiting

        if waiting:
            data += ser.read(waiting)
            deadline = time.monotonic() + 0.03
        else:
            b = ser.read(1)
            if b:
                data += b
                deadline = time.monotonic() + 0.03

    return bytes(data)


def send_get_hw_code(ser):
    print()
    print("Sending GET_HW_CODE command: FD")

    ser.write(GET_HW_CODE)
    ser.flush()

    rx = read_available(ser, wait_time=0.40)

    print(f"GET_HW_CODE RX length: {len(rx)} byte(s)")
    print(f"GET_HW_CODE RX hex:    {hexdump(rx)}")

    if len(rx) >= 5 and rx[0] == 0xFD:
        value = int.from_bytes(rx[1:5], "big")
        hwcode = (value >> 16) & 0xFFFF
        hwver = value & 0xFFFF

        print()
        print("Possible decoded result:")
        print(f"HW code: 0x{hwcode:04X}")
        print(f"HW ver:  0x{hwver:04X}")

    elif len(rx) >= 4:
        value = int.from_bytes(rx[:4], "big")
        hwcode = (value >> 16) & 0xFFFF
        hwver = value & 0xFFFF

        print()
        print("Possible decoded result without command echo:")
        print(f"HW code: 0x{hwcode:04X}")
        print(f"HW ver:  0x{hwver:04X}")

    else:
        print()
        print("Not enough bytes to decode HW code.")


def main():
    parser = argparse.ArgumentParser(
        description="MTK UART BootROM sync scanner"
    )
    parser.add_argument("port", help="Serial port, example: COM3 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--wait", type=float, default=0.05)
    parser.add_argument("--delay", type=float, default=0.005)
    parser.add_argument("--read-timeout", type=float, default=0.01)
    parser.add_argument("--show-noise", action="store_true", help="Print non-BootROM serial data")
    parser.add_argument("--no-hwcode", action="store_true", help="Do not send FD after sync")
    args = parser.parse_args()

    ser = None
    rx_window = bytearray()
    os_warning_printed = False

    print(f"Opening serial port: {args.port}")
    print(f"Baud: {args.baud}, mode: 8N1")
    print(f"Flooding sync bytes: {hexdump(SYNC)}")
    print(f"Looking for BootROM reply: {hexdump(EXPECTED)}")
    print()
    print("MTK BootROM sync happens only during the very early boot stage.")
    print("Keep this script running, then power-cycle/reboot the router.")
    print("Press Ctrl+C to stop.")
    print()

    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=args.read_timeout,
            write_timeout=1,
        )

        ser.dtr = False
        ser.rts = False
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        attempt = 0
        noise_count = 0

        while True:
            attempt += 1

            ser.write(SYNC)
            ser.flush()

            data = read_available(ser, wait_time=args.wait)

            if data:
                rx_window += data

                # Keep rolling.
                if len(rx_window) > 4096:
                    rx_window = rx_window[-4096:]

                #  MTK BootROM sync detected.
                if EXPECTED in rx_window:
                    print()
                    print(f"BootROM sync detected after attempt #{attempt}")
                    print(f"Expected reply found: {hexdump(EXPECTED)}")
                    print()
                    print("Recent RX hex:")
                    print(hexdump(bytes(rx_window[-128:])))

                    if not args.no_hwcode:
                        send_get_hw_code(ser)

                    return

                #  Linux/OpenWrt/U-Boot/regular console detected.
                if looks_like_os(rx_window) and not os_warning_printed:
                    os_warning_printed = True

                    print()
                    print("Regular OS / boot console detected on the UART.")
                    print("This is not MTK BootROM mode.")
                    print()
                    print("The router needs to reboot or power-cycle.")
                    print("MTK connection happens only during the very early start sequence.")
                    print("Keep this script running, then reboot/power-cycle the router now.")
                    print()

                    if args.show_noise:
                        print("Detected text:")
                        print(printable(bytes(rx_window[-512:])))
                        print()

                noise_count += len(data)

                if args.show_noise:
                    print()
                    print(f"Non-BootROM data received, {len(data)} byte(s):")
                    print(hexdump(data))
                    print(printable(data))

            if attempt % 100 == 0:
                print(
                    f"Still scanning... attempts={attempt}, "
                    f"non-BootROM bytes ignored={noise_count}"
                )

            time.sleep(args.delay)

    except KeyboardInterrupt:
        print("\nStopped by user.")

    except serial.SerialException as e:
        print(f"Serial error: {e}")

    finally:
        if ser is not None:
            ser.close()


if __name__ == "__main__":
    main()