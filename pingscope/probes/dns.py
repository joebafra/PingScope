"""DNS query response time (UDP), like SmokePing's DNS probe. Any valid reply counts, including NXDOMAIN."""
import asyncio
import os
import socket
import struct
import time

from .base import Probe, register, spaced, to_result

QTYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28}


def build_query(qname, qtype):
    qid = int.from_bytes(os.urandom(2), "big")
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # recursion desired
    labels = [l for l in qname.strip(".").split(".") if l]
    body = b"".join(bytes([len(l)]) + l.encode("idna") for l in labels) + b"\x00"
    return qid, header + body + struct.pack(">HH", QTYPES.get(qtype, 1), 1)


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, qid, fut):
        self.qid, self.fut = qid, fut

    def datagram_received(self, data, addr):
        if len(data) >= 12 and int.from_bytes(data[:2], "big") == self.qid and data[2] & 0x80 and not self.fut.done():
            self.fut.set_result(time.perf_counter())

    def error_received(self, exc):
        if not self.fut.done():
            self.fut.set_exception(exc)


async def query_once(address, family, port, qname, qtype, timeout_s):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    qid, packet = build_query(qname, qtype)
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Proto(qid, fut), remote_addr=(address, port),
            family=socket.AF_INET if family == 4 else socket.AF_INET6)
    except OSError:
        return None
    try:
        t0 = time.perf_counter()
        transport.sendto(packet)
        t1 = await asyncio.wait_for(fut, timeout_s)
        return (t1 - t0) * 1000
    except (asyncio.TimeoutError, OSError):
        return None
    finally:
        transport.close()


@register
class DnsProbe(Probe):
    name = "dns"
    label = "DNS query"
    description = "Response time of a DNS server for a query (UDP). Use the server's address as host."
    count_setting = "dns_count"
    fields = ("dns_query", "dns_type", "port")
    default_port = 53

    async def run(self, series, s):
        async def measure(x):
            cnt = self.count(x, s)
            if not x.address:
                return x.key, to_result([None] * cnt)
            t = x.target
            qname = t.get("dns_query") or "."
            qtype = t.get("dns_type") or ("NS" if qname == "." else "A")
            port = t.get("port") or 53
            timeout = self.timeout_ms(x, s) / 1000
            vals = await spaced(cnt, self.spacing_ms(x, s) / 1000,
                                lambda: query_once(x.address, x.family, port, qname, qtype, timeout))
            return x.key, to_result(vals)

        return dict(await asyncio.gather(*[measure(x) for x in series]))
