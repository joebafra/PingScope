"""ICMP echo via fping — one fping process per address family and count, all targets in parallel."""
import asyncio
import logging
import shutil

from ..model import norm_ip
from .base import PartialFailure, Probe, Result, register

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
        raise RuntimeError("fping is not installed (apt install fping)")
    except OSError as e:
        raise RuntimeError(f"can't start fping: {e}")
    text = err.decode(errors="replace") + "\n" + out.decode(errors="replace")
    res = parse_fping(text)
    if addrs and not res:
        # fping itself failed (e.g. no permission for raw sockets); this is not packet loss
        msg = next((l.strip() for l in text.splitlines() if l.strip()), f"exit code {proc.returncode}")
        raise RuntimeError(f"fping failed: {msg}")
    return res


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
        outs = await asyncio.gather(*[fping(sorted(addrs), fam, cnt, s) for (fam, cnt), addrs in jobs],
                                    return_exceptions=True)
        by, failed, errors = {}, set(), []
        for ((fam, cnt), _), res in zip(jobs, outs):
            if isinstance(res, Exception):
                failed.add(fam)
                errors.append(f"IPv{fam}: {res}")
                continue
            for addr, r in res.items():
                by[(fam, cnt, addr)] = r
        out = {}
        for x in series:
            cnt = self.count(x, s)
            if x.family in failed and x.address:
                continue  # not measured: leave a gap
            if x.address and (x.family, cnt, x.address) in by:
                out[x.key] = by[(x.family, cnt, x.address)]
            else:
                out[x.key] = Result(cnt, cnt, [])
        if errors:
            raise PartialFailure(out, "; ".join(errors))
        return out
