#!/usr/bin/env bash
# A3.4 teardown (idempotent; needs sudo): kills anything still running in the A3 netns,
# then deletes them (veths and br-a3 go with their netns).
set -uo pipefail
SUDO=""; [[ $(id -u) -eq 0 ]] || SUDO="sudo"
for ns in ns-peer-b ns-peer-a ns-gw ns-a3-br; do
  if $SUDO ip netns list | awk '{print $1}' | grep -qx "$ns"; then
    pids=$($SUDO ip netns pids "$ns" 2>/dev/null || true)
    [[ -n "$pids" ]] && $SUDO kill $pids 2>/dev/null; sleep 0.2
    pids=$($SUDO ip netns pids "$ns" 2>/dev/null || true)
    [[ -n "$pids" ]] && $SUDO kill -9 $pids 2>/dev/null
    $SUDO ip netns del "$ns"
  fi
done
left=$($SUDO ip netns list | awk '{print $1}' | grep -xE 'ns-gw|ns-peer-a|ns-peer-b|ns-a3-br' || true)
if [[ -n "$left" ]]; then echo "FAIL still present: $left" >&2; exit 1; fi
echo "A3_NETNS_DOWN"
