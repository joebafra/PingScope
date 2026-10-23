"""ICMP echo via fping — one fping process per address family and count, all targets in parallel."""
import asyncio
import logging
import shutil

from ..model import norm_ip
from .base import Probe, Result, register

log = logging.getLogger("pingscope")


def parse_fping(text):
    """Parse `fping -C` output lines like '1.1.1.1 : 12.1 - 11.9'."""
    res = {}
    for line in text.splitlines():
        if " : " not in line:
            continue
        host, vals = line.split(" : ", 1)
        try:
            host = norm_ip(host.strip())
        except ValueError:
            continue
        parts = vals.split()
        rtts = []
        for p in parts:
            if p != "-":
                try:
                    rtts.append(float(p))
                except ValueError:
                    pass
        res[host] = Result(len(parts), len(parts) - len(rtts), rtts)
    return res


async def fping(addrs, family, count, s):
    cmd = ["fping", f"-{family}", "-q", "-C", str(count), "-p", str(s["probe_spacing_ms"]),
           "-t", str(s["timeout_ms"]), "-i", "5", "-B1", "-r0", *addrs]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
    except FileNotFoundError:
        log.error("fping not found; install it (apt install fping)")
        return {}
    return parse_fping(err.decode(errors="replace") + "\n" + out.decode(errors="replace"))


@register
class PingProbe(Probe):
    name = "ping"
    label = "Ping (ICMP)"
    description = "Round-trip time and packet loss with fping. Works for any reachable host."
    count_setting = "ping_count"

    def available(self):
        return bool(shutil.which("fping"))

    async def run(self, series, s):
        groups = {}
        for x in series:
            if x.address:
                groups.setdefault((x.family, self.count(x, s)), set()).add(x.address)
        jobs = list(groups.items())
        outs = await asyncio.gather(*[fping(sorted(addrs), fam, cnt, s) for (fam, cnt), addrs in jobs])
        by = {}
        for ((fam, cnt), _), res in zip(jobs, outs):
            for addr, r in res.items():
                by[(fam, cnt, addr)] = r
        out = {}
        for x in series:
            cnt = self.count(x, s)
            if x.address and (x.family, cnt, x.address) in by:
                out[x.key] = by[(x.family, cnt, x.address)]
            else:
                out[x.key] = Result(cnt, cnt, [])
        return out
