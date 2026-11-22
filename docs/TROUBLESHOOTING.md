# Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| *Search network* finds nothing | Not the same network segment; container not on host networking; firewall blocks UDP 63321-63324; the switch firmware has no NSDP. Check *Settings*: it lists the interfaces in use. Try *Add by IP address*. |
| "Network interfaces found: none" | The container is bridged. Use `network_mode: host`. |
| Switch "not reachable" after moving to a bridged container | Set its *Fixed address* (switch, *Maintenance*, *This entry*). If the *Protocol log* then shows requests to that address but no answers, the switch answers by broadcast or a firewall blocks UDP 63321/63322: use host networking or macvlan. See [DEPLOYMENT.md, Networking](DEPLOYMENT.md#networking-which-setup). |
| Traefik answers 404 | Traefik has no route: the container is not healthy yet, host networking without `host.docker.internal` in Traefik, or a wrong `loadbalancer.server.port`. See [Behind Traefik](DEPLOYMENT.md#behind-traefik). |
| Switch found but reading fails | Look in *Protocol log*: a result code 15 or 10 means management through this protocol is switched off. |
| "Incorrect password" | A fresh switch uses `password`. Three failures lock it for 30 minutes. A factory reset restores the default. |
| Changes are refused with "not supported" | The model does not have that feature. The tab says so. |
| "The switch is not answering" after a reboot | It takes about 30 seconds. Press *Try again*. |
| Locked out after a VLAN or IP change | Press the reset button of the switch. |
| "cannot write its data folder" at start | The container runs as user 10001. `sudo chown -R 10001:10001 ./data`, or `user: "1000:1000"` in the compose file, or a named volume. See DEPLOYMENT.md, *Data folder*. |
| Cannot log in to Switchyard | Without a configured password one is generated on first start and printed once on the console. Run `switchyard reset-password` (Docker: `docker compose run --rm switchyard reset-password`) and restart. |
| "This host name is not allowed" (421) | Login is disabled, so only IP addresses and `localhost` are accepted, or the name is not in `server.allowed_hosts`. Add the name there. |
| *Add by IP address* says no switch answers | Check that UDP 63321/63322 pass the router or firewall. Some firmware answers only by broadcast, which no router forwards; manage such a switch from its own segment. |
| Too many failed logins | Wait a minute. The lock applies only to the address that failed. |

## Questions

**Can I use LACP?** No. The firmware of these switches contains no LACP, only static trunks. Set the
device at the other end to a static trunk too (Linux: bonding mode `balance-xor`; other switches:
"static" or "on", not "LACP/active"). Link aggregation in Switchyard is static only.

**Can I change the speed of a port that is part of a LAG?** Only together with all members; the switch
refuses mixed settings and a member cannot be switched off.
