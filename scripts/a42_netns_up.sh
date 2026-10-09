#!/usr/bin/env bash
# A4.2 relay-only netns topology (idempotent; needs sudo). No default route anywhere.
#
#   a42-gw    a42g0 10.73.0.1/25   + 10.73.0.254/32 dev a42g0   (Gateway + Control)
#   a42-relay a42rg 10.73.0.126/25 (gw side)  a42rp 10.73.0.254/25 (peer side)  ip_forward=0
#   a42-peer  a42p0 10.73.0.200/25                               (Peer / test client)
#
# The relay ns is the only one attached to both sides and does not forward, so Gateway and Peer
# can reach the relay (10.73.0.254) but have no route to each other: no direct path exists.
# All addresses are inside the A3.0 default allowlist 10.73.0.0/24. Tear down: a42_netns_down.sh
set -euo pipefail
SUDO=""; [[ $(id -u) -eq 0 ]] || SUDO="sudo"

ensure_ns() {
  $SUDO ip netns list | awk '{print $1}' | grep -qx "$1" || $SUDO ip netns add "$1"
  $SUDO ip -n "$1" link set lo up
  $SUDO ip netns exec "$1" sysctl -qw net.ipv6.conf.all.accept_ra=0 net.ipv6.conf.default.accept_ra=0 || true
  $SUDO ip netns exec "$1" sysctl -qw net.ipv4.ip_forward=0 net.ipv6.conf.all.forwarding=0
}

# veth <ns-a> <if-a> <ns-b> <if-b>
veth() {
  if ! $SUDO ip -n "$1" link show "$2" >/dev/null 2>&1; then
    $SUDO ip -n "$3" link del "$4" >/dev/null 2>&1 || true
    $SUDO ip -n "$1" link add "$2" type veth peer name "$4" netns "$3"
  fi
  $SUDO ip netns exec "$1" sysctl -qw "net.ipv6.conf.$2.accept_ra=0" || true
  $SUDO ip netns exec "$3" sysctl -qw "net.ipv6.conf.$4.accept_ra=0" || true
  $SUDO ip -n "$1" link set "$2" up
  $SUDO ip -n "$3" link set "$4" up
}

for ns in a42-gw a42-relay a42-peer; do ensure_ns "$ns"; done
veth a42-relay a42rg a42-gw a42g0
veth a42-relay a42rp a42-peer a42p0
$SUDO ip -n a42-relay addr replace 10.73.0.126/25 dev a42rg
$SUDO ip -n a42-relay addr replace 10.73.0.254/25 dev a42rp
$SUDO ip -n a42-gw addr replace 10.73.0.1/25 dev a42g0
$SUDO ip -n a42-gw route replace 10.73.0.254/32 dev a42g0
$SUDO ip -n a42-peer addr replace 10.73.0.200/25 dev a42p0

fail=0
for ns in a42-gw a42-relay a42-peer; do
  d4=$($SUDO ip -n "$ns" route show default); d6=$($SUDO ip -n "$ns" -6 route show default)
  if [[ -n "$d4$d6" ]]; then echo "FAIL default route in $ns: $d4 $d6" >&2; fail=1; fi
  [[ $($SUDO ip netns exec "$ns" sysctl -n net.ipv4.ip_forward) == 0 ]] || { echo "FAIL ip_forward on in $ns" >&2; fail=1; }
done
[[ $fail -eq 0 ]] || exit 1
for ns in a42-gw a42-relay a42-peer; do
  echo "$ns routes: $($SUDO ip -n "$ns" route show | tr '\n' ';')"
done
echo "A42_NETNS_UP relay 10.73.0.254 (gw 10.73.0.1, peer 10.73.0.200; no default route, no gw<->peer route)"
