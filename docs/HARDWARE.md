# First run on a real switch

Switchyard has so far only run against a simulator. Take it slowly and keep a way back.

## Before you start

- Find the reset button of the switch. Holding it for about 10 seconds restores factory settings
  (IP 192.168.0.239, password `password`).
- Connect the computer running Switchyard to the switch with a cable, ideally with nothing else
  between them. Same layer-2 network, no router in between.
- Know what is connected to the switch. A wrong VLAN or IP setting can cut you off from it.

## Steps

1. Start Switchyard and open *Activity* in a second tab.
2. *Switches*, *Search network*. The switch should appear with model and firmware. If not, see
   [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
3. Add it and open it. *Overview* shows the ports; plug a cable in and watch a port change.
4. Read-only tabs first: *Ports* statistics, *VLAN*, *QoS*. Compare with the switch's own web
   interface, if it has one.
5. Make one harmless change, for example the switch name under *Network*, and enter the password
   when asked.
6. Download a backup under *Maintenance* before you try VLANs.
7. Try VLAN or QoS changes on ports that carry nothing important.

## Reporting

Open a *Hardware report* issue. For a bug, copy the log with *Copy for bug report* on the
*Activity* page. Passwords are blanked. Remove MAC addresses and IPs you want to keep private.

## Known caveats

| Topic | Detail |
| --- | --- |
| Newer firmware | Some versions answer discovery but refuse management ("Plus Utility management is disabled", result 15) or remove NSDP entirely. Turn it on in the switch web interface if it has the option. |
| GS108E v1/v2, GS105E v1 | No web interface; NSDP is the only way. |
| Counters | Which counter is "CRC errors" differs between model families. |
| Locked out | Three wrong passwords lock the switch for 30 minutes. |

## Compatibility list

Filled from reports.

| Model | Hardware | Firmware | Discovery | Read | Write | Reporter notes |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |
