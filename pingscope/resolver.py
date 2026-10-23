"""Turns configured targets into measurable series: one per target per address family (IPv4/IPv6)."""
import asyncio
import socket
import time
from dataclasses import dataclass, field

from .model import ip_version, is_hostname, norm_ip

KEEP_UNRESOLVED_S = 86400  # keep showing a family that resolved earlier for this long after DNS stops answering


@dataclass
class Series:
    key: str            # "<target id>-<family>"
    target: dict
    family: int
    address: str | None  # None when the hostname could not be resolved
    error: str | None = None
    via: str = "literal"  # literal | dns

    @property
    def target_id(self):
        return self.target["id"]

    @property
    def path(self):
        """Network path identity shared by all probes towards the same address (used for mtr)."""
        return f"{self.family}|{self.address}" if self.address else None


async def lookup(name, family, timeout=5.0):
    loop = asyncio.get_running_loop()
    af = socket.AF_INET if family == 4 else socket.AF_INET6
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(name, None, family=af, type=socket.SOCK_DGRAM), timeout)
    except (socket.gaierror, asyncio.TimeoutError, OSError, UnicodeError):
        return []
    out = []
    for *_, sa in infos:
        try:
            a = norm_ip(sa[0])
        except ValueError:
            continue
        if ip_version(a) == family and a not in out:
            out.append(a)
    return out


@dataclass
class _Entry:
    addrs: list = field(default_factory=list)
    at: float = 0.0
    good: str | None = None
    good_at: float = 0.0


class Resolver:
    def __init__(self):
        self.cache: dict[tuple, _Entry] = {}

    async def get(self, name, family, refresh):
        now = time.time()
        e = self.cache.get((name, family))
        if e and now - e.at < (refresh if e.addrs else min(refresh, 60)):
            return e
        addrs = await lookup(name, family)
        e = e or _Entry()
        e.addrs, e.at = addrs, now
        if addrs:
            e.good, e.good_at = addrs[0], now
        self.cache[(name, family)] = e
        return e

    def forget_unused(self, names):
        for k in list(self.cache):
            if k[0] not in names:
                del self.cache[k]

    async def plan(self, targets, refresh):
        """Build the list of series for all targets, resolving hostnames concurrently."""
        jobs = {}
        for t in targets:
            if t["host"] and is_hostname(t["host"]):
                for fam in _wanted(t):
                    if not (fam == 6 and t["host_v6"]):
                        jobs[(t["host"], fam)] = None
        keys = list(jobs)
        results = await asyncio.gather(*[self.get(n, f, refresh) for n, f in keys])
        jobs = dict(zip(keys, results))
        self.forget_unused({k[0] for k in keys})

        now = time.time()
        out = []
        for t in targets:
            series = []
            hostname = t["host"] if t["host"] and is_hostname(t["host"]) else None
            for fam in _wanted(t):
                key = f"{t['id']}-{fam}"
                if fam == 6 and t["host_v6"]:
                    series.append(Series(key, t, 6, t["host_v6"]))
                elif t["host"] and ip_version(t["host"]) == fam:
                    series.append(Series(key, t, fam, t["host"]))
                elif hostname:
                    e = jobs[(hostname, fam)]
                    rr = "A" if fam == 4 else "AAAA"
                    if e.addrs:
                        series.append(Series(key, t, fam, e.addrs[0], via="dns"))
                    elif e.good and now - e.good_at < KEEP_UNRESOLVED_S:
                        series.append(Series(key, t, fam, None, f"No {rr} record for {hostname} (resolved earlier)", "dns"))
                    elif t["families"] != "auto":
                        series.append(Series(key, t, fam, None, f"No {rr} record for {hostname}", "dns"))
            if not series and hostname and t["families"] == "auto":
                # Neither family resolves: show the target as down rather than hiding it.
                series.append(Series(f"{t['id']}-4", t, 4, None, f"{hostname} does not resolve", "dns"))
            out.extend(series)
        return out


def _wanted(t):
    return {"auto": (4, 6), "ipv4": (4,), "ipv6": (6,)}.get(t["families"], (4, 6))
