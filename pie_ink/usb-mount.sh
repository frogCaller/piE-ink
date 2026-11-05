#!/bin/sh
# Mounts a filesystem on a USB disk the moment it's plugged in — read-only, so it can be pulled out
# any time — at /media/<its kernel name> (sda1, sdb2…), readable by PiE-ink's user. udev calls it,
# from the rule setup.sh writes (/etc/udev/rules.d/99-pie-ink-usb.rules); systemd unmounts it again
# when the drive is pulled out. setup.sh installs it as /usr/local/sbin/pie-ink-usb-mount.
#   pie-ink-usb-mount DEVICE NAME TYPE UID GID
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
dev="$1"; name="$2"; fs="$3"; uid="$4"; gid="$5"
[ -b "$dev" ] || exit 0
case "$name" in ""|*[!A-Za-z0-9]*) exit 0 ;; esac            # (only ever /media/sda1 and the like)
case "$uid$gid" in ""|*[!0-9]*) exit 0 ;; esac
grep -q "^$dev " /proc/mounts && exit 0                      # mounted already (by hand, or a desktop)
# the disk the Pi itself runs from (when it boots off USB) is left alone
disk="$(lsblk -ndo PKNAME "$dev" 2>/dev/null)"
[ -n "$disk" ] || disk="$(basename "$dev")"
for own in / /boot /boot/firmware; do
  src="$(findmnt -nvo SOURCE "$own" 2>/dev/null)" || continue
  [ -b "$src" ] || continue
  theirs="$(lsblk -ndo PKNAME "$src" 2>/dev/null)"
  [ -n "$theirs" ] || theirs="$(basename "$src")"
  [ "$theirs" = "$disk" ] && exit 0
done
case "$fs" in
  vfat|exfat|ntfs|ntfs3) opts="ro,uid=$uid,gid=$gid,fmask=0133,dmask=0022" ;;
  *) opts="ro" ;;
esac
exec systemd-mount --no-block --collect --options="$opts" "$dev" "/media/$name"
