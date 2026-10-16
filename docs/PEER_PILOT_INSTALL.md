# Pilot Peer install (A4.5)

For the two pilot Linux devices. **amd64 only**: both pilot devices are x86_64.

**Approved pilot image ID: `sha256:2452f56d86fe01d99cbfb3f284ef79fb99471017014c0dacbdb364a2dc41e21a`.**
The image is pinned by ID, never by tag: `peer-pilot:amd64` is only a convenience name and a
rebuild can silently move it. Every load/verify/run below uses the ID. A new ID replaces this one
only after `a45` passes on it (`SPIKE_PEER_IMAGE_ID=<new id>`) and the room agrees.

The image is built from `rust/crates/stream-peer/Dockerfile.pilot`. It contains no key and no
settings: each device's identity key and daily byte counter live in a Docker volume mounted at
`/data`. The status page listens only inside the container (127.0.0.1:9200), so use
`docker exec` to reach it.

## 0. Install Docker

On a fresh Ubuntu device: `sudo apt-get update && sudo apt-get install -y docker.io`, then
`sudo systemctl enable --now docker`. Keep the default unix socket only (no `-H tcp://`, nothing
on 2375/2376), and don't add your user to the `docker` group; every command below uses `sudo docker`.

## 1. Get the image

On a build box with the repo:

```
sudo docker build -f rust/crates/stream-peer/Dockerfile.pilot -t peer-pilot:amd64 rust/
sudo docker image inspect -f '{{.Id}}' sha256:2452f56d86fe01d99cbfb3f284ef79fb99471017014c0dacbdb364a2dc41e21a   # must succeed
sudo docker save sha256:2452f56d86fe01d99cbfb3f284ef79fb99471017014c0dacbdb364a2dc41e21a | gzip > peer-pilot-amd64.tar.gz
```

(Or run `SPIKE_PEER_IMAGE_ID=sha256:2452f56d86fe01d99cbfb3f284ef79fb99471017014c0dacbdb364a2dc41e21a python3 scripts/a45_peer_package_smoke.py`,
which FAILs if the build isn't that ID and prints `A45_IMAGE_ID=<id>`.)

Copy `peer-pilot-amd64.tar.gz` to the device, then `sudo docker load < peer-pilot-amd64.tar.gz`
and verify by ID: `sudo docker image inspect -f '{{.Id}}' sha256:2452f56d86fe01d99cbfb3f284ef79fb99471017014c0dacbdb364a2dc41e21a`
must print that same ID. Don't verify via the tag.

Note: Docker 29+ (containerd image store, the Ubuntu 26.04 default) shows the *manifest* digest
as the image ID instead, so the ID differs. Check the config digest instead:
`sudo ctr -n moby content get <shown ID> | grep -o '"config":{[^}]*'` must name the ID above.
In that case run by the shown (manifest) ID once its config digest is confirmed; the tag is optional.

## 2. Settings file

Create `/etc/peer-pilot.env` (mode 0600) with the live pilot values below (Jeff's VPS,
`170-9-253-4.sslip.io`, Let's Encrypt). Leave the last block commented out so you see each
consent screen yourself. Allowlist and caps are the defaults; public egress is off by default
(the kill switch on Control starts off: `public_egress: false`).

```
# Pilot Gateway endpoint ID (fresh server key, not the dev key)
SPIKE_GATEWAY_ENDPOINT_ID=218c4864f8a99e38329c103172957cfc0185590f05b34997156240d02b3acdc7
# The one relay this device may use (also used to reach the Gateway)
SPIKE_RELAY_ALLOW_URL=https://170-9-253-4.sslip.io/
# Control (exposes only /health, GET /v1/egress/state, POST /v1/tickets/verify)
CONTROL_URL=https://control.170-9-253-4.sslip.io
# Sites traffic may reach: host:443, comma separated (allowlist version 27b285b4…2bc1)
SPIKE_EGRESS_ALLOWLIST=echo.170-9-253-4.sslip.io:443
# Where the on/off switch is read from
SPIKE_EGRESS_STATE_URL=https://control.170-9-253-4.sslip.io/v1/egress/state
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
  sha256:2452f56d86fe01d99cbfb3f284ef79fb99471017014c0dacbdb364a2dc41e21a
```

(Docker 29+: use the manifest ID you confirmed in step 1.) Check what is running:
`sudo docker inspect -f '{{.Image}}' peer` must print the approved ID.

If a setting is missing the container exits right away; `sudo docker logs peer` names the
problem (for example `a4_refuse_relay_config:<unset>` = no relay URL).
### First run: register the device ID

On first start the Peer creates its key in `/data` and logs its ID:

```
sudo docker logs peer 2>&1 | grep iroh_endpoint_id
# peer iroh_endpoint_id=<64 hex characters>
```

Send that ID to Jeff, who passes it to Platform to add to `/opt/stream-pilot/peer_endpoints.txt`
on the relay server. Until it is listed, the relay refuses this device (only listed devices are
admitted). Nothing else needs to change; the Peer retries on its own.

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
