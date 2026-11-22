# TP-Link Easy Smart protocol

Notes behind `src/switchyard/easysmart/`. Collected from the public projects listed below; facts
only, no code copied. What Switchyard does with each open question is noted in the code; the
hardware test plan in [HARDWARE.md](HARDWARE.md#tp-link-easy-smart) checks them on a real switch.

Scope: the UDP protocol of the "Easy Smart Configuration Utility" (TL-SG105E, TL-SG108E, TL-SG108PE,
TL-SG1016DE, TL-SG1024DE and relatives). Facts only, each with its source tag. 

Conventions: integers are big-endian unless noted. "mask" = u32 port bitmap, bit (n-1) = port n
(port 1 = 0x00000001). Ports are 1-based. "NUL string" = ASCII bytes followed by one 0x00.
Markers: **[1 src]** = only one source states it; **[DISAGREE]** = sources disagree;
**[INFER]** = inferred from the sources, not stated by any; **[UNVERIFIED]** = no source has
exercised it on hardware.

## 0. Sources

| Tag | Repo (license) | Files read | Basis |
|---|---|---|---|
| ESCP | davidlukac/escp-go (MIT) | PROTOCOL.md, internal/wire/{crypto,packet,tlv}.go, session.go, seq.go, transport.go, request.go, config.go, info.go, discover.go, lag.go, errors.go, model/*.go, escptest/*, testdata/*.json/txt, safety/compatibility.json | live on TL-SG108E v6, fw 1.0.0 Build 20230218 and 20250710, plus the vendor utility (decompiled) |
| TFD | davidlukac/terraform-provider-tplinkeasysmart (MIT) | go.mod, internal/provider/client.go | uses ESCP; no protocol code of its own |
| DBO | DarthBo/tplink-easysmart (no license: facts only) | PROTOCOL.md, src/proto/{client,packet}.rs | live on **TL-SG108PE**, fw 1.0.0 Build 20201030, plus vendor utility |
| ESSLIB | firstof9/tplink-ess-lib (no license file: facts only) | tplink_ess_lib/{protocol,network,__init__,binary}.py, tests/common.py | library used by lyricnz/tplink_ess; tests/common.py holds **real captured replies** from a TL-SG105E 3.0 (fw 1.0.0 Build 20160715) and a TL-SG108PE 1.0 (fw 1.0.2 Build 20160526), decrypted for these notes |
| LYR | lyricnz/tplink_ess (MIT) | api.py, manifest.json | thin wrapper over ESSLIB |
| SMRT | rgl/smrt (no license: facts only), philippechataignon/smrt, gtalusan/smrt | README.md, protocol.py, network.py, smrt.py | TL-SG105E/108E v1 and v4, fw 2014-2018 |
| ANS | rgl/ansible-collection-tp-link-easy-smart-switch (GPL-3.0: facts only) | plugins/module_utils/{protocol,network,switch,switch_client,switch_take_ownership_client}.py, README | old firmware |
| SLK | juvenal/smrtlink (GPL-2.0: facts only) | src/lookup/*.lst, src/Program.cpp, src/Packet.h, src/Types.h | 2015, old firmware |
| RUS | russor/easysmart (BSD-2) | easysmart.escript | stats read |
| JSI | janisstreib/tp-link-intercept (no license: facts only) | README.md, src/Intercept.java, src/TPTools.java | old firmware capture |
| VMK | vmakeev/hass_tplink_easy_smart (MIT) | client/{classes,const,tplink_api}.py | **web UI only** (HTTP): used for value meanings, PoE |
| EBZ | ebenezer-isaac/tplink-easysmart-mcp (MIT) | docs/specs/switch-protocol.md | web UI only, TL-SG1016PE; PoE semantics (derived from VMK) |
| USER | [Wireshark dissector gist](https://gist.github.com/foxt/a48753d23d4c52a77aa95c87f15eb54c) | tpl_easy_smart.lua | tag names only |

Checked and **not usable for UDP** (HTTP web UI only): Aohzan/hass-tplink-easysmartswitch (Apache-2.0,
`logon.cgi` + HTML scraping), lucavb/terraform-provider-tplink-easysmart (MIT, `internal/client/webui`),
donid/TplinkEasySmartSwitch (MIT, `logon.cgi`, `qos_bandwidth_set.cgi`), Clete2/check_tplink (MIT, regex
over the port statistics page), ozonejunkieau/tplink-easysmartswitch (BSD-3, `logon.cgi`, `qvlanSet.cgi`),
marcinooo/switch_TL_SG108PE (MIT, web). psmode/essstat (GPL) was not read (web UI statistics).

**No source found documents the UDP layout of any PoE TLV (13568-14592), cable test (16896),
config write (780), firmware upgrade (1536) or 1016DE/1024DE specifics.** See section 12.

## 1. Two protocol generations (important for the TL-SG108PE)

| | Gen A: token (old) | Gen B: RSA session (new) |
|---|---|---|
| Seen on | TL-SG105E 1.0-3.0, TL-SG108E 1.0-4.0 (SMRT, ESSLIB, ANS, SLK, JSI); **TL-SG108PE fw 1.0.0 Build 20201030** (DBO) | TL-SG108E v6 fw 20230218, 20250710 (ESCP) |
| Crypto | static RC4 for everything | static RC4 for discovery and the key exchange, then an 8-state RC4 keyed per session |
| Write auth | GET 2305 -> token in reply header -> SET (op 3) carrying token + 512/514 | key exchange TLV 528, then every SET carries 512/514; no token (ESCP) |
| TLV 528 | ignored, **no reply** (DBO) | answered op 4, error code 4098 |

ESCP: "The firmware released in 2023 added the RSA key exchange". ESCP never tried Gen A; DBO never saw
Gen B. **[INFER]** A client can detect the generation by sending the 528 key exchange: reply with code
4098 = Gen B; silence = Gen A. Whether Gen B firmware still answers GET 2305 is unknown.

**Observed on hardware (Switchyard #79):** a TL-SG108PE 5.0 with firmware 1.0.0 Build 20230218
accepts the session key exchange (generation B), answers reads with token 0, but also answers
GET 2305, and silently drops a write that carries user name and password without a token.
Switchyard therefore asks for a token before every write on generation B too, once with a short
wait, and remembers whether the switch hands them out.

## 2. Transport

| Fact | Source |
|---|---|
| UDP. Switch listens on **29808**; replies go to UDP **29809**, whatever the sender's port. Client should bind 29809. | all |
| Requests: limited broadcast 255.255.255.255:29808 (SMRT, ESSLIB, DBO, JSI) or unicast to the switch IP (ESCP, RUS). DBO: a switch on a foreign subnet (factory 192.168.0.1) drops subnet-directed broadcasts, so 255.255.255.255 is the only way to reach it. | ESCP, DBO, RUS |
| Replies are sent from source port 29808 to **255.255.255.255:29809** (every host on the segment sees them) - captured for discovery, key exchange and read replies; write replies not captured. | ESCP **[1 src: captured]**, DBO (states it) |
| Same L2 segment only. | ESCP |
| Linux: the receiving socket must bind the wildcard address (0.0.0.0 or 255.255.255.255), not the interface IP, or the broadcast replies are not delivered; pin egress with SO_BINDTODEVICE. | DBO |
| Several local clients can share port 29809 with SO_REUSEPORT / SO_REUSEADDR; each socket gets every broadcast reply. | ESCP, ESSLIB |
| Max datagram seen: ~1440 B (1400-byte TLV in config download). ESSLIB receives with a 1500-byte buffer, DBO 8192. Use >= 2048. | ESCP, ESSLIB, DBO |
| Factory default IP 192.168.0.1 when no DHCP server answers. | ANS README **[1 src]** |

## 3. Packet format

### 3.1 Header (32 bytes; struct `!BB6s6sHiHHHHi`)

| Off | Size | Field | Notes | Source |
|---|---|---|---|---|
| 0 | 1 | version | always 1 | all |
| 1 | 1 | opcode | table 3.2 | all |
| 2 | 6 | switch MAC | 00..00 in a discovery request ("every switch"); otherwise the target; replies always carry the switch's own MAC (ESCP captured 30+ replies of every kind) | ESCP, DBO |
| 8 | 6 | host MAC | sender's MAC; echoed in the reply. RUS uses a made-up MAC (02:0b:ad:0b:ad:00) and still gets answers; ESSLIB uses a configurable fake (de:ad:be:ef:fe:ed). | ESSLIB, RUS, LYR |
| 14 | 2 | sequence | echoed in the reply | all |
| 16 | 4 | error code | signed i32; 0 in requests; section 10 | all |
| 20 | 2 | total length | header + TLVs + end marker | all |
| 22 | 2 | fragment offset / "more fragments" | ESCP: non-zero (=1) on every packet of a multi-packet reply except the last; no index | ESCP **[1 src]** |
| 24 | 2 | flag | always 0 observed | ESCP, ESSLIB captures |
| 26 | 2 | token id | Gen A: issued by the switch, section 5.1. Gen B: 0 in all replies (ESCP measured on config download) | DBO, SMRT, ESSLIB, ESCP |
| 28 | 4 | checksum | always 0 ("apparently not implemented") | JSI, DBO, ESCP |

Note: JSI's TPTools writes fragment offset as a 4-byte zero followed by token, which is the same bytes
(fragment=0, flag=0).

### 3.2 Opcodes

| Op | Direction | Meaning | Source |
|---|---|---|---|
| 0 | host->switch | discovery | all |
| 1 | host->switch | GET (read) | all |
| 2 | switch->host | reply to GET or discovery (ESSLIB/SMRT call it "SET") | all |
| 3 | host->switch | SET / "LOGIN": all writes, the login, the key exchange, config download | all |
| 4 | switch->host | reply to op 3 ("RETURN", "CONFIRM") | all |
| 5 | ? | named "READ5" in ESSLIB/SMRT forks; never seen in a capture. ESCP accepts a received header only if op <= 4. | ESSLIB **[1 src]** |

### 3.3 Payload

- Sequence of TLVs: u16 type, u16 length, value (length bytes). Terminated by `ff ff 00 00` (type 0xFFFF,
  length 0). A request with no TLVs is 36 bytes. (all)
- A GET carries the wanted type(s) with **length 0**. (all)
- A reply may carry several TLVs of the same type (one per port / VLAN / group), plus related types
  (section 7). (ESCP, ESSLIB captures)
- Strings are NUL-terminated in both directions (ESCP, DBO, SMRT, ESSLIB captures). SLK sends 512/514
  **without** the NUL **[DISAGREE, 1 src]**; send the NUL.
- Robust receive: a buffer is a packet if len >= 36, byte0 = 1, op <= 4 and length field = bytes received. (ESCP)

### 3.4 Worked example (real capture, ESSLIB tests/common.py "num_ports", TL-SG105E 3.0)

```
cipher : 5d761a4b2a37d1d8e7700b2685317e00422ba2f5d786aeed508f0019c202909a5b1981c722d5d43649
plain  : 01 02 704f5789616a 1c1b0de591a4 0095 00000000 0029 0000 0000 4624 00000000
         000a 0001 05  ffff0000
         -> op 2, seq 149, len 41, token 17956, TLV 10 = 5 ports
static RC4 keystream, first 16 bytes: 5c746a047dbeb0b2fb6b06c314957e95
```

## 4. Encryption

### 4.1 Static RC4 (both generations)

- Standard RC4 over the **whole datagram** (header included); the cipher state is **re-initialised for
  every packet** (no stream carried between packets). Encrypt = decrypt. (all)
- Key: the 256-character ASCII string below, standard KSA (ESCP derives it by TEA-decrypting a blob
  from the vendor jar; RUS and JSI give it verbatim; checked: KSA(key) = the state below and
  that it equals the base64 state in ESSLIB).

```
Ei2HNryt8ysSdRRI54XNQHBEbOIRqNjQgYxsTmuW3srSVRVFyLh8mwvhBLPFQph3ecDMLnDtjDUdrUwt7oTsJuYl72hXESNiD6jFIQCtQN1unsmn3JXjeYwGJ55pqTkVyN2OOm3vekF6G1LM4t3kiiG4lGwbxG4CG1s5Sli7gcINFBOLXQnPpsQNWDmPbOm74mE7eyR3L7tk8tUhI17FLKm11hrrd1ck74bMw3VYSK3X5RrDgXelewMU6o1tJ3iX
```

- Post-KSA state S[0..255] (start PRGA from this with i = j = 0):

```
    191, 155, 227, 202,  99, 162,  79, 104,  49,  18, 190, 164,  30,  76, 189, 131,
     23,  52,  86, 106, 207, 125, 126, 169, 196,  28, 172,  58, 188, 132, 160,   3,
     36, 120, 144, 168,  12, 231, 116,  44,  41,  97, 108, 213,  42, 198,  32, 148,
    218, 107, 247, 112, 204,  14,  66,  68,  91, 224, 206, 235,  33, 130, 203, 178,
      1, 134, 199,  78, 249, 123,   7, 145,  73, 208, 209, 100,  74, 115,  72, 118,
      8,  22, 243, 147,  64,  96,   5,  87,  60, 113, 233, 152,  31, 219, 143, 174,
    232, 153, 245, 158, 254,  70, 170,  75,  77, 215, 211,  59,  71, 133, 214, 157,
    151,   6,  46,  81,  94, 136, 166, 210,   4,  43, 241,  29, 223, 176,  67,  63,
    186, 137, 129,  40, 248, 255,  55,  15,  62, 183, 222, 105, 236, 197, 127,  54,
    179, 194, 229, 185,  37,  90, 237, 184,  25, 156, 173,  26, 187, 220,   2, 225,
      0, 240,  50, 251, 212, 253, 167,  17, 193, 205, 177,  21, 181, 246,  82, 226,
     38, 101, 163, 182, 242,  92,  20,  11,  95,  13, 230,  16, 121, 124, 109, 195,
    117,  39,  98, 239,  84,  56, 139, 161,  47, 201,  51, 135, 250,  10,  19, 150,
     45, 111,  27,  24, 142,  80,  85,  83, 234, 138, 216,  57,  93,  65, 154, 141,
    122,  34, 140, 128, 238,  88,  89,   9, 146, 171, 149,  53, 102,  61, 114,  69,
    217, 175, 103, 228,  35, 180, 252, 200, 192, 165, 159, 221, 244, 110, 119,  48
```

### 4.2 Gen B session cipher and key exchange (ESCP only **[1 src]**, verified live)

- Session key: 8 random characters from `A-Za-z0-9`, new per client run.
- Session cipher: RC4 with a **state size of 8** (S = 0..7): KSA `j = (j + S[i] + key[i mod 8]) mod 8`
  (key bytes taken as signed in the jar; irrelevant for ASCII), PRGA with all arithmetic mod 8, output
  byte = S[(S[i]+S[j]) mod 8] (so only the low 3 bits of each byte change). Fresh state per packet.
- Key exchange request: op 3, **static** RC4, single TLV **528**, 10 bytes:
  `00`, u8 word count (4, or fewer if the top words are 0), then the ciphertext as four **little-endian
  u16 words, least significant word first**.
  Ciphertext = textbook RSA (no padding): m = the 8 key bytes read as a little-endian integer,
  e = 65537, N = 0xB3FAB9D6646D4EBF (64-bit; little-endian bytes bf 4e 6d 64 d6 b9 fa b3) for the
  TL-SG108E v6. The vendor utility also carries a 1024-bit modulus "for some other models" (value not
  published by ESCP; DBO: modulus "64-bit or 1024-bit ... selected per-MAC").
- Reply: op 4, **error code 4098 = key accepted**; this reply is already encrypted with the session cipher.
- After that, every packet the client sends is session-encrypted. Receive: try static RC4; if the result
  passes the header check (3.3) accept it, else decrypt with the session cipher. Discovery stays static.
- Session loss (switch reboot, timeout) shows as silence; ESCP then redoes the key exchange once and
  retries (idempotent requests only).

## 5. Session flows

### 5.1 Gen A (token)

| Step | Packet | Source |
|---|---|---|
| 1 | (optional) discovery, op 0 | all |
| 2 | GET op 1 with empty TLV **2305**. Reply op 2, no TLVs, err 0, **token id in the header** (captured: tok 4323). | ESSLIB capture, SMRT, ANS, DBO, SLK ("PING ... sent before SET") |
| 3 | SET op 3: header token = that token; TLVs 512 user, 514 password (NUL strings) first, then the settings. Reply op 4, err 0 or error. | DBO, SMRT, ANS, ESSLIB, SLK |

- DBO (108PE fw 20201030): the token is **single-use**; a SET with token 0 or a spent token is dropped
  **silently** (no reply). Fetch a fresh token immediately before **every** SET, including the 2304 save;
  the vendor utility does `GET 2305` before each SET. **[1 src]**
- SMRT/ESSLIB/ANS copy the token of the **last reply** into every following request, and do
  `GET 2305` + op 3 with only 512/514 ("login") before reads; the captured login reply (op 4) carries the
  same token as the 2305 reply. Do the DBO pattern (fresh 2305 before each SET).
- RUS sends a GET with token = its own random sequence number and gets stats back: **reads need no
  token or login** on Gen A. DBO: "Reads of ordinary fields work without credentials, from any subnet";
  a GET of 512 is silently discarded.
- Every captured Gen A reply (incl. discovery) carries a non-zero token, different per session.

### 5.2 Gen B (ESCP)

1. Discovery (to learn MAC/IP), op 0.
2. Key exchange (4.2).
3. GETs (op 1) - no credentials needed (LED GET worked straight after the key exchange; without the key
   exchange not tested). "All GETs below need the session-key exchange first."
4. Every SET (op 3) = TLV 512 + TLV 514 + the settings. Login alone = op 3 with 512+514, error 0 = ok.

### 5.3 Sequence and matching rules

| Rule | Source |
|---|---|
| Reply echoes the request's sequence number; drop other replies. | all |
| Sequence: random start, +1 per request, wrap mod 1000 (SMRT, ESSLIB, SLK). ESCP: requests 0-999, discoveries 1000-1999 so a late discovery reply is never taken for a request reply. | ESCP, SMRT, ESSLIB |
| Also match host MAC (ESSLIB) and switch MAC (DBO, ESCP: a reply with an all-zero or wrong switch MAC is from another device). | ESSLIB, DBO, ESCP |
| Because all replies are broadcast to 29809, drain the socket before sending (ESCP: otherwise a stale discovery reply was read as a LED/mirror/LAG answer). | ESCP |
| Never retry a request with the same sequence number (ESCP advances it on retry). | ESCP |

### 5.4 Timing / retries

| Value | Source |
|---|---|
| Reply timeout 3 s, 2 retries (default), max 30 s / 10 retries. | ESCP |
| Discovery window 2 s, probe re-sent halfway; switches answer within milliseconds. | ESCP |
| Discovery: one probe per interface, to that interface's subnet broadcast, host MAC = that interface's MAC. | ESCP, JSI |
| ESSLIB: socket timeout 2 s / 10 s total; SLK: 180-250 ms; DBO: 100 ms read loop within a window. | ESSLIB, SLK, DBO |
| After an IP change the switch moves in ~2 s; after enabling DHCP it is silent ~2 s and answers at the lease ~4 s after the write. Poll discovery every 0.5 s (up to 20 s). | ESCP |
| UDP loss is real: 2 of 228 config downloads lost one packet. | ESCP |

## 6. Discovery

- Request: op 0, switch MAC 0, host MAC = interface MAC, **no TLVs** (36 bytes). (ESCP, ESSLIB, JSI)
- Reply op 2, TLVs in this order (ESSLIB captures, SMRT README): 1, 2, 3, 7, 8, 9, 4, 5, 6, and on some
  units 13 (SMRT README: 108E v4 fw 20181120 adds `13 = 01`; 108E v1 does not) and 14 (ESCP).

| Type | Value | Example |
|---|---|---|
| 1 | model, NUL string | `TL-SG108PE`, `TL-SG105E` |
| 2 | description / hostname, NUL string (108PE default = model name) | `switch7` |
| 3 | MAC, 6 bytes | |
| 7 | firmware, NUL string | `1.0.2 Build 20160526 Rel.34684` |
| 8 | hardware, NUL string | `TL-SG108PE 1.0`, `TL-SG105E 3.0` |
| 9 | DHCP, u8 0/1 | |
| 4, 5, 6 | IP, netmask, gateway, 4 bytes each | |
| 13 | auto-save, u8 (1 = on) | ESCP, SMRT ("v4"), ESSLIB, DBO |
| 14 | factory-default flag, u8 (ESCP: `00` or `01` depending on unit) | ESCP, DBO |

A GET (op 1) of type 2 returns the same full set (ESSLIB capture "hostname"); a GET of 9 returns 9, 4, 5, 6
(ANS comment). Both Gen A.

## 7. TLV catalogue

Dir: R = GET, W = in a SET. "Rows" = one TLV per port/VLAN/group in the reply. Gen B: **one type per GET**
(a GET naming several types answers error 1, ESCP); Gen A tools also only ever ask for one type.

### 7.1 System and management

| Type | Name | Dir | Value layout | Notes / source |
|---|---|---|---|---|
| 1-9 | see section 6 | R | | |
| 2, 9, 4, 5, 6 | description, DHCP, IP, mask, gateway | W | NUL string; u8; 4 B; 4 B; 4 B | Gen B: **all five together** or error 3 (tried: without 9, without 6; 2 alone not tried) (ESCP). DBO sends 512, 514, 2, 9, 4, 5, 6 in that order, with IP/mask/gw = 0.0.0.0 when DHCP = 1. ANS (Gen A) sends 9, 4, 5, 6 without 2. Description: `^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$`, max 32 (DBO; ESCP web UI limit 32, letters/digits/hyphen). |
| 9 = 1 | enable DHCP | W | | Switch asks for a lease, static address ignored while DHCP is on; DHCP 0 + static triple restores static at once; static triple is not remembered, resend it. (ESCP) |
| 10 | port count | R | u8 | 5 on 105E (capture), 8 on 108E |
| 11 | management VLAN? (USER: "MAC VLAN?") | R | ? | answers error -2 on 108E v6 (ESCP) |
| 12 | LED | R/W | u8 1 = on | ESCP verified; ESSLIB name led_status |
| 13 | auto-save | R | u8 1 = on | see 8.3 |
| 14 | factory flag | R | u8 | discovery only; a GET of 14, 15, 16 gets **no reply** on 108E v6 (ESCP) |
| 15 | flash type | R | ? | ESSLIB "hex"; no layout |
| 16 | port order | - | ? | USER only |
| 512 | username | W | NUL string, max 16 | all |
| 513 | **new username** | W | NUL string | ANS, SLK **[DISAGREE with USER: "new password"]** |
| 514 | password (current) | W | NUL string, max 16 | all |
| 515 | **new password** | W | NUL string | ANS, SLK |
| 528 | RSA session key | W | 10 B, 4.2 | ESCP, DBO |
| 768 | config file read | W(empty) | reply: raw file in 768 values over several packets | 8.4 |
| 773 | reboot | W | u8: 1 = save config first ("permanent"), 0 = no | SLK; ESSLIB "bool reboot"; USER "save config/reboot" **[UNVERIFIED]** |
| 780 | config file write | W | ? | USER only |
| 1280 | factory reset | W | empty | SLK (sent with 512/514) **[UNVERIFIED]** |
| 1536 | firmware upgrade | W | ? | SLK, USER names only |
| 2304 | save config to flash | W | empty | DBO, SMRT, SLK |
| 2305 | token request | R | empty; token in reply header | Gen A, 5.1 |

### 7.2 Ports

**4096 port settings** (rows, 7 B). Read: u8 port, u8 state (1 = enabled), u8 LAG id (0 = none),
u8 configured speed, u8 actual speed, u8 configured flow control, u8 actual flow control.
Write: same 7 bytes with LAG and actual bytes 0 (ESCP verified) - ANS sends the LAG byte as read
**[DISAGREE, minor]**. One TLV per port; several in one SET (ANS). Sources: ESCP, ANS, ESSLIB capture.

Speed codes (ESCP, ANS, VMK, Clete2, ESSLIB):

| Code | Meaning |
|---|---|
| 0 | link down (actual only) |
| 1 | auto |
| 2 | 10M half |
| 3 | 10M full |
| 4 | 100M half |
| 5 | 100M full |
| 6 | 1000M full |

Rules (ESCP, 108E v6): flow control is kept only with speed auto or 1000M-full (write accepted, read back
off otherwise; vendor utility message "Failed to enable flow control when speed in Force mode").
A LAG member refuses half duplex with 7407.

**16384 port statistics** (rows, 19 B, R): u8 port, u8 state (1 = enabled), u8 link (speed code above),
u32 tx good, u32 tx bad, u32 rx good, u32 rx bad (packets). (ESCP, ESSLIB capture, SMRT, RUS)
No reset/clear TLV is known for UDP (the web UI uses `port_statistics_set.cgi?clear=Clear`, donid).
Counter width: 32 bit (wraps).

### 7.3 LAG / trunk (4608)

Rows, 5 B: u8 group id, mask members; unused group = mask 0. Write: one TLV (group, mask); mask 0 deletes.
(ESCP verified 108E v6; ESSLIB capture; SLK "byte[5]")

- 108E: 2 groups; group 1 only ports 1-4, group 2 only ports 5-8; 2-4 members (ESCP; web UI
  `PortTrunkRpm.htm` same).
- 105E 3.0 capture returns **one** row (`01 00000000`) -> 1 group on 105E **[1 src, INFER]**.
- Refusal codes: section 10.

### 7.4 Mirroring (16640)

10 B, R/W: u8 enabled, u8 destination port, mask ingress (mirrored rx), mask egress (mirrored tx);
all zero = disabled. Disabled entry carrying ports is accepted but read back as zeros. (ESCP: disabled
read and write verified; enabled layout from vendor code and a live write mirror 4 -> 7.)

### 7.5 QoS

| Type | Dir | Layout | Source |
|---|---|---|---|
| 12288 | R/W | u8 mode: 0 port-based, 1 802.1p, 2 DSCP/802.1p | ESCP verified |
| 12289 | R/W | rows 2 B: u8 port, u8 priority raw 0..3 (UI 1 = lowest .. 4 = highest = raw+1). Write one TLV for **every** port (vendor utility does). Ignored and not kept unless mode = 0. | ESCP verified |
| 12544 / 12545 | R/W | rows 6 B: u8 port, u8 enable (read: "reserved" = this flag), u32 rate **kbps**, 0 = unlimited. Ingress / egress requested separately; write one TLV per SET, ingress first. UI: multiple of 64, max 1000000. | ESCP verified (108E v6) |
| 12800 | R/W | storm control rows 9 B: u8 port, u8 enable (1 if any type), u8 unknown-unicast, u8 multicast, u8 broadcast, u32 rate kbps (multiple of 64, >= 64 when a type is set; 0 + no types = off) | ESCP verified; SLK name. Not in USER list. |

One limiter per port: an ingress rate != 0 turns storm control off; storm control with a type removes
the ingress limit (no error). (ESCP, both builds)
Old firmware rate encoding (kbps vs rate codes): unknown for UDP **[UNVERIFIED]**.

### 7.6 IGMP snooping, loop prevention

| Type | Dir | Layout | Source |
|---|---|---|---|
| 4352 | R/W | u8 snooping enabled; GET 4352 also returns 4354 | ESCP (same-value write verified) |
| 4354 | R/W | u8 report message suppression; write together with 4352; kept even while snooping is off | ESCP |
| 4353 | R | learned groups, 10 B each: IPv4 (4), u16 VLAN, mask ports; request separately (combined GET -> error 1); possibly several entries concatenated in one TLV | ESCP |
| 17152 | R/W | u8 loop prevention 1 = on | ESCP verified, JSI, SMRT |

### 7.7 VLAN

The three modes MTU / port-based / 802.1Q exclude each other; enabling one discards the other mode's
table and **resets the table of the mode switched to** (ESCP measured). "Off" is done by enabling
another mode (ESCP never writes 0 to 8448; ANS writes 8704 = 0 on Gen A).

| Type | Dir | Layout | Notes / source |
|---|---|---|---|
| 8192 | R/W | 2 B: u8 enabled, u8 uplink port (1 when disabled) | ESCP (write not exercised), SLK, ESSLIB capture `0001` |
| 8448 | R/W | u8 port-based mode; GET 8448 returns 8448, 8449 rows, 8450 | ESCP verified |
| 8449 | R/W | rows 5 B: u8 group id, mask members; write one TLV, mask 0 removes; membership exclusive (ports move out of VLAN 1 and back) | ESCP verified |
| 8450 | R | u8 max groups (8 on 108E); group 9 -> error 7503 | ESCP |
| 8704 | R/W | u8 802.1Q mode 1/0. Enable -> VLAN 1 "Default", all untagged, all PVID 1 | ESCP verified, ANS |
| 8705 | R/W | rows: u16 VID, mask members (tagged+untagged), mask tagged, NUL string name. Untagged = member & ~tagged. **Delete: VID, 0, 0, empty name** (ESCP verified, SMRT `--delete`). Write one VLAN per SET (ANS: several at once garbles names on old fw). VID 1-4094; name <= 10 chars `[A-Za-z0-9_-]` (ESCP, vendor utility). Only kept while 8704 = 1 (accepted silently otherwise). | ESCP, ANS, SMRT, ESSLIB capture |
| 8706 | R/W | rows 3 B: u8 port, u16 PVID; several in one SET; VLAN must exist | ESCP verified, ANS, SMRT |
| 8707 | R | u8 max 802.1Q VLANs = 32 (0x20) incl. VLAN 1; 33rd -> 7505 | ESCP; ESSLIB/SMRT misread it as a string " " |
| 8708-8713 | R | no reply on 108E v6 (management VLAN etc.) | ESCP |

Default VLAN 1 name: `Default_VLAN` on Gen A (ESSLIB capture, SMRT README), `Default` on 108E v6 (ESCP).
Gen A: a GET 8705 returns 8704, all 8705 rows, then 8707; a GET 8706 returns 8706 rows then 8707
(ESSLIB captures, SMRT README). Gen B returns only what was asked (+ the listed companions) (ESCP).

### 7.8 PoE (TL-SG108PE) - UDP layout UNKNOWN

USER names: 13568 PoE status, 13824 PoE config, 14080 PoE global recovery, 14336 PoE recovery,
14592 PoE extend. **No source gives a byte layout.** Value semantics known from the **web UI**
(VMK `classes.py`/`tplink_api.py`, EBZ; TL-SG1016PE), likely but not proven to match the UDP fields:

| Field | Web UI read encoding |
|---|---|
| per-port state | 1 = enabled, 0 = disabled |
| priority | 0 high, 1 middle, 2 low (web UI write uses 1/2/3) |
| power limit | 0.1 W units: 330 = auto (also "class 0"), 40 = class 1 (4 W), 70 = class 2 (7 W), 154 = class 3 (15.4 W), 300 = class 4 (30 W), any other value = manual limit in 0.1 W (UI range 0.1-30.0 W). Web UI write: 1 auto, 2-5 class 1-4, 6 manual + value |
| power | 0.1 W; voltage 0.1 V; current mA |
| PD class | same codes as power limit (40/70/154/300/330) |
| power status | 0 off, 1 turning on, 2 on, 3 overload, 4 short, 5 non-standard PD, 6 voltage high, 7 voltage low, 8 hardware fault, 9 over-temperature |
| global | system power limit, limit min, limit max, consumption, remain: all 0.1 W |
| PoE port count | `poe_port_num` (TL-SG1016PE: 8) |

Recovery/extend semantics: no source. TL-SG108PE PoE port count / budget is not in any source
(datasheet: ports 1-4, 55-64 W depending on revision - **not from the sources, verify**).

### 7.9 Other

| Type | Name | Status |
|---|---|---|
| 16896 | cable test | name only (JSI, SLK, USER); no layout |
| 2101, 2200 | unnamed ("PORT_VLAN_FOOOOO", "VLAN_FOOOOOO") | SLK only, unknown |

## 8. Operations

### 8.1 Write

Gen A: `GET 2305` -> SET(op 3, token, 512, 514, settings...) -> op 4 reply, err 0 = accepted.
Gen B: SET(op 3, 512, 514, settings...) session-encrypted -> op 4.
- Gen A op 4 reply **contains the updated table** (SMRT README shows the full VLAN/PVID table after a
  set; ANS "it returns the settings after our change"). Gen B reply TLVs: not documented (ESCP's fake
  returns none) **[UNVERIFIED]**. Always read back to confirm.
- Error 0 does not prove it was kept (inactive-feature writes are accepted and dropped, ESCP).
- A write that got no reply may or may not have been applied (ESCP).

### 8.2 Change password

ANS (Gen A, after factory reset): SET with 512 = old user, 514 = old password, then 514 = old password
again, 513 = new username, 515 = new password; expect op 4, err 0. **[1 src, GPL facts]**
User and password max 16 chars (ESCP web UI limits, JSI).

### 8.3 Persistence (save)

- DBO (108PE fw 20201030): "A set changes the running config only. Persisting needs a second request
  carrying TLV 2304, with its own fresh token." The vendor utility always saves. **[1 src]**
- TLV 13 auto-save = 1 means "configuration changes are saved automatically" (ESCP comment; ESCP never
  sends 2304). SMRT README shows 13 = 1 on a 108E v4.
- **[INFER]** If 13 = 1 writes persist; if 13 = 0 or absent send 2304 after the writes. Sending 2304 is
  harmless as far as known. Error -2 = "save to flash failed" (DBO).

### 8.4 Config backup (768)

Gen B (ESCP verified): authenticated SET (512, 514, empty 768). Reply op 4 in several packets, each with
TLV 768 holding a chunk: 1400 B + 858 B = 2258 B total for 108E v6; all packets but the last have
header offset 22 = 1; no index, no checksum. File magic `23 89 23 89`. Accept only if it starts with the
magic, all but the last chunk have equal length and the last is not longer; ESCP also requires two
downloads to match. Restore (780) not documented.

### 8.5 Reboot / factory reset / firmware

- Reboot: SET 773 = u8 (1 = save first) (SLK). Reset: SET 1280 empty (SLK). Both after GET 2305 on Gen A.
  Not verified by any source with a live switch **[UNVERIFIED]**. Expect the reply may not arrive.
- Firmware upgrade 1536: no layout known. Do not implement.

## 9. Model differences (from sources)

| Item | 105E | 108E | 108PE | 1016DE / 1024DE |
|---|---|---|---|---|
| ports (TLV 10) | 5 | 8 | 8 | 16 / 24 (masks are u32, so up to 32 ports) |
| LAG groups | 1 row in capture | 2 (1-4, 5-8) | ? | ? |
| 802.1Q table (8707) | 32 | 32 | ? | ? |
| port-based groups (8450) | ? | 8 | ? | ? (ESCP guesses could differ) |
| Protocol gen | A | A up to v4/v5; B on v6 2023+ | A on fw 20201030 (DBO) | ? |
| PoE TLVs | - | - | yes (layout unknown) | - |

## 10. Error codes (reply header)

| Code | Meaning | Source |
|---|---|---|
| 0 | OK (also: accepted-but-ignored) | all |
| 1 | GET named several types (Gen B) | ESCP |
| -2 | Gen B: read refused (GET 11); DBO: save to flash failed | ESCP, DBO **[DISAGREE: context]** |
| 3 | System-page SET missing one of 2/9/4/5/6 | ESCP |
| 4 | LAG ports outside the group's range | ESCP |
| 6 | invalid IP | DBO (vendor utility) |
| 7 | wrong username or password | DBO; JSI "7 or 8 are somehow bad" |
| 8 | access denied | DBO, JSI |
| 9 | invalid netmask | DBO |
| 10 | invalid gateway | DBO |
| 4098 | session key accepted (not an error) | ESCP |
| 7205 / 7206 | LAG: port is mirror destination / is mirrored | ESCP |
| 7401 | LAG: port in a non-default port-based VLAN | ESCP |
| 7403 / 7404 | LAG: port has bandwidth limit / storm control | ESCP |
| 7405 | LAG: members' QoS priorities differ (port-based mode) | ESCP |
| 7406 | LAG: members' fixed speeds differ | ESCP |
| 7407 | LAG: member half duplex (also on a port write) | ESCP |
| 7410 | LAG: auto mixed with fixed speed (auto vs 1000F accepted) | ESCP |
| 7415 | LAG: single port | ESCP |
| 7417 / 7418 | LAG: flow control differs / not all enabled or all disabled | ESCP |
| 7503 | refused setting (port-based group > max, deleting a missing group); generic | ESCP |
| 7505 | 802.1Q table full | ESCP |
| (silence) | Gen A missing/stale token; Gen A GET of 512; GET of unknown types; 528 on Gen A | DBO, ESCP |

Bad credentials on Gen B: code not established (ESCP's fake uses 5, untested) **[UNVERIFIED]**.

## 11. Fragmentation

Only observed for the config download (8.4): consecutive op 4 packets, same sequence, offset 22 = 1 on
all but the last, each a full packet with its own header and end marker. No source saw fragmented GET
replies; at <= 24 ports all known tables fit one datagram. ESCP bounds reassembly at 64 KiB / 64 packets.

## 12. Open questions for a hardware test (TL-SG108PE)

1. Which generation is the tester's unit (hardware rev, firmware)? Does it answer TLV 528 (Gen B) or need
   tokens (Gen A)? Does Gen B still answer GET 2305?
2. PoE: byte layout of 13568/13824/14080/14336/14592 for read and write; units (0.1 W?); priority and
   limit codes on the wire; per-port vs global; recovery/extend meaning; PoE port count and budget.
   Capture the vendor utility (Windows) while changing each PoE field.
3. Is a write persistent without 2304 when TLV 13 = 1? Does 108PE report 13?
4. Gen A: is the token really single-use (DBO) or reusable across GETs/SETs (SMRT/ESSLIB behaviour)?
5. Content of op 4 replies to writes (echoed table or empty) on each generation.
6. Error code for wrong credentials (7?) and for a malformed value.
7. Does a Gen A GET with several types work? Which companion TLVs come back per GET?
8. Reboot 773 value semantics, reset 1280, whether replies are sent before rebooting.
9. Cable test 16896 request/result layout and timing.
10. Bandwidth/storm rate encoding on older firmware (kbps vs codes); storm control availability on 108PE.
11. LAG group count/ranges on 105E, 108PE, 1016DE, 1024DE; 8450 and 8707 values on those.
12. Fragmentation of large replies on 16/24-port models; config file size/magic on Gen A; 780 restore.
13. Password change via 513/515 on Gen B; whether the double 514 is needed.
14. Does IP/DHCP write on Gen A also require all five TLVs (2,9,4,5,6)?
15. TLV 11, 15, 16, 2101, 2200 meanings.
