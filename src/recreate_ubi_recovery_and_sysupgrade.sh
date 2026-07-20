#!/bin/sh
set -eu

RECOVERY_IMAGE="${RECOVERY_IMAGE:-/tmp/recovery.itb}"
RECOVERY_VOL="${RECOVERY_VOL:-recovery}"
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

round_up_leb() {
    bytes="$1"
    leb="$2"
    echo $(( ((bytes + leb - 1) / leb) * leb ))
}

image_magic() {
    dd if="$1" bs=4 count=1 2>/dev/null | hexdump -v -e '1/1 "%02x"'
}

log "Add ubootmod recovery UBI volume after sysupgrade"

echo "Recovery image:  $RECOVERY_IMAGE"
echo "Recovery volume: $RECOVERY_VOL"

[ "$(id -u)" = "0" ] || die "must run as root"

for c in awk cat grep dd hexdump strings ubinfo ubiattach ubirmvol ubimkvol ubiupdatevol sync wc; do
    need_cmd "$c"
done

log "Checking that we are in RAM recovery/initramfs"
echo "cmdline: $(cat /proc/cmdline)"

if grep -qw "root=/dev/fit0" /proc/cmdline; then
    die "Refusing to continue: this is normal production OpenWrt, not RAM recovery/initramfs."
fi

log "Checking recovery image"
[ -f "$RECOVERY_IMAGE" ] || die "missing recovery image: $RECOVERY_IMAGE"

MAGIC="$(image_magic "$RECOVERY_IMAGE")"
echo "Image magic: $MAGIC"
[ "$MAGIC" = "d00dfeed" ] || die "recovery image is not FIT/ITB magic d00dfeed"

if strings "$RECOVERY_IMAGE" | grep -Eqi 'zyxel_ex5601-t0-stock|stock layout|ubi2|zyubi'; then
    die "This recovery image looks like STOCK layout. Use ubootmod recovery.itb instead."
fi

if ! strings "$RECOVERY_IMAGE" | grep -Eqi 'ubootmod|zyxel_ex5601-t0-ubootmod'; then
    echo "WARNING: could not clearly detect ubootmod string in recovery image."
    echo "Make sure this is really a ubootmod recovery ITB."
    if [ "$YES" != "1" ]; then
        printf "Type YES to continue anyway: "
        read ans
        [ "$ans" = "YES" ] || die "aborted"
    fi
fi

IMG_SIZE="$(wc -c < "$RECOVERY_IMAGE")"
echo "Image size: $IMG_SIZE bytes"

log "Checking MTD layout"
cat /proc/mtd

UBI_MTD="$(awk -F: 'tolower($0) ~ /"ubi"/ {gsub("mtd","",$1); print $1; exit}' /proc/mtd)"
[ -n "$UBI_MTD" ] || die "cannot find mtd partition named ubi"

echo "UBI MTD number: $UBI_MTD"

log "Attaching UBI if needed"
if [ ! -e /dev/ubi0 ]; then
    ubiattach -m "$UBI_MTD" || true
fi

[ -e /dev/ubi0 ] || die "/dev/ubi0 does not exist"

ubinfo -a

LEB_SIZE="$(ubinfo /dev/ubi0 | awk -F: '/Logical eraseblock size/ {gsub(/[^0-9]/,"",$2); print $2; exit}')"
[ -n "$LEB_SIZE" ] || die "cannot determine UBI LEB size"

RECOVERY_SIZE="$(round_up_leb "$IMG_SIZE" "$LEB_SIZE")"

echo "LEB size:             $LEB_SIZE"
echo "Recovery volume size: $RECOVERY_SIZE"

log "Planned destructive action"
echo "This will delete rootfs_data to free space."
echo "This will create UBI volume: $RECOVERY_VOL"
echo "This will write: $RECOVERY_IMAGE"
echo "It will NOT touch BL2/FIP/Factory/zloader/fit."

if [ "$YES" != "1" ]; then
    echo
    printf "Type YES to continue: "
    read ans
    [ "$ans" = "YES" ] || die "aborted"
fi

log "Deleting rootfs_data to free UBI space"
if vol_exists rootfs_data; then
    ubirmvol /dev/ubi0 -N rootfs_data
else
    echo "rootfs_data not present; continuing"
fi

log "Deleting old recovery volume if present"
if vol_exists "$RECOVERY_VOL"; then
    ubirmvol /dev/ubi0 -N "$RECOVERY_VOL"
else
    echo "old recovery volume not present; continuing"
fi

log "Creating recovery volume"
ubimkvol /dev/ubi0 -N "$RECOVERY_VOL" -s "$RECOVERY_SIZE"

RECDEV="$(voldev_by_name "$RECOVERY_VOL")"
[ -n "$RECDEV" ] || die "cannot find recovery volume device"

echo "Recovery device: $RECDEV"

log "Writing recovery image"
ubiupdatevol "$RECDEV" "$RECOVERY_IMAGE"
sync

log "Verifying volume exists"
ubinfo -a

echo
echo "DONE."
echo "Now reboot.it  will recreate rootfs_data in the remaining free space."

echo "After normal boot, run:"
echo "  /tmp/matrix_flash_inactive.sh --diagnose"
