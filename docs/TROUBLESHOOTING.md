# Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| *Search network* finds nothing | Not the same network segment; container not on host networking; firewall blocks UDP 63321-63324; the switch firmware has no NSDP. Check *Settings*: it lists the interfaces in use. Try *Add by IP address*. |
| "Network interfaces found: none" | The container is bridged. Use `network_mode: host`. |
| Switch found but reading fails | Look in *Protocol log*: a result code 15 or 10 means management through this protocol is switched off. |
| "Incorrect password" | A fresh switch uses `password`. Three failures lock it for 30 minutes. A factory reset restores the default. |
| Changes are refused with "not supported" | The model does not have that feature. The tab says so. |
| "The switch is not answering" after a reboot | It takes about 30 seconds. Press *Try again*. |
| Locked out after a VLAN or IP change | Press the reset button of the switch. |
| Cannot log in to Switchyard | Without a configured password one is generated on first start and printed once in the log. Delete `secrets.json` in the data folder to generate a new one. |
| Too many failed logins | Wait a minute. |

## Questions

**Can I use LACP?** No. The firmware of these switches contains no LACP, only static trunks. Set the
device at the other end to a static trunk too (Linux: bonding mode `balance-xor`; other switches:
"static" or "on", not "LACP/active"). Link aggregation in Switchyard is static only.

**Can I change the speed of a port that is part of a LAG?** Only together with all members; the switch
refuses mixed settings and a member cannot be switched off.
