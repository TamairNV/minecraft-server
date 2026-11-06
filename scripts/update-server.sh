#!/usr/bin/env bash
# Move the server and plugins to a new Minecraft version. Backs up first.
# The live world upgrades permanently when it next loads, so to start fresh on the
# new version, swap to a new world on the site's Worlds tab afterwards.
# Usage: update-server.sh <mc-version>     e.g. update-server.sh 26.4
set -euo pipefail
VERSION=${1:?usage: update-server.sh <mc-version>}
HERE=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
MC="$HERE/scripts/mc"
get() { grep "^$1=" "$HERE/.env" | cut -d= -f2; }
SD="$HERE/crafty/servers/$(get CRAFTY_SERVER_ID)"
BK="/mnt/fpv2tb/minecraft-backups/$(get CRAFTY_SERVER_ID)/$(get CRAFTY_BACKUP_ID)"

curl -fsS "https://fill.papermc.io/v3/projects/paper/versions/$VERSION/builds" | grep -q STABLE \
  || { echo "No stable Paper build for $VERSION yet."; exit 1; }
read -r -p "Stop the server and update it to $VERSION? Type yes: " a
[ "$a" = yes ] || exit 1

"$MC" stop
for _ in $(seq 1 90); do docker exec minecraft-crafty sh -c 'ps -eo args | grep -q "[p]aper.jar"' || break; sleep 2; done

BEFORE=$(ls -t "$BK" | head -1)
"$MC" backup
echo "Backing up..."
for _ in $(seq 1 360); do
  NEW=$(ls -t "$BK" | head -1)
  [ "$NEW" != "$BEFORE" ] && python3 -I -c 'import sys,zipfile;sys.exit(not zipfile.is_zipfile(sys.argv[1]))' "$BK/$NEW" && break
  sleep 5
done
[ "$NEW" != "$BEFORE" ] || { echo "Backup didn't finish, so nothing was changed. Run: $MC start"; exit 1; }

python3 -I "$HERE/scripts/install-plugins.py" "$VERSION" "$SD"
echo "Anything marked NOT declared above may refuse to load on $VERSION: check the log."
"$MC" start
echo "Starting. Watch with: $MC logs"
