"""PingScope command line.

  python -m pingscope                       start the server
  python -m pingscope create-admin          create an admin account
  python -m pingscope import FILE [--replace] [--no-known-ipv6] [--site-title]
  python -m pingscope export [FILE]
  python -m pingscope backup FILE         consistent copy of the database (safe while running)
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

    elif args.cmd == "export":
        from . import importer
        data = json.dumps(importer.export(store), indent=2)
        if args.file:
            with open(args.file, "w", encoding="utf-8") as f:
                f.write(data)
            print(f"Exported to {args.file}")
        else:
            print(data)


if __name__ == "__main__":
    main()
