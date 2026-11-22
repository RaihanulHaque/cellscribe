#!/usr/bin/env bash
# Consistent snapshot of the gateway database (devices, tokens' hashes, request log).
#   ./backup.sh [directory]     default: ./backups, keeps the newest 14
# Run it from cron on the server, e.g.:  0 2 * * * cd /opt/libre-agent/gateway && ./backup.sh
set -euo pipefail
cd "$(dirname "$0")"
dir=${1:-backups}
mkdir -p "$dir"; chmod 700 "$dir"
out="$dir/librethinkeragent-$(date +%Y%m%d-%H%M%S).db"
docker compose exec -T librethinkeragent-gateway python -c "
import os, sqlite3, sys, tempfile
tmp = tempfile.mktemp()
src = sqlite3.connect(os.environ['LIBRETHINKERAGENT_DB'])
dst = sqlite3.connect(tmp)
src.backup(dst); dst.close(); src.close()
sys.stdout.buffer.write(open(tmp, 'rb').read()); os.remove(tmp)
" > "$out"
chmod 600 "$out"
python3 -c "import sqlite3,sys; print('backup ok:', sys.argv[1], '-', sqlite3.connect(sys.argv[1]).execute('select count(*) from devices').fetchone()[0], 'devices')" "$out"
ls -1t "$dir"/librethinkeragent-*.db | tail -n +15 | xargs -r rm --
