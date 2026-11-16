#!/bin/sh
# Run once with sudo. Makes the empty /mnt/fpv2tb folder on the main disk (the one hidden
# under the 2TB drive when it is mounted) read-only, so if the drive is missing at boot
# Minecraft still starts but backups fail instead of quietly filling the main disk.
# The folders Docker binds are pre-created so containers still start.
# Undo: same bind mount, then chattr -R -i "$T/mnt/fpv2tb".
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
SERVER=bbcec7f2-5e56-4d6c-95f6-64f4635787f2
BACKUP=$(grep "^CRAFTY_BACKUP_ID=" /home/tnv/minecraft/.env | cut -d= -f2)
[ -n "$BACKUP" ] || { echo "CRAFTY_BACKUP_ID not found"; exit 1; }
T=$(mktemp -d)
mount --bind / "$T"            # plain bind: shows the main disk without the 2TB drive on top
trap "umount $T; rmdir $T" EXIT
U="$T/mnt/fpv2tb"
if [ -n "$(ls -A "$U" | grep -v ^minecraft-backups$ || true)" ]; then
  echo "unexpected files under the mountpoint on the main disk:"; ls -la "$U"; exit 1
fi
LEAF="$U/minecraft-backups/$SERVER/$BACKUP"
mkdir -p "$LEAF"
chown -R 1000:0 "$U/minecraft-backups"; chmod -R 2775 "$U/minecraft-backups"
chattr -R +i "$U/minecraft-backups"
chattr +i "$U"
lsattr -d "$U" "$U/minecraft-backups" "$U/minecraft-backups/$SERVER" "$LEAF"
echo "done; live drive untouched:"; mountpoint /mnt/fpv2tb; touch /mnt/fpv2tb/minecraft-backups/.guard-test && rm /mnt/fpv2tb/minecraft-backups/.guard-test && echo "live backups folder writable"
