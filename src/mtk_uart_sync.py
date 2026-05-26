#Written by Qureshi Majad as part of projcts, at lut.fi
import time

from mtk_uart_common import (
    SYNC,
    EXPECTED_SYNC_REPLY,
    hexdump,
    printable,
    looks_like_os,
    read_available,
)


def wait_for_brom_sync(
    ser,
    wait=0.05,
    delay=0.005,
    show_noise=False,
    max_window=4096,
):
    rx_window = bytearray()
    os_warning_printed = False
    attempt = 0
    noise_count = 0

    print(f"Flooding sync bytes: {hexdump(SYNC)}")
    print(f"Looking for BootROM reply: {hexdump(EXPECTED_SYNC_REPLY)}")
    print()
    print("MTK BootROM sync happens only during the very early boot stage.")
    print("Keep this script running, then power-cycle/reboot the router.")
    print("Press Ctrl+C to stop.")
    print()

    while True:
        attempt += 1

        ser.write(SYNC)
        ser.flush()

        data = read_available(ser, wait_time=wait)

        if data:
            rx_window += data

            if len(rx_window) > max_window:
                rx_window = rx_window[-max_window:]

            if EXPECTED_SYNC_REPLY in rx_window:
                print()
                print(f"BootROM sync detected after attempt #{attempt}")
                print(f"Expected reply found: {hexdump(EXPECTED_SYNC_REPLY)}")
                print()
                print("Recent RX hex:")
                print(hexdump(bytes(rx_window[-128:])))
                print()

                return {
                    "attempt": attempt,
                    "noise_count": noise_count,
                    "rx_window": bytes(rx_window),
                }

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

                if show_noise:
                    print("Detected text:")
                    print(printable(bytes(rx_window[-512:])))
                    print()

            noise_count += len(data)

            if show_noise:
                print()
                print(f"Non-BootROM data received, {len(data)} byte(s):")
                print(hexdump(data))
                print(printable(data))

        if attempt % 100 == 0:
            print(
                f"Still scanning... attempts={attempt}, "
                f"non-BootROM bytes ignored={noise_count}"
            )

        time.sleep(delay)