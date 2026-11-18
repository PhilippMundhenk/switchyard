# Deployment

## Docker

Use host networking. NSDP discovery is a UDP broadcast on the switches' network; a bridged
container never sees the answers. See the compose file in the repository root.

```sh
docker compose up -d
docker compose logs switchyard | grep -i password   # generated password, first start only
```

With host networking `ports:` is ignored; change the web port with `SWITCHYARD_SERVER_PORT`.

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

By default Switchyard searches every interface that is up and has an IPv4 address. Limit it with
`nsdp.interfaces`. For switches on other subnets list directed broadcast or unicast addresses in
`nsdp.targets`, or add them by IP address in the UI. A router that does not forward UDP broadcast
blocks the first; unicast to a known address works across routers.

## TLS and proxies

Switchyard speaks plain HTTP. Put a reverse proxy in front for TLS. Forward the `Host` header
unchanged: the origin check compares it with the `Origin` header of write requests.
