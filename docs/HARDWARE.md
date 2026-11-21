# First run on a real switch

Switchyard works on the maintainer's GS108E (discovery, reading and changing settings). Other models
and the riskier functions below have only run against the simulator. Take it slowly and keep a way back.

## Before you start

- Find the reset button of the switch. Holding it for about 10 seconds restores factory settings
  (IP 192.168.0.239, password `password`).
- Connect the computer running Switchyard to the switch with a cable, ideally with nothing else
  between them. Same layer-2 network, no router in between.
- Know what is connected to the switch. A wrong VLAN or IP setting can cut you off from it.

## Steps

1. Start Switchyard and open *Protocol log* in a second tab.
2. *Switches*, *Search network*. The switch should appear with model and firmware. If not, see
   [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
3. Add it and open it. *Overview* shows the ports; plug a cable in and watch a port change.
4. Read-only tabs first: *Ports* statistics, *VLAN*, *QoS*. Compare with the switch's own web
   interface, if it has one.
5. Make one harmless change, for example the switch name under *Network*, and enter the password
   when asked.
6. Download a backup under *Maintenance* before you try VLANs.
7. Try VLAN or QoS changes on ports that carry nothing important.

## Help support more models

*Maintenance*, *Diagnostics*, *Scan settings* reads every known setting once and shows which ones
your switch answers, with the raw bytes. *empty (not supported)* means the switch answered without a value, which is how some firmware says it does not have that setting. It changes nothing. Press *Copy for bug report* and paste
the result into a *Hardware report* issue. Settings such as port LED, power saving, loop prevention
and port names exist on some models; their value layouts are not verified, so Switchyard does not
write them yet and needs these reports first.

## Riskier functions

Try these last, on a switch you can afford to lose, with stable power:

- **Firmware upgrade.** Use the official image for the exact model and hardware revision, make a
  backup first, never power off during the upload or the restart (several minutes). If the switch
  does not come back, wait 10 minutes, then power-cycle once. The recovery path depends on the model.
- **Port speed and flow control.** A fixed speed needs the same setting at the other end. Switching
  off the port you are connected through ends your session.
- **Link aggregation.** Static trunks need the other end configured as a static trunk too, or the
  network loops or drops traffic.

## Reporting

Open a *Hardware report* issue. For a bug, copy the log with *Copy for bug report* on the
*Protocol log* page. Passwords are blanked. Remove MAC addresses and IPs you want to keep private.

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
| GS108E | v1 | 1.00.09 | yes | yes | yes | maintainer, 2026-11: discovery, status, statistics, VLANs, QoS, name and IP work. No loop detection, link aggregation or port speed settings on this model (the settings scan gets empty answers for all three). Answers a read with an unknown setting by an empty reply (handled since 0.1). Firmware upgrade not tried. |
