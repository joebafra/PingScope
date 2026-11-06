"""Import from a SmokePing Targets config or a PingScope JSON export, and export to JSON."""
import json
import re

from .settings import DEFAULTS
from .model import ValidationError, clean_target, ip_version, norm_ip

# Well-known IPv6 counterparts of IPv4 addresses (root servers, public resolvers).
KNOWN_IPV6 = {
    "198.41.0.4": "2001:503:ba3e::2:30",      # a.root-servers.net
    "170.247.170.2": "2801:1b8:10::b",        # b.root-servers.net (current)
    "199.9.14.201": "2801:1b8:10::b",         # b.root-servers.net (old IPv4, see warning)
    "192.33.4.12": "2001:500:2::c",           # c.root-servers.net
    "199.7.91.13": "2001:500:2d::d",          # d.root-servers.net
    "192.203.230.10": "2001:500:a8::e",       # e.root-servers.net
    "192.5.5.241": "2001:500:2f::f",          # f.root-servers.net
    "192.112.36.4": "2001:500:12::d0d",       # g.root-servers.net
    "198.97.190.53": "2001:500:1::53",        # h.root-servers.net
    "192.36.148.17": "2001:7fe::53",          # i.root-servers.net
    "192.58.128.30": "2001:503:c27::2:30",    # j.root-servers.net
    "193.0.14.129": "2001:7fd::1",            # k.root-servers.net
    "199.7.83.42": "2001:500:9f::42",         # l.root-servers.net
    "202.12.27.33": "2001:dc3::35",           # m.root-servers.net
    "8.8.8.8": "2001:4860:4860::8888",        # Google Public DNS
    "8.8.4.4": "2001:4860:4860::8844",
    "1.1.1.1": "2606:4700:4700::1111",        # Cloudflare
    "1.0.0.1": "2606:4700:4700::1001",
    "9.9.9.9": "2620:fe::fe",                 # Quad9
    "149.112.112.112": "2620:fe::9",
    "208.67.222.222": "2620:119:35::35",      # OpenDNS
    "208.67.220.220": "2620:119:53::53",
    "45.90.28.0": "2a07:a8c0::",              # NextDNS anycast
    "45.90.30.0": "2a07:a8c1::",
}

OUTDATED = {
    "199.9.14.201": "b.root-servers.net moved to 170.247.170.2 in November 2023; the old address may stop answering.",
}

PROBE_MAP = {"fping": ("ping", "auto"), "fping6": ("ping", "ipv6"), "dns": ("dns", "auto"),
             "tcpping": ("tcp", "auto"), "fpingcontinuous": ("ping", "auto")}

_NODE = re.compile(r"^(\++)\s*(\S+)\s*$")
_VAR = re.compile(r"^([A-Za-z_][\w-]*)\s*=\s*(.*?)\s*$")
_SECTION = re.compile(r"^\*\*\*\s*(\w+)\s*\*\*\*\s*$")
_SUFFIX = re.compile(r"\s*\((dns lookup|dns|ping)\)\s*$", re.I)


