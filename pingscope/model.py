"""Target validation and address helpers."""
import ipaddress
import re

FAMILY_MODES = {
    "auto": "IPv4 + IPv6",
    "ipv4": "IPv4 only",
    "ipv6": "IPv6 only",
}
DNS_TYPES = ["A", "AAAA", "NS", "SOA", "MX", "TXT", "CNAME", "PTR"]

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)*[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.?$", re.I)


def ip_version(s):
    try:
        return ipaddress.ip_address(str(s).strip().strip("[]")).version
    except ValueError:
        return None


def norm_ip(s):
    return str(ipaddress.ip_address(str(s).strip().strip("[]").split("%")[0]))


def is_hostname(s):
    s = str(s or "").strip()
    return bool(s) and ip_version(s) is None and bool(_HOST_RE.match(s))


def norm_host(s):
    s = str(s or "").strip().strip("[]")
    if not s:
        return ""
    if ip_version(s):
        return norm_ip(s)
    return s.lower().rstrip(".")


class ValidationError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def clean_target(data, probes):
    """Normalize a target dict from the admin form or an import. Raises ValidationError."""
    err = {}
    t = {}
    t["name"] = str(data.get("name") or "").strip()[:200]
    t["probe"] = str(data.get("probe") or "ping").strip().lower()
    t["host"] = norm_host(data.get("host"))
    t["host_v6"] = norm_host(data.get("host_v6"))
    t["families"] = str(data.get("families") or "auto").strip().lower()
    t["dns_query"] = str(data.get("dns_query") or "").strip().rstrip(".")[:253] if data.get("dns_query") else ""
    t["dns_type"] = str(data.get("dns_type") or "A").strip().upper()
    t["note"] = str(data.get("note") or "").strip()[:1000]
    t["mtr"] = bool(data.get("mtr", True))
    t["enabled"] = bool(data.get("enabled", True))
    t["alert_skip"] = bool(data.get("alert_skip", False))
    ids = [x for x in re.split(r"[\s,;]+", str(data.get("alert_chat_ids") or "").strip()) if x]
    bad = [x for x in ids if not re.fullmatch(r"-?\d{3,20}|@[A-Za-z]\w{4,31}", x)]
    if bad:
        err["alert_chat_ids"] = f"Not a Telegram chat ID: {', '.join(bad[:3])} (use numbers like 123456789 or -100123…)"
    t["alert_chat_ids"] = ", ".join(dict.fromkeys(ids))[:500]
    for k in ("port", "count"):
        v = data.get(k)
        if v in (None, ""):
            t[k] = None
        else:
            try:
                t[k] = int(v)
            except (TypeError, ValueError):
                err[k] = "must be a whole number"
                t[k] = None
    if t["port"] is not None and not 1 <= t["port"] <= 65535:
        err["port"] = "must be between 1 and 65535"
    if t["count"] is not None and not 1 <= t["count"] <= 100:
        err["count"] = "must be between 1 and 100"

    if t["probe"] not in probes:
        err["probe"] = f"unknown probe; choose one of {', '.join(probes)}"
    if t["families"] not in FAMILY_MODES:
        err["families"] = "must be auto, ipv4 or ipv6"
    if t["dns_type"] not in DNS_TYPES:
        err["dns_type"] = f"must be one of {', '.join(DNS_TYPES)}"

    if not t["host"] and not t["host_v6"]:
        err["host"] = "enter a hostname, IPv4 or IPv6 address"
    elif t["host"] and not ip_version(t["host"]) and not is_hostname(t["host"]):
        err["host"] = "not a valid hostname or IP address"
    if t["host_v6"] and ip_version(t["host_v6"]) != 6:
        err["host_v6"] = "must be an IPv6 address"
    if t["host"] and ip_version(t["host"]) == 6:
        if t["host_v6"] and t["host_v6"] != t["host"]:
            err["host_v6"] = "the host is already an IPv6 address"
        t["host_v6"], t["host"] = t["host"], ""

    if t["probe"] == "tcp" and t["port"] is None:
        t["port"] = 443
    if not t["name"]:
        t["name"] = t["host"] or t["host_v6"]
    if err:
        raise ValidationError(err)
    return t


def planned_families(t):
    """Families this target can produce without DNS lookups (literals only); hostnames decide at runtime."""
    fams = []
    want = {"auto": (4, 6), "ipv4": (4,), "ipv6": (6,)}[t["families"]]
    for f in want:
        if f == 6 and t.get("host_v6"):
            fams.append(6)
        elif t.get("host") and ip_version(t["host"]) == f:
            fams.append(f)
        elif t.get("host") and is_hostname(t["host"]):
            fams.append(f)
    return fams
