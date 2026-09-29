# Fake Relay line protocol (spike)

Peer dials `gateway:9100`. Newline-delimited JSON. ALPN name `stream/tunnel/1` is logical (not wire TLS in fake lane).

## Peer → Gateway
`{"type":"HELLO","peer_id":"peer_demo","endpoint_id":"iroh_ep_…","isp_ack_version":"v1","host_tier":"always_on"}`

Empty `isp_ack_version` → Gateway closes with `isp_ack_required`.

## Gateway → Peer
`{"type":"HELLO_OK","alpn":"stream/tunnel/1"}`

## Gateway → Peer (per stream)
`{"type":"AUTH_TICKET","alpn":"stream/tunnel/1","ticket_json":"…","stream_id":"str_…","dest_host":"…","dest_port":443}`

Peer must `POST /v1/tickets/verify` (or local HMAC with shared secret) and check `peer_endpoint_id == self`.

## Peer → Gateway
`{"type":"AUTH_OK"}` or `{"type":"AUTH_REJECT","error":"…"}` or egress deny.

## Gateway → Peer
`{"type":"OPEN","stream_id":"…"}` then `{"type":"BYTES","stream_id":"…","n":N}` …

`{"type":"CLOSE","stream_id":"…"}` — Peer teardown-only (no early cut on grace).
