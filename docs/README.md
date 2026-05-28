
### What this tool does

EX5601-De-Bricker uses the MediaTek BootROM UART recovery path to start a temporary recovery environment entirely from RAM.

The normal flow is:

```text
MediaTek BootROM
  -> UART upload RAM BL2
  -> jump to RAM BL2 using JUMP_DA64
  -> BL2 UART download protocol
  -> upload RAM de-bricker FIP
  -> start de-bricker U-Boot
  -> reach EX5601-DEBRICKER>
  -> inspect or repair flash partitions
```

The RAM de-bricker FIP contains a modified U-Boot environment intended for recovery. It does not depend on NAND U-Boot environment variables.

Expected de-bricker prompt:

```text
EX5601-DEBRICKER>
```

Expected safe U-Boot environment behavior:

```text
Loading Environment from nowhere... OK
bootdelay=-1
```

This prevents a broken NAND environment from interfering with recovery.

### Supported target

This project is intended for:

```text
Zyxel EX5601-T0 / T56
MediaTek MT7986
OpenWrt ubootmod layout
SPI-NAND flash
```

Expected MTD layout:

```text
bl2         0x000000-0x100000
u-boot-env  0x100000-0x180000
Factory     0x180000-0x380000
fip         0x380000-0x540000
zloader     0x540000-0x580000
ubi         0x580000-0x1e000000
```

Partition names are matched case-insensitively. For example, `Factory` and `factory` are treated as the same partition.

The tool blocks write actions if the expected layout is not detected.

### BootROM details

The tool talks directly to the MediaTek BootROM over UART.

Known working values for this target:

```text
Initial UART baud:       115200
BootROM upload baud:     460800
BL2 FIP load baud:       921600
U-Boot loady baud:       460800
BL2 load address:        0x00201000
U-Boot/FIT load address: 0x46000000
```

BootROM sync:

```text
TX: A0 0A 50 05
RX: 5F F5 AF FA
```

MT7986 hardware code:

```text
GET_HW_CODE -> 79 86 00 00
HW code: 0x7986
```

The tool uses `JUMP_DA64` for MT7986 RAM BL2.

### Payload files

The RAM boot payloads are:

```text
payload_bl2.bin
paylaod_FIP.bin
```

Note: `paylaod_FIP.bin` is intentionally spelled this way if your local scripts already use that filename.

Repair payloads are:

```text
repair_bl2.bin
repair_fip.bin
```

Optional factory restore image:

```text
factory.bin
```

Recommended folder layout for Python development:

```text
mtk/
  loader.py
  repair_bootchain.py
  debricker_menu.py
  paths.py
  payload_bl2.bin
  paylaod_FIP.bin
  repair_bl2.bin
  repair_fip.bin
  factory.bin
```

Recommended folder layout for releases:

```text
EX5601-De-Bricker/
  ex5601-debricker.exe
  factory.bin                  # optional, root directory only
  payloads/
    payload_bl2.bin
    paylaod_FIP.bin
    repair_bl2.bin
    repair_fip.bin
```

### BL2 repair behavior

The BL2 partition is 1 MiB:

```text
0x100000 bytes
```

A real BL2/preloader image may be smaller than the full partition. The tool pads the image to the full partition size with `0xFF`, uploads the padded image, verifies RAM CRC, writes the partition, reads it back, and verifies CRC again.

BL2 repair flow:

```text
read repair_bl2.bin
pad to 0x100000 with 0xFF
upload to RAM at 0x46000000
crc32 RAM copy
mtd erase bl2
mtd write bl2 0x46000000
mtd read bl2 0x47000000 0x0 0x100000
crc32 read-back copy
```

Expected success markers:

```text
WRITE_BL2_OK
READBACK_BL2_OK
CRC OK
bl2 repair completed and verified.
```

### FIP repair behavior

The FIP partition is 0x1C0000 bytes:

```text
0x380000-0x540000
```

