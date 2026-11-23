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

## TP-Link Easy Smart

TP-Link Easy Smart switches (TL-SG105E, TL-SG108E, TL-SG108PE, TL-SG1016DE, TL-SG1024DE and
relatives) speak a different protocol, written up in [PROTOCOL-EASYSMART.md](PROTOCOL-EASYSMART.md).
Switchyard supports both of its generations: older firmware with a token per change, and firmware
from 2023 on with a session key. So far this has been tested against the built-in simulator only.

- The switches answer by broadcast, so Switchyard has to run in the same network segment, with
  host networking in Docker. *Add by IP address* does not work for them.
- UDP 29808 and 29809 must be open between Switchyard and the switches. The vendor utility on
  another computer can run at the same time.
- Changes need the user name and password of the switch (factory setting `admin` / `admin`).
  Switchyard saves every change to the switch's flash, as the vendor utility does.
- Not yet: PoE power readings and per-port PoE on/off, the cable test, firmware upgrades, resetting the statistics. These switches
  count packets, not bytes, so the statistics and charts show packets per second.

### Test plan

For a first test on a real switch, for example a TL-SG108PE. Work through it in this order. Every
step that changes something is undone right after. Use ports that have no cable plugged in, and
keep the cable to the computer running Switchyard where it is.

**Before you start**

1. Write down the hardware version and firmware of the switch (label on the bottom, or the
   *System Info* page of the switch's own web interface).
2. Keep a way back: the switch's web page or the Easy Smart Configuration Utility, and the reset
   button (hold it pressed for several seconds, see the switch manual; afterwards user `admin`,
   password `admin`).
3. Run the latest Switchyard image with host networking.

**Reading (changes nothing)**

4. *Search network*. Does the switch show up as "TP-Link TL-SG108PE"? Add it.
5. Open it and go through *Overview*, *Ports*, *VLAN*, *QoS*, *Mirroring & multicast*,
   *Speed & flow* and *Link aggregation*. Do the values match the switch's own web page?
6. *Maintenance*, *Diagnostics*, *Scan settings*, then *Copy for bug report*. If PoE devices are
   connected, do this once with a powered device on a PoE port and once without: the raw values
   are what is needed to add PoE.

**Changing (each one undone right away)**

7. *Maintenance*, *Download backup*.
8. *Mirroring & multicast*: switch loop prevention on, apply, off again. Then IGMP snooping on and
   off. The first change asks for user name and password.
9. *Speed & flow*: set an unused port to *100M full*, apply, check the switch's web page, then back
   to *auto*. Then switch the unused port off and on.
10. *QoS*: set an ingress limit on an unused port, apply, remove it again.
11. *Mirroring*: mirror one unused port to another, apply, then switch mirroring off.
12. *Link aggregation*: put two unused ports of the same half (3 and 4, or 7 and 8) into a group,
    apply, then remove the group.
13. *VLAN*, only if the switch has no VLAN configuration you need (switching the mode resets the
    table): switch to 802.1Q, add VLAN 99 with an unused port tagged, apply, then delete VLAN 99.
14. *Network*: change the switch name, apply, change it back. Leave the IP settings alone.
15. *Maintenance*, *Reboot*. Does the switch come back, and are the settings you left (the name)
    still there?

**What to send back**

- Model, hardware version and firmware.
- For each step: worked, or the error message.
- The *Copy for bug report* text of step 6.
- For a step that failed: *Protocol log*, *Copy for bug report* (passwords are blanked in it).

## Compatibility list

Filled from reports.

| Model | Hardware | Firmware | Discovery | Read | Write | Reporter notes |
| --- | --- | --- | --- | --- | --- | --- |
| TL-SG108PE | 5.0 | 1.0.0 Build 20230218 | yes | yes | yes | tester, 2026-11: all pages read correctly; name, VLANs (802.1Q add/delete, mode), QoS rate limit, mirroring, IGMP, loop prevention, LAG, port speed and on/off changed and confirmed on the switch's own page; settings kept after a reboot. Needed a token besides the session key (#79); slow answers to some changes (#83). TP-Link ships several builds as 1.0.0 (the download page lists Build 20220930): report the full build string. Not yet tried: backup and restore, password change, PoE. |
| GS108E | v1 | 1.00.09 | yes | yes | yes | maintainer, 2026-11: discovery, status, statistics, VLANs, QoS, name and IP work. No loop detection, link aggregation or port speed settings on this model (the settings scan gets empty answers for all three). Answers a read with an unknown setting by an empty reply (handled since 0.1). Firmware upgrade not tried. |
