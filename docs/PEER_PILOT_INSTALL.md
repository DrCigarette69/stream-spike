# Pilot Peer install (A4.5)

For the two pilot Linux devices. **amd64 only for now** (arm64 image comes once the device
architecture is confirmed; check yours with `uname -m`: `x86_64` = amd64, `aarch64` = arm64).

The image is built from `rust/crates/stream-peer/Dockerfile.pilot`. It contains no key and no
settings: each device's identity key and daily byte counter live in a Docker volume mounted at
`/data`. The status page listens only inside the container (127.0.0.1:9200), so use
`docker exec` to reach it.

## 1. Get the image

On a build box with the repo:

```
sudo docker build -f rust/crates/stream-peer/Dockerfile.pilot -t peer-pilot:amd64 rust/
sudo docker save peer-pilot:amd64 | gzip > peer-pilot-amd64.tar.gz
```

For an arm64 device (`aarch64`), the same file cross-builds on an amd64 box (no emulation needed):

```
sudo docker buildx build --platform linux/arm64 --load \
  -f rust/crates/stream-peer/Dockerfile.pilot -t peer-pilot:arm64 rust/
sudo docker save peer-pilot:arm64 | gzip > peer-pilot-arm64.tar.gz
```

Copy `peer-pilot-amd64.tar.gz` to the device, then `sudo docker load < peer-pilot-amd64.tar.gz`.
Compare `sudo docker image inspect -f '{{.Id}}' peer-pilot:amd64` with the ID you were given.

## 2. Settings file

Create `/etc/peer-pilot.env` (mode 0600). Fill in the values you were sent; leave the last
block commented out so you see each consent screen yourself.

```
# Your gateway's endpoint ID (64 hex characters)
SPIKE_GATEWAY_ENDPOINT_ID=
# The one relay this device may use, e.g. https://relay.example.org/
SPIKE_RELAY_ALLOW_URL=
# Sites traffic may reach: host:443, comma separated
SPIKE_EGRESS_ALLOWLIST=
# Where the on/off switch is read from (sent with your setup details)
SPIKE_EGRESS_STATE_URL=
# Daily byte cap (default 50000000)
#SPIKE_EGRESS_BYTE_CAP=50000000

# Leave these OFF: you accept each screen yourself (step 4).
#SPIKE_ISP_ACK_VERSION=
#SPIKE_P2_CONSENT=
#SPIKE_P9_ACK=
#SPIKE_PUBLIC_EGRESS=
```

Already set inside the image (do not change): `SPIKE_TRANSPORT=iroh_pilot`,
`SPIKE_IROH_KEY_PATH=/data/peer_iroh_key`, `SPIKE_EGRESS_BUDGET_PATH=/data/peer_egress_budget`,
`SPIKE_PEER_ADMIN=127.0.0.1:9200`.

## 3. Run it

```
sudo docker volume create peer-data
sudo docker run -d --name peer --restart unless-stopped \
  --env-file /etc/peer-pilot.env \
  -v peer-data:/data \
  peer-pilot:amd64
```

If a setting is missing the container exits right away; `sudo docker logs peer` names the
problem (for example `a4_refuse_relay_config:<unset>` = no relay URL).
The device ID stays the same across restarts and upgrades as long as you keep the `peer-data`
volume. Deleting the volume creates a new ID.

## 4. Accept the screens

Read each screen, then accept it:

```
sudo docker exec peer peer-admin GET /peer/consent/p1        # read
sudo docker exec peer peer-admin POST /peer/ack '{"isp_ack_version":"v1","understood":true}'

sudo docker exec peer peer-admin GET /peer/consent/p2        # read
sudo docker exec peer peer-admin POST /peer/consent '{"accepted":true}'
```

The allowlist screen (`/peer/consent/p9`, accept with `{"accepted":true}` to the same path)
only shows if public sharing is turned on for the pilot.

## 5. Check it

```
sudo docker ps --filter name=peer          # STATUS shows (healthy)
sudo docker exec peer peer-admin GET /health
sudo docker exec peer peer-admin GET /peer/screen/p8   # why sharing stopped, if it did
sudo docker exec peer peer-admin GET /peer/egress      # sites, byte count, switch
```

`"ok":true` means it is running; `"online":true` means it is sharing right now.

## 6. Pause or stop

- Pause sharing (stays paused until you resume):
  `sudo docker exec peer peer-admin POST /peer/kill`
- Resume: `sudo docker exec peer peer-admin POST /peer/resume`
- Stop everything: `sudo docker stop peer` (saves today's byte count first).
- Withdraw consent: POST `{"accepted":false}` to `/peer/consent`; sharing stops until you accept again.
- Remove: `sudo docker rm -f peer` (add `sudo docker volume rm peer-data` to also delete the device key).
