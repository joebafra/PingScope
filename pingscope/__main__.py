"""PingScope command line.

  python -m pingscope                       start the server
  python -m pingscope create-admin          create an admin account
  python -m pingscope import FILE [--replace] [--no-known-ipv6] [--site-title]
  python -m pingscope export [FILE]
  python -m pingscope backup FILE         consistent copy of the database (safe while running)
  python -m pingscope ping-test [--family 6] [--only NAME] [--solo] [--rounds 3]
                                          run fping exactly like a measurement round and show
                                          which ping of each round was lost (diagnostics)
"""
import argparse
import getpass
import json
import logging
import os
import sys


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="pingscope", description="Latency and packet-loss monitoring for IPv4 and IPv6.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="start the web server and measurements (default)")
    sub.add_parser("create-admin", help="create an admin account")
    imp = sub.add_parser("import", help="import a SmokePing Targets file or PingScope JSON export")
    imp.add_argument("file")
    imp.add_argument("--replace", action="store_true", help="delete all existing groups and targets first")
    imp.add_argument("--no-known-ipv6", action="store_true", help="don't add IPv6 for root servers/public DNS")
    imp.add_argument("--site-title", action="store_true", help="use the title/remark from the file")
    exp = sub.add_parser("export", help="export groups, targets and settings as JSON")
    exp.add_argument("file", nargs="?")
    bak = sub.add_parser("backup", help="write a consistent copy of the database")
    bak.add_argument("file")
    pt = sub.add_parser("ping-test", help="run fping like a measurement round and show which pings are lost")
    pt.add_argument("--family", choices=["4", "6", "both"], default="both")
    pt.add_argument("--only", help="show the per-ping pattern for targets whose name or address contains this")
    pt.add_argument("--solo", action="store_true", help="ping only the --only targets (instead of all targets at once)")
    pt.add_argument("--rounds", type=int, default=3)
    pt.add_argument("--count", type=int, help="pings per target (default: the 'Pings per round' setting)")
    args = ap.parse_args()

    if args.cmd in (None, "serve"):
        import uvicorn
        uvicorn.run("pingscope.web:app", host=os.environ.get("PINGSCOPE_LISTEN", "0.0.0.0"),
                    port=int(os.environ.get("PINGSCOPE_PORT", "8080")), proxy_headers=True,
                    forwarded_allow_ips=os.environ.get("PINGSCOPE_TRUSTED_PROXIES", "127.0.0.1"),
                    log_level="warning")
        return

    from .db import Store
    store = Store(os.environ.get("PINGSCOPE_DB", "data/pingscope.db"))

    if args.cmd == "create-admin":
        username = input("Username: ").strip()
        pw = getpass.getpass("Password (min. 10 characters): ")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords don't match")
        try:
            store.create_user(username, pw)
        except ValueError as e:
            sys.exit(str(e))
        print(f"Admin '{username}' created.")

    elif args.cmd == "import":
        from . import importer
        from .probes import PROBES
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
        tree, warnings, kind = importer.parse_any(text)
        stats = importer.prepare(tree, list(PROBES), not args.no_known_ipv6, warnings)
        added = importer.apply(store, tree, "replace" if args.replace else "merge")
        if args.site_title and tree.get("site", {}).get("title"):
            store.save_settings({"site_title": tree["site"]["title"], "site_remark": tree["site"].get("remark", "")})
        for w in warnings:
            print("warning:", w)
        print(f"Imported {added['groups']} groups and {added['targets']} targets "
              f"({added['skipped']} skipped as duplicates); {stats['known_ipv6']} known IPv6 addresses added.")

    elif args.cmd == "backup":
        import sqlite3
        dst = sqlite3.connect(args.file)
        with dst:
            store.c.backup(dst)
        dst.close()
        print(f"Database backed up to {args.file}")

    elif args.cmd == "ping-test":
        import asyncio
        asyncio.run(ping_test(store, args))

    elif args.cmd == "export":
        from . import importer
        data = json.dumps(importer.export(store), indent=2)
        if args.file:
            with open(args.file, "w", encoding="utf-8") as f:
                f.write(data)
            print(f"Exported to {args.file}")
        else:
            print(data)


async def ping_test(store, args):
    """Diagnostics: same fping command line as a measurement round, with the position of every lost ping."""
    import asyncio
    import collections
    import time
    from .probes.ping import fping_cmd
    from .model import norm_ip
    from .resolver import Resolver
    s = store.settings()
    count = args.count or s["ping_count"]
    targets = [t for t in store.targets(enabled_only=True) if t["probe"] == "ping"]
    series = await Resolver().plan(targets, 300)
    fams = [4, 6] if args.family == "both" else [int(args.family)]
    names = collections.defaultdict(set)
    for x in series:
        if x.address and x.family in fams:
            names[(x.family, x.address)].add(x.target["name"])
    q = (args.only or "").lower()
    watch = {k for k, v in names.items() if q and (q in k[1].lower() or any(q in n.lower() for n in v))}
    if args.only and not watch:
        sys.exit(f"No ping target matches '{args.only}'")
    print(f"PingScope ping test: {count} pings per target, spacing {s['probe_spacing_ms']} ms, "
          f"timeout {s['timeout_ms']} ms" + (" (only the --only targets)" if args.solo else " (all targets at once, like a real round)"))
    print("Note: this runs next to the normal measurements, so the server sends twice as many pings meanwhile.\n")
    for rnd in range(1, args.rounds + 1):
        jobs = []
        for fam in fams:
            addrs = sorted(a for (f, a) in names if f == fam and (not args.solo or (f, a) in watch))
            if addrs:
                jobs.append((fam, addrs))
        t0 = time.time()

        async def run(fam, addrs):
            proc = await asyncio.create_subprocess_exec(*fping_cmd(addrs, fam, count, s), stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
            out, err = await proc.communicate()
            res = {}
            for line in (err + out).decode(errors="replace").splitlines():
                if " : " in line:
                    host, vals = line.split(" : ", 1)
                    try:
                        res[(fam, norm_ip(host.strip()))] = vals.split()
                    except ValueError:
                        pass
            return res

        results = {}
        for r in await asyncio.gather(*[run(f, a) for f, a in jobs]):
            results.update(r)
        lost_pos = collections.Counter()
        with_loss = 0
        for key, vals in results.items():
            pos = [i + 1 for i, v in enumerate(vals) if v == "-"]
            if pos and len(pos) < len(vals):  # ignore targets that are completely down
                with_loss += 1
                lost_pos.update(pos)
        total = sum(len(v) for v in results.values())
        lost = sum(v.count("-") for v in results.values())
        print(f"Round {rnd}: {len(results)} addresses, {total} pings, {lost} lost "
              f"({lost / total * 100 if total else 0:.2f}%), {with_loss} addresses with partial loss, "
              f"took {time.time() - t0:.1f}s")
        if lost_pos:
            print("  lost pings by position in the round (1 = first ping):",
                  ", ".join(f"#{p}: {n}" for p, n in sorted(lost_pos.items())))
        for key in sorted(watch):
            vals = results.get(key)
            if vals:
                pattern = "".join("x" if v == "-" else "." for v in vals)
                print(f"  {', '.join(sorted(names[key]))} [IPv{key[0]} {key[1]}]: {pattern}  ({vals.count('-')} lost)")
        print()


if __name__ == "__main__":
    main()
