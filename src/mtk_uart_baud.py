#Written as part of De bricker projct, at lut.fi
# by Qureshi Majad at lut.fi
import time

from mtk_uart_common import (
    MtkProtocolError,
    read_u16be,
    send_and_expect_echo,
)

CMD_UART1_SET_BAUDRATE = bytes.fromhex("DC")


def set_brom_uart_baud(ser, baud: int):
    """
    BootROM UART baud switch.

    Correct protocol:
      send DC
      read echo DC
      send baud as 4-byte big-endian
      read echo of baud
      read 2-byte status
      change host COM baud
    """
    print()
    print(f"Setting BootROM UART baud to {baud}")
    print("Command: DC")

    send_and_expect_echo(ser, CMD_UART1_SET_BAUDRATE, timeout=1.0)

    baud_bytes = baud.to_bytes(4, "big")
    send_and_expect_echo(ser, baud_bytes, timeout=1.0)

    status = read_u16be(ser, timeout=2.0)

    print(f"UART1_SET_BAUDRATE status: 0x{status:04X}")

    if status == 0x1D1D:
        raise MtkProtocolError(
            f"BootROM rejected baud {baud}: baudrate too high"
        )

    if status != 0x0000:
        raise MtkProtocolError(
            f"UART1_SET_BAUDRATE failed, status=0x{status:04X}"
        )

    time.sleep(0.05)

    print(f"Changing host COM baud to {baud}")
    ser.baudrate = baud

    time.sleep(0.10)

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print("Baud switch completed.")
    print()


def baud_roundtrip_test(ser, baud: int, get_hw_code_func):
    set_brom_uart_baud(ser, baud)

    print(f"Testing GET_HW_CODE at {baud} baud")
    result = get_hw_code_func(ser)

    print(f"Baud roundtrip test at {baud} succeeded.")
    print()

    return result