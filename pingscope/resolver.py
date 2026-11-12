"""Turns configured targets into measurable series: one per target per address family (IPv4/IPv6)."""
import asyncio
import socket
import time
from dataclasses import dataclass, field

from .model import ip_version, is_hostname, norm_ip

# When the DNS lookup of a hostname fails (no record anymore, timeout, server failure), its last known address is
# measured for this long, with a DNS status and a DNS alert; after that the series is shown as down.
DNS_STALE_S = 86400
PROBLEMS = {"no_record": "no {rr} record", "timeout": "DNS lookup timed out", "failure": "DNS server failure"}


@dataclass
class Series:
    key: str            # "<target id>-<family>"
    target: dict
    family: int
    address: str | None  # None when the hostname could not be resolved
    error: str | None = None
    via: str = "literal"  # literal | dns
    dns: dict | None = None  # DNS problem: {"problem", "since", "until", "rr", "name"} (see dns_text)

    @property
    def target_id(self):
        return self.target["id"]

    @property
    def path(self):
        """Network path identity shared by all probes towards the same address (used for mtr)."""
        return f"{self.family}|{self.address}" if self.address else None


def dns_text(d, with_name=True):
    """'no A record for example.nl', 'DNS lookup timed out', ... for a Series.dns dict."""
    text = PROBLEMS.get(d["problem"], "DNS lookup failed").format(rr=d["rr"])
    if with_name and d.get("name"):
        text += f" for {d['name']}" if d["problem"] == "no_record" else f" ({d['name']})"
    return text


async def lookup(name, family, timeout=5.0):
    return (await lookup_status(name, family, timeout))[0]


async def lookup_status(name, family, timeout=5.0):
    """(addresses, problem): problem is None, "no_record" (the name or its A/AAAA record doesn't exist: a real answer),
    "timeout" or "failure" (the DNS server didn't give an answer; says nothing about the record)."""
    loop = asyncio.get_running_loop()
    af = socket.AF_INET if family == 4 else socket.AF_INET6
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(name, None, family=af, type=socket.SOCK_DGRAM), timeout)
    except asyncio.TimeoutError:
        return [], "timeout"
    except socket.gaierror as e:
        definite = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -5), getattr(socket, "EAI_ADDRFAMILY", -9)}
        return [], "no_record" if e.errno in definite else "failure"
    except UnicodeError:
        return [], "no_record"  # not a valid hostname
    except OSError:
        return [], "failure"
    out = []
    for *_, sa in infos:
        try:
            a = norm_ip(sa[0])
        except ValueError:
            continue
        if ip_version(a) == family and a not in out:
            out.append(a)
    return out, None if out else "no_record"


@dataclass
class _Entry:
    addrs: list = field(default_factory=list)
    at: float = 0.0
    good: str | None = None       # last address that resolved
    problem: str | None = None    # since the last good answer: no_record | timeout | failure
    since: float | None = None    # when the problem started


class Resolver:
    """Hostname lookups for the measurement rounds. With a store, the last good address and an ongoing DNS problem are
    kept in the database, so a restart during a DNS problem still measures the last known address."""

    def __init__(self, store=None):
        self.cache: dict[tuple, _Entry] = {}
        self.store = store
        if store is not None:
            for (name, fam), (good, problem, since) in store.dns_states().items():
                self.cache[(name, fam)] = _Entry(good=good, problem=problem, since=since)

    async def get(self, name, family, refresh):
        now = time.time()
        e = self.cache.get((name, family))
        if e and e.at and now - e.at < (refresh if e.addrs else min(refresh, 60)):  # retry failures every round
            return e
        addrs, problem = await lookup_status(name, family)
        e = e or _Entry()
        before = (e.good, e.problem)
        e.addrs, e.at = addrs, now
        if addrs:
            e.good, e.problem, e.since = addrs[0], None, None
        elif e.problem is None or e.problem != problem:
            e.since = e.since if e.problem else now  # a timeout that becomes "no record" is the same outage
            e.problem = problem
        self.cache[(name, family)] = e
        if self.store is not None and (e.good, e.problem) != before:
            self.store.save_dns_state(name, family, e.good, e.problem, e.since)
        return e

    def forget_unused(self, names):
        for k in list(self.cache):
            if k[0] not in names:
                del self.cache[k]
        if self.store is not None:
            self.store.forget_dns_states(names)

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
                    dns = e.problem and {"problem": e.problem, "since": int(e.since or now), "rr": rr, "name": hostname,
                                         "until": int((e.since or now) + DNS_STALE_S)}
                    if e.addrs:
                        series.append(Series(key, t, fam, e.addrs[0], via="dns"))
                    elif e.good and now < dns["until"]:
                        # DNS fails but resolved before: keep measuring the last known address, with a DNS status
                        series.append(Series(key, t, fam, e.good, None, "dns", dns))
                    elif e.good:
                        series.append(Series(key, t, fam, None, f"{_cap(dns_text(dns))} for more than 24 hours",
                                             "dns", dns))
                    elif t["families"] != "auto":
                        series.append(Series(key, t, fam, None, _cap(dns_text(dns)), "dns", dns))
            if not series and hostname and t["families"] == "auto":
                # Neither family resolves: show the target as down rather than hiding it.
                e = jobs[(hostname, 4)]
                dns = e.problem and {"problem": e.problem, "since": int(e.since or now), "rr": "A", "name": hostname,
                                     "until": int(e.since or now)}
                series.append(Series(f"{t['id']}-4", t, 4, None, f"{hostname} does not resolve", "dns", dns))
            out.extend(series)
        return out


def _cap(text):
    return text[:1].upper() + text[1:]


def _wanted(t):
    return {"auto": (4, 6), "ipv4": (4,), "ipv6": (6,)}.get(t["families"], (4, 6))
