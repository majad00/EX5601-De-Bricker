#Written by Qureshi Majad as part of projcts, at lut.fi
# not used
import time

from mtk_uart_common import (
    CMD_SEND_DA,
    MtkProtocolError,
    hexdump,
    read_u16be,
    send_and_expect_echo,
    send_u32_echo,
)


def prepare_data(payload: bytes, sig_len: int = 0, maxsize: int | None = None):
    """
    Match the useful part of mtkclient Preloader.prepare_data().

    For our first test, sig_len is normally 0.
    We still support sig_len for later real DA/preloader files.
    """
    if sig_len < 0:
        raise ValueError("sig_len cannot be negative")

    if sig_len > len(payload):
        raise ValueError("sig_len is bigger than payload size")

    if sig_len == 0:
        body = payload
        sig = b""
    else:
        body = payload[:-sig_len]
        sig = payload[-sig_len:]

    if maxsize is not None:
        body = body[:maxsize]

    data = body + sig

    # mtkclient pads odd length before checksum/upload.
    if len(data) % 2 != 0:
        data += b"\x00"

    checksum = 0

    for pos in range(0, len(data), 2):
        checksum ^= int.from_bytes(data[pos:pos + 2], "little")

    checksum &= 0xFFFF

    return checksum, data


def upload_data(ser, data: bytes, expected_checksum: int, chunk_size: int = 0x400):
    """
    Send DA payload body, then read returned checksum + status.

    mtkclient sends data in chunks, sends a zero-length packet/flush marker,
    waits briefly, then reads two 16-bit words: checksum and status.
    """
    print(f"Uploading {len(data)} byte(s)...")

    pos = 0
    remaining = len(data)

    while remaining > 0:
        size = min(remaining, chunk_size)
        ser.write(data[pos:pos + size])
        ser.flush()

        pos += size
        remaining -= size

    # In mtkclient this is usbwrite(b"") plus a short delay.
    # On UART there is no USB zero-length packet, so just flush and wait.
    ser.flush()
    time.sleep(0.12)

    returned_checksum = read_u16be(ser, timeout=2.0)
    status = read_u16be(ser, timeout=2.0)

    print(f"Returned checksum: 0x{returned_checksum:04X}")
    print(f"Expected checksum: 0x{expected_checksum:04X}")
    print(f"Upload status:      0x{status:04X}")

    if returned_checksum not in (expected_checksum, 0x0000):
        raise MtkProtocolError(
            f"Checksum mismatch: expected 0x{expected_checksum:04X}, "
            f"got 0x{returned_checksum:04X}"
        )

    if status > 0x00FF:
        raise MtkProtocolError(f"SEND_DA upload failed, status=0x{status:04X}")

    return {
        "returned_checksum": returned_checksum,
        "expected_checksum": expected_checksum,
        "status": status,
    }


def send_da_no_jump(
    ser,
    address: int,
    payload: bytes,
    sig_len: int = 0,
    maxsize: int | None = None,
):
    """
    Safe first test:
    - sends SEND_DA header
    - uploads payload to RAM
    - does NOT jump/execute it
    """
    checksum, data = prepare_data(payload, sig_len=sig_len, maxsize=maxsize)

    print("SEND_DA test only. This uploads to RAM but does NOT execute.")
    print(f"Command:       D7")
    print(f"Address:       0x{address:08X}")
    print(f"Payload input: {len(payload)} byte(s)")
    print(f"Upload size:   {len(data)} byte(s)")
    print(f"Sig length:    {sig_len}")
    print(f"Checksum:      0x{checksum:04X}")
    print()

    send_and_expect_echo(ser, CMD_SEND_DA, timeout=1.0)
    send_u32_echo(ser, address, timeout=1.0)
    send_u32_echo(ser, len(data), timeout=1.0)
    send_u32_echo(ser, sig_len, timeout=1.0)

    status = read_u16be(ser, timeout=2.0)
    print(f"SEND_DA header status: 0x{status:04X}")

    if status == 0x1D0D:
        raise MtkProtocolError("SLA required. Stop here.")

    if status > 0x00FF:
        raise MtkProtocolError(f"SEND_DA header failed, status=0x{status:04X}")

    result = upload_data(ser, data, checksum)

    print()
    print("SEND_DA upload test completed.")
    print("No jump was sent. Reboot/power-cycle the router before next test.")

    return result