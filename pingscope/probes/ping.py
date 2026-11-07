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


def fping_cmd(addrs, family, count, spacing_ms, timeout_ms, size):
    """The exact fping command line used for a measurement round (also used by `pingscope ping-test`)."""
    return ["fping", f"-{family}", "-q", "-C", str(count), "-p", str(spacing_ms), "-t", str(timeout_ms),
            "-b", str(size), "-i", "5", "-B1", "-r0", *addrs]


async def fping(addrs, family, opts):
    cmd = fping_cmd(addrs, family, *opts)
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

    def size(self, series, s):
        return series.target.get("packet_size") or s["ping_size"]

    def opts(self, series, s):
        """fping options of a series: targets with the same options share one fping process."""
        return (self.count(series, s), self.spacing_ms(series, s), self.timeout_ms(series, s), self.size(series, s))

    async def run(self, series, s):
        groups = {}
        for x in series:
            if x.address:
                groups.setdefault((x.family, self.opts(x, s)), set()).add(x.address)
        jobs = list(groups.items())
        outs = await asyncio.gather(*[fping(sorted(addrs), fam, opts) for (fam, opts), addrs in jobs],
                                    return_exceptions=True)
        by, failed, errors = {}, set(), []
        for ((fam, opts), _), res in zip(jobs, outs):
            if isinstance(res, Exception):
                failed.add(fam)
                errors.append(f"IPv{fam}: {res}")
                continue
            for addr, r in res.items():
                by[(fam, opts, addr)] = r
        out = {}
        for x in series:
            cnt, opts = self.count(x, s), self.opts(x, s)
            if x.family in failed and x.address:
                continue  # not measured: leave a gap
            if x.address and (x.family, opts, x.address) in by:
                out[x.key] = by[(x.family, opts, x.address)]
            else:
                out[x.key] = Result(cnt, cnt, [])
        if errors:
            raise PartialFailure(out, "; ".join(errors))
        return out
