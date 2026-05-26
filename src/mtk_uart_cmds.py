from mtk_uart_common import (
    CMD_GET_HW_CODE,
    CMD_GET_TARGET_CONFIG,
    MtkProtocolError,
    hexdump,
    read_exact,
    send_and_expect_echo,
)


def get_hw_code(ser):
    print("Sending GET_HW_CODE command: FD")

    send_and_expect_echo(ser, CMD_GET_HW_CODE, timeout=1.0)

    raw = read_exact(ser, 4, timeout=1.0)

    if len(raw) != 4:
        raise MtkProtocolError(
            f"GET_HW_CODE expected 4 data bytes, received {len(raw)}: {hexdump(raw)}"
        )

    value = int.from_bytes(raw, "big")
    hwcode = (value >> 16) & 0xFFFF
    hwver = value & 0xFFFF

    print(f"GET_HW_CODE data: {hexdump(raw)}")
    print(f"HW code: 0x{hwcode:04X}")
    print(f"HW ver:  0x{hwver:04X}")
    print()

    return {
        "raw": raw,
        "value": value,
        "hwcode": hwcode,
        "hwver": hwver,
    }


def decode_target_config(value: int):
    return {
        "sbc": bool(value & 0x1),
        "sla": bool(value & 0x2),
        "daa": bool(value & 0x4),
        "swjtag": bool(value & 0x6),
        "epp": bool(value & 0x8),
        "cert": bool(value & 0x10),
        "memread": bool(value & 0x20),
        "memwrite": bool(value & 0x40),
        "cmd_c8_blocked": bool(value & 0x80),
    }


def get_target_config(ser):
    print("Sending GET_TARGET_CONFIG command: D8")

    send_and_expect_echo(ser, CMD_GET_TARGET_CONFIG, timeout=1.0)

    raw = read_exact(ser, 6, timeout=1.0)

    if len(raw) != 6:
        raise MtkProtocolError(
            f"GET_TARGET_CONFIG expected 6 data bytes, received {len(raw)}: {hexdump(raw)}"
        )

    target_config = int.from_bytes(raw[0:4], "big")
    status = int.from_bytes(raw[4:6], "big")

    flags = decode_target_config(target_config)

    print(f"GET_TARGET_CONFIG data: {hexdump(raw)}")
    print(f"Target config: 0x{target_config:08X}")
    print(f"Status:        0x{status:04X}")
    print()
    print("Decoded target config:")
    print(f"  SBC enabled:        {flags['sbc']}")
    print(f"  SLA enabled:        {flags['sla']}")
    print(f"  DAA enabled:        {flags['daa']}")
    print(f"  SWJTAG enabled:     {flags['swjtag']}")
    print(f"  EPP enabled:        {flags['epp']}")
    print(f"  Root cert required: {flags['cert']}")
    print(f"  Mem read auth:      {flags['memread']}")
    print(f"  Mem write auth:     {flags['memwrite']}")
    print(f"  Cmd C8 blocked:     {flags['cmd_c8_blocked']}")
    print()

    return {
        "raw": raw,
        "target_config": target_config,
        "status": status,
        "flags": flags,
    }