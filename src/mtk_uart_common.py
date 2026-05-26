#Written by Qureshi Majad at lut.fi
import time

SYNC = bytes.fromhex("A0 0A 50 05")
EXPECTED_SYNC_REPLY = bytes.fromhex("5F F5 AF FA")

CMD_GET_TARGET_CONFIG = bytes.fromhex("D8")
CMD_GET_HW_CODE = bytes.fromhex("FD")
CMD_SEND_DA = bytes.fromhex("D7")
CMD_JUMP_DA = bytes.fromhex("D5")

OS_KEYWORDS = [
    b"BusyBox",
    b"OpenWrt",
    b"Linux",
    b"built-in shell",
    b"ash",
    b"login:",
    b"root@",
    b"/bin/ash",
    b"U-Boot",
]


class MtkProtocolError(Exception):
    pass


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


def read_available(ser, wait_time: float = 0.10) -> bytes:
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


def read_exact(ser, size: int, timeout: float = 1.0) -> bytes:
    deadline = time.monotonic() + timeout
    data = bytearray()

    while len(data) < size and time.monotonic() < deadline:
        chunk = ser.read(size - len(data))

        if chunk:
            data += chunk

    return bytes(data)


def send_and_expect_echo(ser, data: bytes, timeout: float = 1.0) -> bytes:
    ser.write(data)
    ser.flush()

    echo = read_exact(ser, len(data), timeout=timeout)

    if echo != data:
        raise MtkProtocolError(
            f"Echo mismatch. Sent {hexdump(data)}, received {hexdump(echo)}"
        )

    return echo


def u16be(value: int) -> bytes:
    return value.to_bytes(2, "big")


def u32be(value: int) -> bytes:
    return value.to_bytes(4, "big")


def read_u16be(ser, timeout: float = 1.0) -> int:
    data = read_exact(ser, 2, timeout=timeout)

    if len(data) != 2:
        raise MtkProtocolError(
            f"Expected 2 bytes, received {len(data)}: {hexdump(data)}"
        )

    return int.from_bytes(data, "big")


def read_u32be(ser, timeout: float = 1.0) -> int:
    data = read_exact(ser, 4, timeout=timeout)

    if len(data) != 4:
        raise MtkProtocolError(
            f"Expected 4 bytes, received {len(data)}: {hexdump(data)}"
        )

    return int.from_bytes(data, "big")


def send_u32_echo(ser, value: int, timeout: float = 1.0) -> bytes:
    data = u32be(value)
    return send_and_expect_echo(ser, data, timeout=timeout)