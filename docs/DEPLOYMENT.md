# Deployment

## Docker

```sh
docker compose up -d
docker compose logs switchyard | grep -i password   # generated password, first start only
```

The container runs as user 10001, so a bind-mounted `./data` folder has to belong to that user
before the first start: `mkdir -p data && sudo chown 10001:10001 data` (other options under
[Data folder](#data-folder)).

### Networking: which setup?

Switchyard talks to the switches over UDP. How the container is attached to the network decides
what works:

| | Host networking | Bridged, published port | macvlan |
| --- | --- | --- | --- |
| *Search network* (broadcast) | yes | no | yes |
| NETGEAR switch added by IP address | yes | only if the switch answers to the sender | yes |
| NETGEAR switch that answers by broadcast | yes | no | yes |
| TP-Link Easy Smart (answers by broadcast) | yes | no | yes |
| Firmware upgrade (TFTP) | yes | no (Docker drops the TFTP answers) | yes |
| Separation from the host | none: shares the host's network | full | own LAN address |
| Effort | lowest | low | a network definition |

**Use host networking unless you have a reason not to.** Bridged works only for some NETGEAR
switches; macvlan is the way to keep the container separate and still have everything.

### Host networking

This is the compose file in the repository root (`network_mode: host`).

- `ports:` and `expose:` have no effect. The web port is `SWITCHYARD_SERVER_PORT` (default 8080),
  opened directly on the host. Pick a free one: the container runs as a normal user and cannot
  open ports below 1024, and port 80/443 usually belong to a reverse proxy anyway.
- The port listens on every interface of the host unless `SWITCHYARD_SERVER_HOST` names one
  address. Behind a reverse proxy, see [Behind Traefik](#behind-traefik) for how to keep the port
  away from the rest of the network.
- Switchyard searches every interface except Docker's, Podman's, libvirt's and Kubernetes'
  bridges. The log says which ones it uses (`NSDP on UDP 63321 via [...]`); narrow it with
  `SWITCHYARD_NSDP_INTERFACES`.
- The container shares the host's network: it sees all interfaces and can reach services that
  listen only on the host's `localhost`. This is the price for broadcasts; it runs as an
  unprivileged user.

### Bridged network with a published port

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

The search finds nothing here: its broadcast stays inside the Docker network. Add every switch
with *Add by IP address*, or list the addresses in `SWITCHYARD_NSDP_TARGETS`. Docker translates
the container's requests to the host's address and passes the answers back, **as long as the switch
answers to the address that asked**. Some NETGEAR firmware answers every request by broadcast, and
a broadcast never reaches a bridged container, whatever ports you publish. A GS108E v1 with
firmware 1.00.09 gave no answers in a bridged container. TP-Link Easy Smart switches always answer
by broadcast and cannot be used this way.

Switches added while Switchyard ran with host networking have no stored address: set it under the
switch, *Maintenance*, *This entry*, *Fixed address* (shown also when the switch does not answer).
The switch card then shows `unicast <address>`.

**Checking whether a switch works bridged.** Open the switch once, then:

1. *Protocol log*: `tx ... 192.168.1.20:63322` lines mean the requests go out to the switch's
   address. `rx` lines from it mean it works. No `tx` to its address: the fixed address is not
   set.
2. Requests but no `rx`: on the Docker host, watch the traffic while you open the switch page:
   ```sh
   sudo tcpdump -ni any 'udp port 63321 or udp port 63322'
   ```
   - answers to `255.255.255.255.63321` or the subnet's broadcast: the switch answers by
     broadcast. Use host networking or macvlan.
   - no answer at all: a firewall or route between the container's network and the switches
     blocks the requests, or the switch ignores them.
   - answers to the host's address that still do not show up in the protocol log: something on
     the host drops them; please report it.

### macvlan: an own address on the switches' network

A macvlan network gives the container its own address on the LAN, so broadcasts, the search and
firmware upgrades work without host networking. A second, normal network keeps it reachable for
a reverse proxy:

```yaml
networks:
  proxy:
    external: true            # the network your reverse proxy is on
  lan:
    driver: macvlan
    driver_opts:
      parent: eth0            # the host's LAN interface
    ipam:
      config:
        - subnet: 192.168.1.0/24
          gateway: 192.168.1.1
          ip_range: 192.168.1.240/29   # addresses your DHCP server does not hand out

services:
  switchyard:
    image: ghcr.io/philippmundhenk/switchyard:latest
    restart: unless-stopped
    volumes:
      - ./data:/data
    networks:
      proxy: {}
      lan: {}
```

The host itself cannot talk to a macvlan address (other machines and other containers can).
Limit the search to the LAN side with `SWITCHYARD_NSDP_INTERFACES` if it picks the wrong
interface; the log shows the interfaces in use.

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
forwarded scheme is ignored and the session cookie lacks the `Secure` flag. Subnets work too,
e.g. `172.18.0.0/16`.
### Behind Traefik

Traefik sends the forwarded headers by default. Which labels it needs depends on the networking
setup above.

**Bridged or macvlan.** Put Switchyard on Traefik's network and route to its port:

```yaml
services:
  switchyard:
    image: ghcr.io/philippmundhenk/switchyard:latest
    restart: unless-stopped
    volumes:
      - ./data:/data
    networks:
      - proxy
    labels:
      - traefik.enable=true
      - traefik.docker.network=proxy     # needed when the container has several networks
      - traefik.http.routers.switchyard.rule=Host(`switchyard.example.com`)
      - traefik.http.routers.switchyard.entrypoints=https
      - traefik.http.routers.switchyard.tls.certresolver=letsencrypt
      - traefik.http.services.switchyard.loadbalancer.server.port=8080
    environment:
      SWITCHYARD_SERVER_FORWARDED_ALLOW_IPS: "172.18.0.0/16"   # the subnet of the proxy network
```

**Host networking.** Traefik cannot reach the container over a Docker network, so it goes to the
host instead:

1. Traefik's own service needs a name for the host. This is a Compose option, next to
   `command:`, whatever way Traefik itself is configured:
   ```yaml
   services:
     traefik:
       extra_hosts:
         - "host.docker.internal:host-gateway"
   ```
   For containers with host networking Traefik connects to `host.docker.internal` (without the
   entry it falls back to 127.0.0.1, which is Traefik itself). Check with
   `docker exec traefik getent hosts host.docker.internal`. The entry only names an address
   Traefik could already reach; it opens nothing new.
2. Switchyard listens on a free port of the host, and the label says which:
   ```yaml
   services:
     switchyard:
       image: ghcr.io/philippmundhenk/switchyard:latest
       network_mode: host
       restart: unless-stopped
       volumes:
         - ./data:/data
       labels:
         - traefik.enable=true
         - traefik.http.routers.switchyard.rule=Host(`switchyard.example.com`)
         - traefik.http.routers.switchyard.entrypoints=https
         - traefik.http.routers.switchyard.tls.certresolver=letsencrypt
         - traefik.http.services.switchyard.loadbalancer.server.port=7432
       environment:
         SWITCHYARD_SERVER_PORT: "7432"
         SWITCHYARD_SERVER_FORWARDED_ALLOW_IPS: "172.18.0.0/16"   # Traefik's network
   ```
   Not port 80: that is Traefik's, and the container may not open ports below 1024. No
   `networks:` and no `expose:`.
3. Traefik's requests still come from its container address (Docker translates addresses only
   for traffic leaving the host), so trust the subnet of Traefik's network. The access log shows
   which address Switchyard sees: `INFO: 172.18.0.5:41234 - "GET / ..."`. Subnets work in
   `server.forwarded_allow_ips`; a single address breaks when Traefik gets a new one, unless you
   pin it with `ipv4_address`.
4. Keep the port away from the rest of the network. With host networking it is open on every
   interface of the host, and whoever reaches it directly skips Traefik and any authentication
   middleware in front (Switchyard's own login still applies; keep it on). Either
   - allow only Traefik's network in the host firewall:
     ```sh
     iptables -I INPUT -p tcp --dport 7432 ! -s 172.18.0.0/16 -j DROP
     ```
     (make it permanent the way your host keeps its firewall rules), or
   - let Switchyard listen on the Docker gateway only: `SWITCHYARD_SERVER_HOST: "172.17.0.1"`,
     the address `host.docker.internal` resolves to. The LAN cannot reach it then; Switchyard
     does not start if that address does not exist yet when it starts.

   Trusting a whole subnet means any container in it that reaches the port could fake its client
   address (used for the audit log and the per-client login lockout); the firewall rule or the
   gateway address prevents that too.

**When Traefik answers 404**, it has no route to Switchyard:

| Cause | Check |
| --- | --- |
| The container is not healthy yet, or unhealthy: Traefik ignores such containers | `docker ps` shows `(healthy)`. Older images checked 127.0.0.1 even with `SWITCHYARD_SERVER_HOST` set; update, or override `healthcheck:` with the right address |
| Host networking without `host.docker.internal` in Traefik | `docker exec traefik getent hosts host.docker.internal` |
| Wrong port in `loadbalancer.server.port` | it must match `SWITCHYARD_SERVER_PORT` |
| Several networks without `traefik.docker.network` | Traefik picks the wrong one |
| Host firewall blocks Traefik | the rule above allows Traefik's network |
