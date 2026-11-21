# Deployment

## Docker

Host networking is only needed for *Search network*. The search is a UDP broadcast on the
switches' network, and a container on a Docker bridge network neither sends into that network nor
receives the answers. Everything else talks to one switch at a time, by unicast to its address.

**Host networking (recommended).** Everything works, including the search. This is the compose
file in the repository root:

```sh
docker compose up -d
docker compose logs switchyard | grep -i password   # generated password, first start only
```

With host networking `ports:` is ignored; change the web port with `SWITCHYARD_SERVER_PORT`.

**Bridged network with a published port.** If you would rather not use host networking, publish
the web port and add every switch with *Add by IP address* (or list the switch addresses in
`SWITCHYARD_NSDP_TARGETS`). The search finds nothing this way.

```yaml
services:
  switchyard:
    image: ghcr.io/philippmundhenk/switchyard:latest
    ports:
      - "8080:8080"
    restart: unless-stopped
    volumes:
      - ./data:/data
```

Docker translates the container's requests to the host's address and passes the switch's answers
back, as long as the switch answers to the sender. Firmware that answers every request by
broadcast (see [Several networks](#several-networks)) cannot be managed from a bridged container.
Not verified this way: firmware upgrades. The switch's TFTP server answers from a new port, which
Docker's address translation usually drops; use host networking for an upgrade. Reports from
real switches on a bridged network are welcome.

## Data folder

Everything Switchyard keeps is in `/data` (the container) or the folder given by `--data` /
`SWITCHYARD_DATA_DIR`: `switchyard.toml`, `devices.json`, `secrets.json`, `audit.log` and
`backups/`. It needs about 1 MB plus a few kB per stored backup, no special file system.

The container runs as user **10001**, not as root. The folder must be writable for that user:

- **Named volume** (`switchyard-data:/data`): works as is; Docker takes the ownership from the image.
- **Bind mount** (`./data:/data`): give the folder to the container user, or run the container
  as the folder's owner:

  ```sh
  mkdir -p data && sudo chown 10001:10001 data
  ```

  ```yaml
  services:
    switchyard:
      user: "1000:1000"   # the output of: id -u, id -g
  ```

If the folder is not writable, Switchyard stops with a message that names the user and the folder.
`devices.json` and `secrets.json` are created readable by that user only (mode 0600).

## Without Docker

```sh
python3 -m venv /opt/switchyard && /opt/switchyard/bin/pip install .
```

`/etc/systemd/system/switchyard.service`:

```ini
[Unit]
Description=Switchyard
After=network-online.target

[Service]
User=switchyard
Environment=SWITCHYARD_DATA_DIR=/var/lib/switchyard
EnvironmentFile=-/etc/switchyard.env
ExecStart=/opt/switchyard/bin/switchyard serve
Restart=on-failure
StateDirectory=switchyard

[Install]
WantedBy=multi-user.target
```

## Firewall

Allow UDP 63321 to 63324 in both directions between the host and the switches, and the web port
for your browsers.

## Several networks

By default Switchyard searches every interface that is up and has an IPv4 address, except the
bridges of Docker, Podman, libvirt and Kubernetes (`docker0`, `br-...`, `veth...`, `virbr...`).
Once a switch has answered, further requests for it go only out of the interface whose subnet
holds its address. Limit the search with `nsdp.interfaces`; interfaces listed there are used even
if they look virtual. For switches on other subnets list directed broadcast or unicast addresses in
`nsdp.targets`, or add them by IP address in the UI (*Switches*, *Add by IP address*).
Switchyard sends to that address directly; the operating system chooses the source address by
route, so the switch can answer through the gateway. Some firmware answers every request by
broadcast instead of to the sender; such answers do not cross a router, and the switch can then
only be managed from its own network segment. A router that does not forward UDP broadcast
blocks the first; unicast to a known address works across routers.

## TLS and proxies

Switchyard speaks plain HTTP. Put a reverse proxy in front for TLS. Forward the `Host` header
unchanged: the origin check compares it with the `Origin` header of write requests.

### Examples

Caddy:

```
switchyard.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

nginx (inside a `server` block with TLS):

```
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 16m;   # firmware images
}
```

The proxy must be listed in `server.forwarded_allow_ips` (default `127.0.0.1`), otherwise the
forwarded scheme is ignored and the session cookie lacks the `Secure` flag. Traefik: the same
headers are sent by default.