def parse_smokeping(text):
    """Parse a SmokePing config. Returns (tree, warnings): tree = {site, groups:[{name, groups, targets}]}."""
    lines = text.replace("\r\n", "\n").split("\n")
    has_sections = any(_SECTION.match(l.strip()) for l in lines)
    section = None if has_sections else "targets"
    root = {"level": 0, "id": "", "vars": {}, "children": [], "line": 0}
    stack = [root]
    cur = root
    warnings = []
    for no, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _SECTION.match(line)
        if m:
            section = m.group(1).lower()
            continue
        if section != "targets":
            continue
        m = _NODE.match(line)
        if m:
            level = len(m.group(1))
            while stack and stack[-1]["level"] >= level:
                stack.pop()
            parent = stack[-1] if stack else root
            if level > parent["level"] + 1:
                warnings.append(f"Line {no}: '{m.group(2)}' skips a level; placed under '{parent['id'] or 'top'}'.")
            node = {"level": level, "id": m.group(2), "vars": {}, "children": [], "line": no}
            parent["children"].append(node)
            stack.append(node)
            cur = node
            continue
        m = _VAR.match(line)
        if m:
            cur["vars"][m.group(1).lower()] = m.group(2).strip()
        else:
            warnings.append(f"Line {no}: not understood: {line[:60]}")

    site = {"title": root["vars"].get("title", ""), "remark": root["vars"].get("remark", "")}
    inherit_keys = ("probe", "lookup", "pings", "port")

    def node_name(n):
        name = n["vars"].get("title") or n["vars"].get("menu") or n["id"]
        return _SUFFIX.sub("", " ".join(name.split()))

    def to_target(n, inh):
        v = {**inh, **n["vars"]}
        probe_raw = (v.get("probe") or "FPing").strip()
        probe, families = PROBE_MAP.get(probe_raw.lower(), (None, "auto"))
        if probe is None:
            warnings.append(f"Line {n['line']}: probe '{probe_raw}' is not supported; '{node_name(n)}' imported as ping.")
            probe = "ping"
        host = (v.get("server") if probe == "dns" and v.get("server") else v.get("host", "")).strip()
        t = {"name": node_name(n), "probe": probe, "host": host, "families": families}
        if probe == "dns":
            t["dns_query"] = v.get("lookup", "")
            t["dns_type"] = (v.get("recordtype") or "A").upper()
        if probe in ("dns", "tcp") and v.get("port"):
            t["port"] = v["port"]
        default_count = {"ping": DEFAULTS["ping_count"], "dns": DEFAULTS["dns_count"], "tcp": DEFAULTS["tcp_count"]}[probe]
        if v.get("pings") and str(v["pings"]).strip() != str(default_count):
            t["count"] = v["pings"]
        return t

    def walk(n, inh, path):
        inh = {**inh, **{k: n["vars"][k] for k in inherit_keys if k in n["vars"]}}
        group = {"name": node_name(n), "groups": [], "targets": []}
        for c in n["children"]:
            if "host" in c["vars"]:
                group["targets"].append(to_target(c, inh))
            if c["children"]:
                if "host" in c["vars"]:
                    # A target with sub-nodes: keep the sub-nodes in the same group.
                    sub = walk(c, inh, path + [node_name(c)])
                    group["targets"].extend(sub["targets"])
                    group["groups"].extend(sub["groups"])
                else:
                    group["groups"].append(walk(c, inh, path + [node_name(c)]))
            elif "host" not in c["vars"]:
                warnings.append(f"Line {c['line']}: '{node_name(c)}' has no host and no children; skipped.")
        return group

    top = walk(root, {"probe": root["vars"].get("probe", "FPing")}, [])
    groups = top["groups"]
    if top["targets"]:
        groups.insert(0, {"name": "Ungrouped", "groups": [], "targets": top["targets"]})
    return {"site": site, "groups": groups}, warnings


def parse_any(text):
    s = text.lstrip()
    if s.startswith("{"):
        try:
            data = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        if not isinstance(data, dict) or "groups" not in data:
            raise ValueError("JSON is not a PingScope export")
        return {"site": data.get("site") or {}, "groups": data["groups"], "settings": data.get("settings")}, [], "json"
    tree, warnings = parse_smokeping(text)
    if not tree["groups"]:
        raise ValueError("No targets found. Paste the Targets section of a SmokePing config.")
    return tree, warnings, "smokeping"


def prepare(tree, probes, add_known_ipv6=True, warnings=None):
    """Validate all targets, add known IPv6 addresses. Returns stats."""
    warnings = warnings if warnings is not None else []
    stats = {"groups": 0, "targets": 0, "by_probe": {}, "known_ipv6": 0, "hostnames": 0, "invalid": 0,
             "ipv4_only": 0, "duplicates": 0}
    seen = {}

    def walk(g, path):
        stats["groups"] += 1
        clean = []
        for raw in g.get("targets", []):
            try:
                t = clean_target(raw, probes)
            except ValidationError as e:
                stats["invalid"] += 1
                warnings.append(f"{' › '.join(path)} › {raw.get('name') or raw.get('host')}: {e}")
                continue
            if t["host"] in OUTDATED:
                warnings.append(f"{t['name']} ({t['host']}): {OUTDATED[t['host']]}")
            if add_known_ipv6 and not t["host_v6"] and t["host"] in KNOWN_IPV6 and t["families"] == "auto":
                t["host_v6"] = KNOWN_IPV6[t["host"]]
                stats["known_ipv6"] += 1
            if t["host"] and not ip_version(t["host"]):
                stats["hostnames"] += 1
            elif not t["host_v6"] and t["families"] != "ipv6":
                stats["ipv4_only"] += 1
            sig = (t["probe"], t["host"], t["host_v6"], t["dns_query"])
            if sig in seen:
                stats["duplicates"] += 1
                warnings.append(f"{' › '.join(path)} › {t['name']}: same {t['probe']} target as '{seen[sig]}'.")
            else:
                seen[sig] = " › ".join(path + [t["name"]])
            stats["targets"] += 1
            stats["by_probe"][t["probe"]] = stats["by_probe"].get(t["probe"], 0) + 1
            clean.append(t)
        g["targets"] = clean
        for sub in g.get("groups", []):
            walk(sub, path + [sub.get("name", "?")])

    for g in tree["groups"]:
        walk(g, [g.get("name", "?")])
    warnings[:] = list(dict.fromkeys(warnings))
    return stats


