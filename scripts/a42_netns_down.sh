#!/usr/bin/env bash
# Tear down the A4.2 topology (idempotent). Kills only processes inside the a42-* namespaces.
set -uo pipefail
SUDO=""; [[ $(id -u) -eq 0 ]] || SUDO="sudo"
for ns in a42-peer a42-gw a42-relay; do
  if $SUDO ip netns list | awk '{print $1}' | grep -qx "$ns"; then
    pids=$($SUDO ip netns pids "$ns" 2>/dev/null)
    [[ -n "$pids" ]] && $SUDO kill -9 $pids 2>/dev/null
    $SUDO ip netns del "$ns"
  fi
done
echo "A42_NETNS_DOWN"