The tool pads `repair_fip.bin` to the full FIP partition size with `0xFF`.

FIP repair flow:

```text
read repair_fip.bin
pad to 0x1C0000 with 0xFF
upload to RAM at 0x46000000
crc32 RAM copy
mtd erase fip
mtd write fip 0x46000000
mtd read fip 0x47000000 0x0 0x1c0000
crc32 read-back copy
```

Expected success markers:

```text
WRITE_FIP_OK
READBACK_FIP_OK
CRC OK
fip repair completed and verified.
```

### Factory restore behavior

Factory restore is dangerous and should only be used if the Factory/factory partition was erased or corrupted.

The factory partition contains device-specific data such as calibration, MAC addresses, board data, and RF-related information.

Factory restore file:

```text
factory.bin
```

Requirements:

```text
factory.bin must be in the root directory beside the script/EXE.
factory.bin must be exactly 0x200000 bytes / 2097152 bytes / 2 MiB.
The user must type YES before writing.
The layout check must pass.
```

Factory restore flow:

```text
read factory.bin
verify size is exactly 0x200000
upload to RAM at 0x46000000
crc32 RAM copy
mtd erase Factory/factory
mtd write Factory/factory 0x46000000
mtd read Factory/factory 0x47000000 0x0 0x200000
crc32 read-back copy
```

Expected success markers:

```text
WRITE_FACTORY_OK
READBACK_FACTORY_OK
CRC OK
Factory restore completed and verified.
```

Do not use a random or generated Factory image unless this is an emergency and you understand the consequences. A Factory image from another router may cause duplicate MAC addresses or incorrect RF calibration.

### RAM rescue FIT / ITB mode

If included, the tool can also upload a rescue FIT/ITB image:

```text
payload_RESCUE.itb
```

The RAM rescue flow uses U-Boot `loady` and YMODEM:

```text
loady 0x46000000 460800
upload payload_RESCUE.itb
iminfo 0x46000000
bootm 0x46000000
```

This is useful for Linux-side recovery or more advanced NAND/UBI repair work.

### Safety rules

The tool should never automatically overwrite:

```text
Factory/factory
zloader
ubi
u-boot-env
```

Factory restore is the only exception, and it must be a dedicated explicit menu option.

Normal boot-chain repair should only write:

```text
bl2
fip
```

Recommended order:

```text
1. Repair FIP.
2. Test normal boot.
3. Repair BL2 only if required.
4. Restore Factory only from a valid backup.
```

### Building from source

Install dependencies:

```bash
pip install pyserial
```

Run from source on Windows:

```powershell
python debricker_menu.py COM3
```

Run from source on Linux:

```bash
python3 debricker_menu.py /dev/ttyUSB0
```

### Building release binaries

Install PyInstaller:

```bash
pip install pyinstaller pyserial
```

Build Windows EXE on Windows:

```powershell
pyinstaller --clean --onefile --name ex5601-debricker debricker_menu.py
```

Output:

```text
dist\ex5601-debricker.exe
```

Build Linux binary on Linux:

```bash
pyinstaller --clean --onefile --name ex5601-debricker debricker_menu.py
```

Output:

```text
dist/ex5601-debricker
```

PyInstaller is not a cross-compiler. Build the Windows executable on Windows and the Linux executable on Linux.

### Release packaging

Windows release package:

```text
EX5601-De-Bricker-Windows/
  ex5601-debricker.exe
  payloads/
    payload_bl2.bin
    paylaod_FIP.bin
    repair_bl2.bin
    repair_fip.bin
```

Linux release package:

```text
EX5601-De-Bricker-Linux/
  ex5601-debricker
  payloads/
    payload_bl2.bin
    paylaod_FIP.bin
    repair_bl2.bin
    repair_fip.bin
```

Optional:

```text
factory.bin
payload_RESCUE.itb
```

Keep `factory.bin` outside `payloads/`, 