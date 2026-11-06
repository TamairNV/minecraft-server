#!/usr/bin/env bash
# Queue pre-generation of the whole 10,000 x 10,000 block border. Chunky keeps one
# task per dimension and resumes after restarts.
set -euo pipefail
MC="$(dirname "$(readlink -f "$0")")/mc"
for dim in overworld the_nether the_end; do
  for c in "chunky world minecraft:$dim" "chunky shape square" "chunky center 0 0" "chunky radius 5000" "chunky start" "chunky confirm"; do
    $MC $c >/dev/null; sleep 1
  done
done
echo "Pre-generation queued for overworld, nether and end."
