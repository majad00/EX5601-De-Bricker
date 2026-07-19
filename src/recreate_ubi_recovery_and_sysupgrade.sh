#!/bin/sh
set -eu

FIRMWARE="${FIRMWARE:-/tmp/firmware.bin}"

# Optional but strongly recommended.
# This must be a ubootmod recovery FIT/ITB, not stock-layout.
RECOVERY_IMAGE="${RECOVERY_IMAGE:-/tmp/recovery.itb}"

RECOVERY_VOL="${RECOVERY_VOL:-recovery}"
RECOVERY_SIZE_BYTES="${RECOVERY_SIZE_BYTES:-16777216}"   # 16 MiB
ALLOW_EMPTY_RECOVERY="${ALLOW_EMPTY_RECOVERY:-0}"
YES="${YES:-0}"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

log() {
    echo
    echo "======================================================================"
    echo "$*"
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

voldev_by_name() {
    name="$1"
    for p in /sys/class/ubi/ubi0_*; do
        [ -f "$p/name" ] || continue
        if [ "$(cat "$p/name")" = "$name" ]; then
            echo "/dev/$(basename "$p")"
            return 0
        fi
    done
    return 1
}

vol_exists() {
    voldev_by_name "$1" >/dev/null 2>&1
}

image_magic() {
    dd if="$1" bs=4 count=1 2>/dev/null | hexdump -v -e '1/1 "%02x"'
}

log "UBI recovery volume creator + sysupgrade"
echo "Firmware:        $FIRMWARE"
echo "Recovery image:  $RECOVERY_IMAGE"
echo "Recovery volume: $RECOVERY_VOL"
echo "Recovery size:   $RECOVERY_SIZE_BYTES bytes"
echo "Allow empty:     $ALLOW_EMPTY_RECOVERY"

[ "$(id -u)" = "0" ] || die "must run as root"

for c in awk cat grep dd hexdump strings ubinfo ubiattach ubirmvol ubimkvol ubiupdatevol sysupgrade sync; do
    need_cmd "$c"
done

[ -f "$FIRMWARE" ] || die "firmware not found: $FIRMWARE"

log "Checking that we are in RAM recovery/initramfs"
echo "cmdline: $(cat /proc/cmdline)"

if grep -qw "root=/dev/fit0" /proc/cmdline; then
    die "Refusing to continue: this looks like normal production boot, not RAM recovery/initramfs."
fi

log "Checking MTD layout"
cat /proc/mtd

UBI_MTD="$(awk -F: '/"ubi"/ {gsub("mtd","",$1); print $1; exit}' /proc/mtd)"
[ -n "$UBI_MTD" ] || die "cannot find MTD partition named ubi"

echo "UBI MTD number: $UBI_MTD"

if ! grep -qi '"bl2"' /proc/mtd; then die "cannot find bl2 partition"; fi
if ! grep -qi '"factory"' /proc/mtd; then die "cannot find Factory/factory partition"; fi
if ! grep -qi '"fip"' /proc/mtd; then die "cannot find fip partition"; fi
if ! grep -qi '"zloader"' /proc/mtd; then die "cannot find zloader partition"; fi

log "Attaching UBI if needed"
if [ ! -e /dev/ubi0 ]; then
    ubiattach -m "$UBI_MTD" || true
fi

[ -e /dev/ubi0 ] || die "/dev/ubi0 does not exist after attach"

ubinfo -a

HAS_RECOVERY_IMAGE=0

if [ -f "$RECOVERY_IMAGE" ]; then
    log "Checking recovery image"
    MAGIC="$(image_magic "$RECOVERY_IMAGE")"
    echo "Recovery image magic: $MAGIC"

    [ "$MAGIC" = "d00dfeed" ] || die "recovery image is not FIT/ITB magic d00dfeed"

    if strings "$RECOVERY_IMAGE" | grep -Eqi 'zyxel_ex5601-t0-stock|stock layout|ubi2|zyubi'; then
        die "recovery image looks like STOCK-layout image. Use ubootmod recovery ITB instead."
    fi

    HAS_RECOVERY_IMAGE=1
    echo "Recovery image looks usable."
else
    echo
    echo "WARNING: recovery image not found: $RECOVERY_IMAGE"
    echo "The script can reserve a recovery UBI volume, but it will NOT be bootable yet."
    echo "Auto-recovery will only work after you write a valid ubootmod recovery ITB into it."

    if [ "$ALLOW_EMPTY_RECOVERY" != "1" ]; then
        die "No recovery image. Put ubootmod recovery ITB at $RECOVERY_IMAGE, or set ALLOW_EMPTY_RECOVERY=1 to reserve only."
    fi
fi

log "Testing firmware image with sysupgrade"
sysupgrade -T "$FIRMWARE" || die "sysupgrade test failed for $FIRMWARE"

log "Planned destructive action"
echo "This will remove UBI volume: rootfs_data"
echo "This will create/recreate UBI volume: $RECOVERY_VOL"
echo "This will NOT touch BL2/FIP/Factory/zloader directly."
echo "Then it will run: sysupgrade -n $FIRMWARE"

if [ "$YES" != "1" ]; then
    echo
    printf "Type YES to continue: "
    read ans
    [ "$ans" = "YES" ] || die "aborted"
fi

log "Removing rootfs_data to free UBI space"
if vol_exists rootfs_data; then
    ubirmvol /dev/ubi0 -N rootfs_data
else
    echo "rootfs_data does not exist; continuing"
fi

log "Removing old recovery volume if present"
if vol_exists "$RECOVERY_VOL"; then
    ubirmvol /dev/ubi0 -N "$RECOVERY_VOL"
else
    echo "old recovery volume does not exist; continuing"
fi

log "Creating recovery volume"
ubimkvol /dev/ubi0 -N "$RECOVERY_VOL" -s "$RECOVERY_SIZE_BYTES"

RECDEV="$(voldev_by_name "$RECOVERY_VOL")"
[ -n "$RECDEV" ] || die "cannot find newly created recovery volume device"

echo "Recovery volume device: $RECDEV"

if [ "$HAS_RECOVERY_IMAGE" = "1" ]; then
    log "Writing recovery image into recovery volume"
    ubiupdatevol "$RECDEV" "$RECOVERY_IMAGE"
    sync
else
    log "Skipping recovery image write"
    echo "WARNING: recovery volume exists but is empty/unusable until written later."
fi

log "UBI layout before sysupgrade"
ubinfo -a

log "Running sysupgrade"
sync
sysupgrade -n "$FIRMWARE"