def outline(tree):
    def walk(g):
        return {"name": g.get("name"), "targets": len(g.get("targets", [])),
                "groups": [walk(s) for s in g.get("groups", [])]}
    return [walk(g) for g in tree["groups"]]


def apply(store, tree, mode="merge"):
    """Write a prepared tree into the store. mode: merge (add to existing) or replace (wipe first)."""
    if mode == "replace":
        store.clear_config()
    added = {"groups": 0, "targets": 0, "skipped": 0}
    existing_groups = store.groups()
    existing_targets = store.targets()

    def find_group(name, parent_id):
        for g in existing_groups:
            if g["parent_id"] == parent_id and g["name"].lower() == name.lower():
                return g["id"]
        return None

    def walk(g, parent_id):
        name = str(g.get("name") or "Unnamed").strip()
        gid = find_group(name, parent_id) if mode == "merge" else None
        if gid is None:
            gid = store.create_group(name, parent_id, g.get("sort_mode") if g.get("sort_mode") in ("manual", "name") else "manual",
                                     bool(g.get("hidden")), bool(g.get("show_addresses")))
            if g.get("alert_chat_ids"):
                try:
                    store.set_group_chats(gid, g["alert_chat_ids"])
                except ValueError:
                    pass
            if g.get("alert_mode") in ("off", "on"):
                try:
                    store.set_alerts(gid, g["alert_mode"], g.get("alert_loss"), g.get("alert_down", True),
                                     g.get("alert_email", True), g.get("alert_telegram", True),
                                     g.get("alert_rounds"))
                except ValueError:
                    pass
            existing_groups.append({"id": gid, "parent_id": parent_id, "name": name})
            added["groups"] += 1
        for t in g.get("targets", []):
            dup = any(x["group_id"] == gid and x["probe"] == t["probe"] and x["host"] == t["host"]
                      and x["host_v6"] == t["host_v6"] and x["name"] == t["name"] for x in existing_targets)
            if dup:
                added["skipped"] += 1
                continue
            tid = store.create_target(gid, t)
            existing_targets.append({**t, "id": tid, "group_id": gid})
            added["targets"] += 1
        for sub in g.get("groups", []):
            walk(sub, gid)

    for g in tree["groups"]:
        walk(g, None)
    return added


def export(store, include_settings=True):
    groups = store.groups()
    targets = store.targets()
    keep = ("name", "probe", "host", "host_v6", "families", "dns_query", "dns_type", "port", "count",
            "mtr", "enabled", "note", "alert_skip", "alert_chat_ids", "alert_rounds", "hide_address")

    def walk(parent_id):
        out = []
        for g in [g for g in groups if g["parent_id"] == parent_id]:
            out.append({
                "name": g["name"], "sort_mode": g.get("sort_mode", "manual"), "hidden": bool(g.get("hidden")),
                "show_addresses": bool(g.get("show_addresses")),
                "alert_mode": g.get("alert_mode", "inherit"), "alert_loss": g.get("alert_loss"),
                "alert_down": bool(g.get("alert_down", True)), "alert_email": bool(g.get("alert_email", True)),
                "alert_telegram": bool(g.get("alert_telegram", True)), "alert_rounds": g.get("alert_rounds"),
                "alert_chat_ids": g.get("alert_chat_ids", ""),
                "targets": [{k: t[k] for k in keep} for t in targets if t["group_id"] == g["id"]],
                "groups": walk(g["id"]),
            })
        return out

    s = store.settings()
    data = {"format": "pingscope", "version": 1,
            "site": {"title": s["site_title"], "remark": s["site_remark"]}, "groups": walk(None)}
    if include_settings:
        from .settings import SECRETS
        data["settings"] = {k: v for k, v in s.items() if k not in SECRETS}  # never export API tokens
    return data


def ipv6_candidates(t):
    """Hostnames worth an AAAA lookup for a target that has no IPv6 yet."""
    names = []
    if t["host"] and not ip_version(t["host"]):
        names.append(t["host"])
    for word in re.split(r"[\s,()]+", t["name"]):
        w = word.strip().lower().rstrip(".")
        if "." in w and not ip_version(w) and re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", w) and w not in names:
            names.append(w)
    return names


def norm_v6(s):
    return norm_ip(s) if ip_version(s) == 6 else None
