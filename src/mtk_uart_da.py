import time

from mtk_uart_common import (
    CMD_SEND_DA,
    CMD_JUMP_DA,
    MtkProtocolError,
    hexdump,
    printable,
    read_available,
    read_exact,
    read_u16be,
    send_and_expect_echo,
    send_u32_echo,
    u32be,
)


def prepare_data(payload: bytes, sig_len: int = 0, maxsize=None):
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

    if len(data) % 2 != 0:
        data += b"\x00"

    checksum = 0

    for pos in range(0, len(data), 2):
        checksum ^= int.from_bytes(data[pos:pos + 2], "little")

    checksum &= 0xFFFF

    return checksum, data


def upload_data(ser, data: bytes, expected_checksum: int, chunk_size: int = 0x400):
    print(f"Uploading {len(data)} byte(s)...")

    pos = 0
    remaining = len(data)

    while remaining > 0:
        size = min(remaining, chunk_size)
        ser.write(data[pos:pos + size])
        ser.flush()

        pos += size
        remaining -= size

        if pos % 0x2000 == 0:
            ser.flush()

    ser.flush()
    time.sleep(0.12)

    returned_checksum = read_u16be(ser, timeout=2.0)
    status = read_u16be(ser, timeout=2.0)

    print(f"Returned checksum: 0x{returned_checksum:04X}")
    print(f"Expected checksum: 0x{expected_checksum:04X}")
    print(f"Upload status:      0x{status:04X}")

    if returned_checksum != expected_checksum and returned_checksum != 0x0000:
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


def send_da(
    ser,
    address: int,
    payload: bytes,
    sig_len: int = 0,
    maxsize=None,
):
    checksum, data = prepare_data(payload, sig_len=sig_len, maxsize=maxsize)

    print("SEND_DA upload")
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
    print("SEND_DA upload completed.")
    print()

    return result


def send_da_no_jump(
    ser,
    address: int,
    payload: bytes,
    sig_len: int = 0,
    maxsize=None,
):
    result = send_da(
        ser=ser,
        address=address,
        payload=payload,
        sig_len=sig_len,
        maxsize=maxsize,
    )

    print("No jump was sent. Reboot/power-cycle the router before next test.")
    print()

    return result


def jump_da(ser, address: int):
    print("JUMP_DA")
    print(f"Command: D5")
    print(f"Address: 0x{address:08X}")
    print()

    send_and_expect_echo(ser, CMD_JUMP_DA, timeout=1.0)

    ser.write(u32be(address))
    ser.flush()

    raw_addr = read_exact(ser, 4, timeout=2.0)

    if len(raw_addr) != 4:
        raise MtkProtocolError(
            f"JUMP_DA expected 4-byte returned address, got {len(raw_addr)}: "
            f"{hexdump(raw_addr)}"
        )

    returned_addr = int.from_bytes(raw_addr, "big")

    print(f"Returned address: 0x{returned_addr:08X}")

    if returned_addr != address:
        raise MtkProtocolError(
            f"JUMP_DA returned wrong address: expected 0x{address:08X}, "
            f"got 0x{returned_addr:08X}"
        )

    status = read_u16be(ser, timeout=2.0)

    print(f"JUMP_DA status:   0x{status:04X}")

    if status != 0x0000:
        raise MtkProtocolError(f"JUMP_DA failed, status=0x{status:04X}")

    print()
    print("JUMP_DA accepted. Payload should now be running.")
    print()

    return {
        "returned_addr": returned_addr,
        "status": status,
    }


def listen_after_jump(ser, seconds: float = 5.0):
    if seconds <= 0:
        return b""

    print(f"Listening for payload output for {seconds:.1f} second(s)...")
    print()

    deadline = time.monotonic() + seconds
    collected = bytearray()

    while time.monotonic() < deadline:
        data = read_available(ser, wait_time=0.10)

        if data:
            collected += data
            print(data.decode("utf-8", errors="replace"), end="", flush=True)

    print()
    print()
    print(f"Payload output length: {len(collected)} byte(s)")

    if collected:
        print("Payload output hex:")
        print(hexdump(bytes(collected)))
        print()
        print("Payload output printable:")
        print(printable(bytes(collected)))

    return bytes(collected)