"""TCP connect time to a port (e.g. 443 for a web server, 25 for mail)."""
import asyncio
import time

from .base import Probe, register, spaced, to_result


async def connect_once(address, port, timeout_s):
    t0 = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout_s)
    except (asyncio.TimeoutError, OSError):
        return None
    rtt = (time.perf_counter() - t0) * 1000
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 1)
    except (asyncio.TimeoutError, OSError):
        pass
    return rtt


@register
class TcpProbe(Probe):
    name = "tcp"
    label = "TCP connect"
    description = "Time to open a TCP connection to a port. Useful for hosts that block ICMP."
    count_setting = "tcp_count"
    fields = ("port",)
    default_port = 443

    async def run(self, series, s):
        async def measure(x):
            cnt = self.count(x, s)
            if not x.address:
                return x.key, to_result([None] * cnt)
            port = x.target.get("port") or 443
            vals = await spaced(cnt, s["probe_spacing_ms"] / 1000,
                                lambda: connect_once(x.address, port, s["timeout_ms"] / 1000))
            return x.key, to_result(vals)

        return dict(await asyncio.gather(*[measure(x) for x in series]))
