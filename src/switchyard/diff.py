"""Describe what differs between two configuration backups (live state vs. a file)."""

from __future__ import annotations


def _ports(values) -> str:
    return ", ".join(str(p) for p in sorted(values)) or "none"


def _table(value) -> dict:
    return {int(k): v for k, v in (value or {}).items()}


def diff_config(live: dict, other: dict) -> list[dict]:
    """Lines saying what a restore of ``other`` would change on the switch described by ``live``.

    Both are the ``config`` part of a backup. Each line is ``{"area", "text"}``.
    """
    out: list[dict] = []

    def add(area: str, text: str) -> None:
        out.append({"area": area, "text": text})

    def scalar(area: str, label: str, a, b) -> None:
        if a != b:
            add(area, f"{label}: {a} → {b}")

    a, b = live.get("network") or {}, other.get("network") or {}
    for key in ("name", "dhcp", "ip", "netmask", "gateway"):
        if key in b:
            scalar("Network", key, a.get(key), b.get(key))

    va, vb = live.get("vlan"), other.get("vlan")
    if vb:
        scalar("VLAN", "mode", (va or {}).get("mode"), vb.get("mode"))
        if va and va.get("mode") == vb.get("mode"):
            for kind in ("dot1q", "port_vlans"):
                mine = {v["id"]: v for v in va.get(kind, [])}
                theirs = {v["id"]: v for v in vb.get(kind, [])}
                for vid in sorted(set(mine) | set(theirs)):
                    if vid not in theirs:
                        add("VLAN", f"VLAN {vid} would be removed")
                    elif vid not in mine:
                        add(
                            "VLAN",
                            f"VLAN {vid} would be added (ports {_ports(theirs[vid]['members'])})",
                        )
                    else:
                        x, y = mine[vid], theirs[vid]
                        if sorted(x["members"]) != sorted(y["members"]):
                            add(
                                "VLAN",
                                f"VLAN {vid} members: {_ports(x['members'])} → {_ports(y['members'])}",
                            )
                        if sorted(x.get("tagged", [])) != sorted(y.get("tagged", [])):
                            add(
                                "VLAN",
                                f"VLAN {vid} tagged: {_ports(x.get('tagged', []))} → {_ports(y.get('tagged', []))}",
                            )
            pa, pb = _table(va.get("pvid")), _table(vb.get("pvid"))
            for port in sorted(set(pa) | set(pb)):
                if pa.get(port) != pb.get(port):
                    add("VLAN", f"Port {port} PVID: {pa.get(port)} → {pb.get(port)}")

    qa, qb = live.get("qos"), other.get("qos")
    if qb and qa:
        scalar("QoS", "mode", qa.get("mode"), qb.get("mode"))
        scalar("QoS", "storm control", qa.get("storm_control"), qb.get("storm_control"))
        for key, label in (
            ("priorities", "priority"),
            ("ingress", "ingress limit"),
            ("egress", "egress limit"),
            ("storm_rates", "storm rate"),
        ):
            ta, tb = _table(qa.get(key)), _table(qb.get(key))
            for port in sorted(set(ta) | set(tb)):
                if ta.get(port) != tb.get(port):
                    add("QoS", f"Port {port} {label}: {ta.get(port)} → {tb.get(port)}")

    la, lb = (live.get("lag") or {}).get("lags", []), (other.get("lag") or {}).get("lags", [])
    ma = {g["id"]: g for g in la}
    for g in lb:
        old = ma.get(g["id"], {"enabled": False, "members": []})
        if old["enabled"] != g["enabled"] or sorted(old["members"]) != sorted(g["members"]):
            add(
                "Link aggregation",
                f"LAG {g['id']}: {'on' if old['enabled'] else 'off'} ({_ports(old['members'])}) → {'on' if g['enabled'] else 'off'} ({_ports(g['members'])})",
            )

    aa, ab = _table(live.get("port_admin")), _table(other.get("port_admin"))
    for port in sorted(set(aa) | set(ab)):
        x, y = aa.get(port), ab.get(port)
        if (x or {}).get("speed_code") != (y or {}).get("speed_code") or (x or {}).get(
            "flow_control"
        ) != (y or {}).get("flow_control"):
            add(
                "Port settings",
                f"Port {port}: {(x or {}).get('speed')} → {(y or {}).get('speed')}, flow control {(x or {}).get('flow_control')} → {(y or {}).get('flow_control')}",
            )

    if other.get("mirror") is not None and live.get("mirror") != other.get("mirror"):
        ma_, mb_ = live.get("mirror") or {}, other["mirror"]
        add(
            "Mirroring",
            f"destination {ma_.get('destination')} sources {_ports(ma_.get('sources', []))} → destination {mb_.get('destination')} sources {_ports(mb_.get('sources', []))}",
        )
    if other.get("igmp") is not None and live.get("igmp") != other.get("igmp"):
        add("IGMP snooping", f"{live.get('igmp')} → {other['igmp']}")
    if other.get("loop_detection") is not None:
        scalar("Loop detection", "enabled", live.get("loop_detection"), other["loop_detection"])

    labels_a, labels_b = live.get("labels") or {}, other.get("labels") or {}
    if labels_b and labels_a != labels_b:
        add("Names", "Port or VLAN names would change")
    return out
