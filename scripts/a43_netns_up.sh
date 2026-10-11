#!/usr/bin/env bash
# A4.3 topology = A4.2 relay-only topology (a42_netns_up.sh) + a stand-in public site (idempotent; sudo).
# No default route anywhere.
#
#   a43-site  a43s0 198.51.100.10/24  (TEST-NET-2 stand-in "public" site, :443 TLS)
#   a42-peer  a43ps 198.51.100.1/24   (only the Peer side reaches the site; Gateway/relay have no route)
#
# Test names (Peer ns only, via /etc/netns/a42-peer/hosts, which `ip netns exec` bind-mounts over
# /etc/hosts; empty resolv.conf = no DNS): echo.example.com -> 198.51.100.10 (stand-in),
# priv.example.com -> 10.73.0.1 (private: the Peer's SSRF check must refuse it).
# 198.51.100.0/24 counts as public only in a4_local builds (guard::LOCAL_TEST_SITE_CIDR).
set -euo pipefail
SUDO=""; [[ $(id -u) -eq 0 ]] || SUDO="sudo"
HERE="$(cd "$(dirname "$0")" && pwd)"
bash "$HERE/a42_netns_up.sh"

$SUDO ip netns list | awk '{print $1}' | grep -qx a43-site || $SUDO ip netns add a43-site
$SUDO ip -n a43-site link set lo up
$SUDO ip netns exec a43-site sysctl -qw net.ipv6.conf.all.accept_ra=0 net.ipv6.conf.default.accept_ra=0 || true
$SUDO ip netns exec a43-site sysctl -qw net.ipv4.ip_forward=0 net.ipv6.conf.all.forwarding=0
# Let the unprivileged stand-in bind :443 inside its own netns only.
$SUDO ip netns exec a43-site sysctl -qw net.ipv4.ip_unprivileged_port_start=443
if ! $SUDO ip -n a42-peer link show a43ps >/dev/null 2>&1; then
  $SUDO ip -n a43-site link del a43s0 >/dev/null 2>&1 || true
  $SUDO ip -n a42-peer link add a43ps type veth peer name a43s0 netns a43-site
fi
$SUDO ip -n a42-peer link set a43ps up
$SUDO ip -n a43-site link set a43s0 up
$SUDO ip -n a42-peer addr replace 198.51.100.1/24 dev a43ps
$SUDO ip -n a43-site addr replace 198.51.100.10/24 dev a43s0

$SUDO mkdir -p /etc/netns/a42-peer
printf '127.0.0.1 localhost\n198.51.100.10 echo.example.com\n10.73.0.1 priv.example.com\n' | $SUDO tee /etc/netns/a42-peer/hosts >/dev/null
: | $SUDO tee /etc/netns/a42-peer/resolv.conf >/dev/null

for ns in a42-gw a42-relay a42-peer a43-site; do
  d=$($SUDO ip -n "$ns" route show default; $SUDO ip -n "$ns" -6 route show default)
  [[ -z "$d" ]] || { echo "FAIL default route in $ns: $d" >&2; exit 1; }
done
echo "a43-site routes: $($SUDO ip -n a43-site route show | tr '\n' ';')"
echo "A43_NETNS_UP site 198.51.100.10:443 (reachable from a42-peer only; no default route anywhere)"
