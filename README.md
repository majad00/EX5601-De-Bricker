# EX5601-De-Bricker

### If your EX5601 / T56 router has bricked and become paper-weight, this tool can de-bricks
**Recovery often requires a UART terminal, TFTP server, manual U-Boot commands, and careful partition handling, but with this tool you automates everything**

### How it works
EX5601-De-Bricker is written in Python and uses the MediaTek BootROM UART protocol, including BootROM sync and RAM-stage loading, similar in concept to tools such as mtk_uartboot. 
It is built specifically for Zyxel EX5601-T0 / T56 routers and understands the router’s boot chain, partition layout, and recovery requirements.
It can:
- Detect the EX5601/T56 BootROM target.
- Use controlled EX5601-DEBRICKER>.
- Verify the expected ubootmod MTD partition layout.
- Repair BL2 and FIP directly from the host script.
- Pad images to the correct partition size automatically.
- Write selected partitions safely.
- Read back written partitions and verify them with CRC32.
- Restore Factory/factory from backup.



## 1. Quick guide
(**Download de-bricker and run it from PC while router connecte to UART**)

###  download

For normal users, download the latest tested build from the  **Releases** section.

Windows / Linux: Latest release packag from here =  https://github.com/majad00/EX5601-De-Bricker/releases/download/1.1/EX5601-De-Bricker-v1.zip

```text
Windows: EX5601-De-Bricker-v1.zip
```

Extract the archive and keep the included files together.


### Windows quick start

Open PowerShell in the extracted folder, suppose the com  port is COM3

Run:

```powershell
.\ex5601-debricker.exe COM3
```
OR if you have python installed run from source
```powershell
python .\debricker_menu.py com3
```

Start De-Bricker and then power-cycle the router to go through BootROM sync.
Wait for the **Repair menu ** to laod, and select the repair option from menu list

### Linux quick start

Connect the USB-UART adapter and check the serial device:

```bash
ls /dev/ttyUSB*
```

Run:
Precompile binar:
```bash
./ex5601-debricker /dev/ttyUSB0
```
from source:

```bash
chmod +x loader.sh ; ./loader.sh /dev/ttyUSB0
```
OR
```bash 
python3 ./debricker_menu.py /dev/ttyUSB0
```

### Repair menu

After BootROM sync, the tool loads the RAM de-bricker and probes the router.

If the router has the expected EX5601-T0 ubootmod layout, the menu enables repair options:

```text
1. Probe hardware / print U-Boot and MTD info
2. Re-check partition layout
3. Repair FIP partition
4. Repair BL2 partition
5. Repair both: FIP first, then BL2
6. Restore Factory from backup [DANGEROUS]
7. Show current U-Boot environment
8. Reset / reboot router
9. Open manual U-Boot command prompt
0. Exit
```

Recommended repair order:

```text
1. Probe first.
2. Repair FIP first if U-Boot/FIP is damaged.
3. Repair BL2 only if the preloader/BL2 is damaged.
4. Restore Factory only from a known-good backup.
```

### For Expert users,  command examples

If you are using the Python version instead of the compiled release:

```powershell
python debricker_menu.py COM3
```

Probe only:

```powershell
python repair_bootchain.py COM3 --probe
```

Repair FIP:

```powershell
python repair_bootchain.py COM3 --write-fip repair_fip.bin --yes
```

Repair BL2:

```powershell
python repair_bootchain.py COM3 --write-bl2 repair_bl2.bin --yes
```

Repair both:

```powershell
python repair_bootchain.py COM3 --write-fip repair_fip.bin --write-bl2 repair_bl2.bin --yes
```

---
### Payload files

The RAM boot payloads are:

```text
payload_bl2.bin # this is original bl2 file taken from Openwrt release
paylaod_FIP.bin # this is modified version of FIP uboot, the used patch to create this can be found in the /src dir
```

Note: All files intentionally spelled this way and should be same if you replace them with latest release

Repair payloads are:

```text
repair_bl2.bin # non modified version of bl2, you can replace it with your on boot loader
repair_fip.bin # non modified version of FIP image you can replease with your own.
```

Optional factory restore image:

```text
factory.bin # not provided with release 
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
