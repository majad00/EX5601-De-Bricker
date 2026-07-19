# EX5601-De-Bricker

### If your EX5601 / T56 router has become a paper-weight, this tool can de-bricks it.
> Do not requite LAN connection to router, it works using UART serial connection only.

### How it works
EX5601-De-Bricker is written in Python and uses the MediaTek BootROM UART protocol, including BootROM sync and RAM-stage loading, similar in concept to tools such as mtk_uartboot. 

It is built specifically for Zyxel EX5601-T0 / T56 routers and understands the router’s boot chain, partition layout, and recovery requirements.

**Recovery often requires a UART terminal, TFTP server, manual U-Boot commands, and careful partition handling, but with this tool you automates everything**

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

### For Expert users only ( Examples on Windows 10:
> [!TIP]
> Download bundle from: "https://github.com/majad00/ex5601-openwrt-ubootmod-to-stock-layout/"releases/download/1.1/restore_bundle_ex5601.tar.gz" beofore you start

```powershell
cd src
python loader.py COM3
```
When linux is booted, go to web site 192.168.1.18080 to complete recovery or SSH 192.168.1.1 to router.

### Other options:
From SRC dir simple run:

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



# Troubleshooting 

If the Ymodem transfer fails and you need to use a LAN to upload files OR if you wish to perform a manual repair, follow these steps:

#### Step 1: Prepare Files on Your PC

You need the following files:

- `repair_bl2.bin`
- `repair_fip.bin`

Place these files in a single folder, for example:

`C:\De-Bricker\`


#### Step 2: Create Padded BL2 and FIP Files

Open PowerShell in the folder where the files are located.


#### Create Padded BL2

Run the following command:

```powershell
python -c "from pathlib import Path; d=Path('repair_bl2.bin').read_bytes(); assert len(d)<=0x100000; Path('repair_bl2_padded.bin').write_bytes(d+b'\xff'*(0x100000-len(d))); print('repair_bl2:', len(d), '->', 0x100000)"
```


#### Create Padded FIP

Run the following command:

```powershell
python -c "from pathlib import Path; d=Path('repair_fip.bin').read_bytes(); assert len(d)<=0x1c0000; Path('repair_fip_padded.bin').write_bytes(d+b'\xff'*(0x1c0000-len(d))); print('repair_fip:', len(d), '->', 0x1c0000)"
```

After running these commands, you should have the following padded files:

- `repair_bl2_padded.bin` = 1,048,576 bytes
- `repair_fip_padded.bin` = 1,835,008 bytes


#### Step 3: Start TFTP Server

Place the padded files in your TFTP server root directory:

- `repair_bl2_padded.bin`
- `repair_fip_padded.bin`


#### Example Network Settings

- PC / TFTP Server IP: `192.168.1.10`
- Router U-Boot IP: `192.168.1.2`

Connect your PC's Ethernet to the router's LAN port.


#### Step 4: Boot RAM De-Bricker

Run your de-bricker tool with the following command:

```powershell
.\ex5601-debricker.exe COM3
```

Alternatively, use the Python version:

```powershell
python debricker_menu.py COM3
```

Power-cycle the router when the tool waits for BootROM synchronization.

When the menu appears, select:

9. Open manual U-Boot command prompt

You should see:

`EX5601-DEBRICKER>`


#### Step 5: Confirm Layout Before Writing

Run the command:

```shell
mtd list
```

Ensure you see the exact safe layout:

```
0x000000000000-0x000000100000 : "bl2"
0x000000100000-0x000000180000 : "u-boot-env"
0x000000180000-0x000000380000 : "Factory"
0x000000380000-0x000000540000 : "fip"
0x000000540000-0x000000580000 : "zloader"
0x000000580000-0x00001e000000 : "ubi"
```

If the FIP shows an incorrect layout (e.g., `0x000000380000-0x000000580000 : "fip"`), proceed at your own risk, as this overlaps with the zloader area.


#### Step 6: Set Network in U-Boot

At the `EX5601-DEBRICKER>` prompt, run:

```shell
setenv ipaddr 192.168.1.2
setenv serverip 192.168.1.10
```

Test the TFTP with the FIP file:

```shell
tftpboot 0x46000000 repair_fip_padded.bin
```


#### Expected Output:

```
Bytes transferred = 1835008
```


#### Step 7: Repair FIP First

Run these commands in order:

```shell
tftpboot 0x46000000 repair_fip_padded.bin
crc32 0x46000000 0x1c0000
mtd erase fip
mtd write fip 0x46000000 0x0 0x1c0000
mtd read fip 0x47000000 0x0 0x1c0000
crc32 0x47000000 0x1c0000
```

The two CRC values must match. For example:

```
crc32 for 46000000 ... 461bffff ==> XXXXXXXX
crc32 for 47000000 ... 471bffff ==> XXXXXXXX
```

If they match, the FIP is successfully repaired.


#### Step 8: Repair BL2 Second

Run these commands in order:

```shell
tftpboot 0x46000000 repair_bl2_padded.bin
crc32 0x46000000 0x100000
mtd erase bl2
mtd write bl2 0x46000000 0x0 0x100000
mtd read bl2 0x47000000 0x0 0x100000
crc32 0x47000000 0x100000
```

Like before, the two CRC values must match. For example:

```
crc32 for 46000000 ... 460fffff ==> XXXXXXXX
crc32 for 47000000 ... 470fffff ==> XXXXXXXX
```

If they match, the BL2 is successfully repaired.


#### Step 9: Reboot and Test Flash Boot

Run the command:

```shell
reset
```

Now watch the UART output. A successful boot should indicate:

```
Jump to BL
NOTICE: BL2 ...
NOTICE: BL2: Booting BL31
NOTICE: BL31 ...
U-Boot ...
```

If you see the U-Boot message, the repairs for BL2 and FIP were successful.




#### Payload files

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
## Recreate UBI Recovery Volume

If a router has lost its recovery volume, you can recreate it using the following steps with a script located in the `/src` directory.

1. After downloading the source code, navigate to the `/src` directory:
   ```powershell
   cd src
   python loader.py COM3
   ```

2. Download the following files from the OpenWrt archive:
   - `ubootmod sysupgrade` and rename it to `firmware.bin`
   - `ubootmod recovery` and rename it to `recovery.itb`

3. After the `loader.sh` script finishes, copy these three files to the router:
   - The script `recreate_ubi_recovery_and_sysupgrade.sh` from the `/src` directory of the repository.
   - `firmware.bin`
   - `recovery.itb`

4. Once these files are copied to the router's `/tmp` directory, and run commands:
   ```bash
   cd /tmp
   chmod +x recreate_ubi_recovery_and_sysupgrade.sh
   YES=1 /tmp/recreate_ubi_recovery_and_sysupgrade.sh
   ```

This will recreate the partition, copy the recovery image to the recovery partition, and initiate the sysupgrade. Finally, the router will reboot.

# Building from source

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

## Building release binaries

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
