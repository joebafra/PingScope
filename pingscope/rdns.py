"""Reverse DNS (PTR) names of traceroute hops, IPv4 and IPv6, cached so the same routers aren't looked up again.

mtr itself runs with -n (no DNS), so a slow or broken resolver never delays the traceroute; names are looked up
afterwards, a few at a time, with a short timeout.
"""
import asyncio
import socket
import time

POSITIVE_TTL = 6 * 3600   # a name found is kept this long
NEGATIVE_TTL = 3600       # "no name" (or a timeout) is remembered this long
MAX_ENTRIES = 20000

_cache = {}               # ip -> (name or "", expires)
_sem = None


def cached(ip):
    """The cached name: a string ("" = no PTR record), or None when unknown or expired."""
    e = _cache.get(ip)
    return e[0] if e and e[1] > time.time() else None


async def _one(ip, timeout):
    async with _sem:
        try:
            host, _ = await asyncio.wait_for(asyncio.get_running_loop().getnameinfo((ip, 0), socket.NI_NAMEREQD), timeout)
            name = host.rstrip(".") if host and host != ip else ""
        except (asyncio.TimeoutError, OSError, UnicodeError):
            name = ""
    _cache[ip] = (name, time.time() + (POSITIVE_TTL if name else NEGATIVE_TTL))


async def lookup(ips, timeout=2.0):
    """{ip: name or ""} for the given addresses; unknown ones are looked up (at most 8 at a time)."""
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(8)
    todo = {ip for ip in ips if ip and cached(ip) is None}
    if todo:
        await asyncio.gather(*[_one(ip, timeout) for ip in todo])
    if len(_cache) > MAX_ENTRIES:  # drop expired entries, then the oldest
        now = time.time()
        for ip in [ip for ip, (_, exp) in _cache.items() if exp <= now]:
            del _cache[ip]
        for ip in sorted(_cache, key=lambda k: _cache[k][1])[:len(_cache) - MAX_ENTRIES]:
            del _cache[ip]
    return {ip: cached(ip) or "" for ip in ips}
