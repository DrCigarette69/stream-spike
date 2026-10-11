#!/usr/bin/env bash
# Tear down the A4.3 topology (idempotent): a43-site + /etc/netns/a42-peer, then a42_netns_down.sh.
set -uo pipefail
SUDO=""; [[ $(id -u) -eq 0 ]] || SUDO="sudo"
HERE="$(cd "$(dirname "$0")" && pwd)"
if $SUDO ip netns list | awk '{print $1}' | grep -qx a43-site; then
  pids=$($SUDO ip netns pids a43-site 2>/dev/null)
  [[ -n "$pids" ]] && $SUDO kill -9 $pids 2>/dev/null
  $SUDO ip netns del a43-site
fi
$SUDO rm -rf /etc/netns/a42-peer
bash "$HERE/a42_netns_down.sh"
echo "A43_NETNS_DOWN"
