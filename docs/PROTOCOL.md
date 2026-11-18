# NSDP as Switchyard speaks it

NSDP is the UDP protocol the vendor's Plus Utility uses. It is not officially documented. This
page describes what `src/switchyard/nsdp/` implements, based on public community write-ups
(see [DESIGN.md](DESIGN.md#acknowledgements)) and a list of settings read from the utility's
language files. **None of it is verified on real hardware yet.**

## Transport

UDP. This side uses port 63321, switches listen on 63322. A search is a broadcast to
255.255.255.255:63322. Switches answer to the sender's port, by unicast or broadcast. Switchyard
opens one socket per network interface (bound to its address, so the broadcast leaves that
interface) plus a wildcard socket. Replies are matched by sequence number and switch MAC.

## Packet

32-byte header, big endian, then TLV records, then the end marker `ffff 0000`.

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | version, `01` |
| 1 | 1 | operation: 1 read request, 2 read response, 3 write request, 4 write response |
| 2 | 1 | result code, 0 = ok |
| 4 | 2 | tag that caused the failure |
| 8 | 6 | manager MAC |
| 14 | 6 | switch MAC, all zero = any |
| 22 | 2 | sequence number, echoed |
| 24 | 4 | `NSDP` |

A record is tag (2), length (2), value. A read request lists tags with length 0. Some firmware
writes length `ffff` for "empty"; Switchyard treats that as 0.

## Result codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 3 | tag not supported (with the tag in the header) |
| 5 | value rejected |
| 7, 13 | wrong password |
| 9 | wrong user name |
| 10 | managed through the web interface only |
| 14 | locked after 3 failed logins, 30 minutes |
| 15 | Plus Utility management disabled on this switch |

The meanings of 1, 2, 4, 6 and 8 follow the utility's error list.

## Passwords

Writes carry the credential as the first record. The switch announces how it wants it in tag
`0x0014`:

| Flags bit | Mode | Credential |
| --- | --- | --- |
| none or tag missing | plain | tag `0x000a` = password |
| 0 | xor | tag `0x000a` = password XOR the repeating key `NtgrSmartSwitchRock` |
| 3 | hash4 | tag `0x000a` = 4-byte value from password, switch MAC and the salt in tag `0x0017` |
| 4 | hash8 | tag `0x001a` = 8-byte value from the same inputs |

The hashes are fixed XOR patterns, not cryptography. A new password goes in tag `0x0009`;
Switchyard always uses the XOR encoding for it except in plain mode. The hash modes' password
change is unverified.

## Tags

Ports start at 1. A port bitmap has `ceil(ports/8)` bytes, port 1 is the most significant bit.

| Tag | Setting | Value |
| --- | --- | --- |
| `0001` | model | text |
| `0003` | name | text |
| `0004` | MAC | 6 bytes |
| `0006`/`0007`/`0008` | IP / netmask / gateway | 4 bytes |
| `000b` | DHCP | 0 static, 1 DHCP |
| `000d`/`000e` | firmware slot 1 / 2 | text |
| `0013` | reboot | write 1 |
| `0400` | factory reset | write 1 |
| `0c00` | port link | `[port][speed][flow]` |
| `1000` | port statistics | `[port]` + 6 x u64: received bytes, sent bytes, then model specific; CRC errors are the 6th (Gigabit models) or 3rd (XS models) |
| `1400` | clear statistics | write 1 |
| `1800` | start cable test | `[port][1]` |
| `1c00` | cable result | `[port][status u32][distance u32]` |
| `2000` | VLAN mode | 0 off, 1 port basic, 2 port advanced, 3 802.1Q basic, 4 802.1Q advanced |
| `2400` | port-based VLAN | `[id u16][members]` |
| `2800` | 802.1Q VLAN | `[id u16][members][tagged]` |
| `2c00` | delete VLAN | `[id u16]` |
| `3000` | PVID | `[port][vid u16]` |
| `3400` | QoS mode | 1 port based, 2 802.1p |
| `3800` | port priority | `[port][1 high .. 4 low]` |
| `4c00`/`5000` | ingress / egress rate | `[port][u32 rate code]` |
| `5400` | storm control | 0 off, 1 on |
| `5800` | storm control rate | `[port][u32 rate code]` |
| `5c00` | mirroring | `[destination][0][source members]`, destination 0 = off |
| `6000` | port count | 1 byte |
| `6400` | max VLANs | u16 |
| `6800` | IGMP snooping | `[enabled u16][vlan u16]` |
| `6c00`/`7000` | block unknown multicast / validate IGMPv3 | 0 or 1 |
| `7800` | serial number | 1 byte + text |
| `9000` | loop detection | 0 or 1 |

Codes: link 0 down, 1 10M half, 2 10M full, 3 100M half, 4 100M full, 5 1G, 6 10G. Rate code 0 no
limit, then 512 kbit/s doubling up to 11 = 512 Mbit/s. Cable status 0 ok, 1 no cable, 2 open, 3
short, 4 fiber, 5 shorted, 7 crosstalk.

## Behaviours Switchyard depends on

- **Unknown tags.** A switch may ignore them, answer with code 3, or not answer at all. Switchyard
  drops tags a switch reports as unsupported and, when a request gets no answer but the switch is
  alive, splits it tag by tag to find the offender.
- **Safe 802.1Q order.** A switch refuses to take a port out of the VLAN its PVID uses, and to set
  a PVID to a VLAN the port is not in. "Apply" therefore widens every VLAN first, then moves
  PVIDs, then narrows, then deletes unused VLANs.
- **Mode changes reset.** Writing the VLAN mode or QoS mode erases the related tables.
- **Reboot.** The switch stays silent for about half a minute.

## Not implemented

Firmware upgrade (TFTP after tag `0x0010`), link aggregation, per-port speed and flow control,
LED, power saving. See the issue list.
