#!/usr/bin/env bash
# A3.4 multi-node netns topology (idempotent; needs sudo). No default route anywhere.
#
#   ns-a3-br : bridge br-a3 (no IP) — kept off the host so nodes can't reach the host stack
#   ns-gw     a3eth0 10.73.0.1/24   (Gateway + Control)
#   ns-peer-a a3eth0 10.73.0.11/24  (Peer A)
#   ns-peer-b a3eth0 10.73.0.12/24  (Peer B)
#
# Only the connected 10.73.0.0/24 route exists in each node ns (iroh 0.95.1's portmapper
# can't be disabled, so "no default route" is the egress guard). Tear down: a3_netns_down.sh
set -euo pipefail
SUDO=""; [[ $(id -u) -eq 0 ]] || SUDO="sudo"
BRNS=ns-a3-br
declare -A ADDR=([ns-gw]=10.73.0.1 [ns-peer-a]=10.73.0.11 [ns-peer-b]=10.73.0.12)
declare -A VETH=([ns-gw]=a3v-gw [ns-peer-a]=a3v-pa [ns-peer-b]=a3v-pb)

ensure_ns() {
  $SUDO ip netns list | awk '{print $1}' | grep -qx "$1" || $SUDO ip netns add "$1"
  $SUDO ip -n "$1" link set lo up
  # no IPv6 router advertisements -> no v6 default route sneaking in
  $SUDO ip netns exec "$1" sysctl -qw net.ipv6.conf.all.accept_ra=0 net.ipv6.conf.default.accept_ra=0 || true
}

ensure_ns "$BRNS"
$SUDO ip -n "$BRNS" link show br-a3 >/dev/null 2>&1 || $SUDO ip -n "$BRNS" link add br-a3 type bridge
$SUDO ip -n "$BRNS" link set br-a3 up

for ns in ns-gw ns-peer-a ns-peer-b; do
  ensure_ns "$ns"
  v=${VETH[$ns]}
  if ! $SUDO ip -n "$ns" link show a3eth0 >/dev/null 2>&1; then
    $SUDO ip -n "$BRNS" link del "$v" >/dev/null 2>&1 || true
    $SUDO ip -n "$BRNS" link add "$v" type veth peer name a3eth0 netns "$ns"
  fi
  $SUDO ip -n "$BRNS" link set "$v" master br-a3 up
  $SUDO ip netns exec "$ns" sysctl -qw net.ipv6.conf.a3eth0.accept_ra=0 || true
  $SUDO ip -n "$ns" addr replace "${ADDR[$ns]}/24" dev a3eth0
  $SUDO ip -n "$ns" link set a3eth0 up
done

fail=0
for ns in "$BRNS" ns-gw ns-peer-a ns-peer-b; do
  d4=$($SUDO ip -n "$ns" route show default); d6=$($SUDO ip -n "$ns" -6 route show default)
  if [[ -n "$d4$d6" ]]; then echo "FAIL default route in $ns: $d4 $d6" >&2; fail=1; fi
done
[[ $fail -eq 0 ]] || exit 1
for ns in ns-gw ns-peer-a ns-peer-b; do
  echo "$ns ${ADDR[$ns]} routes: $($SUDO ip -n "$ns" route show | tr '\n' ';')"
done
echo "A3_NETNS_UP br-a3 10.73.0.0/24 (no default route in any ns)"
