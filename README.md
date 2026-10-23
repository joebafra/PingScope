# PingScope

Latency and packet-loss monitoring for **IPv4 and IPv6**, in the spirit of SmokePing, with a web-based admin panel.

- **Probes:** ping (fping), DNS query time and TCP connect time. Each probe type is a small plugin.
- **IPv4 and IPv6 side by side:**
  - Hostnames are resolved for A and AAAA records.
  - An IPv4 target can be given an extra IPv6 address.
  - The overview shows how many targets are measured over IPv6.
- **SmokePing-style graphs:**
  - Grey "smoke" shows the spread of all probes.
  - The median line changes colour with packet loss.
  - Ranges: 3 hours, day, week, month, year.
- **mtr traceroutes:**
  - Every 15 minutes normally, every minute while a target has packet loss or is down.
  - Click a time, or a point in a graph, to open the hop-by-hop report.
- **Admin panel:**
  - Nested groups and targets, bulk actions, drag to move.
  - "Test now" for a target, IPv6 suggestions.
  - SmokePing import, JSON export/import.
  - Settings and users.
- **Private by default for visitors:**
  - The public dashboard never shows hostnames, IP addresses or DNS query names.
  - Traceroute hops are hidden from visitors unless you enable them.
  - Logged-in admins see everything.
- **Your own branding:** site title and welcome text are set in **Admin → Settings**.
- **HTTPS out of the box:**
  - Caddy gets a Let's Encrypt certificate for your own hostname.
  - It redirects HTTP to HTTPS and renews the certificate automatically.

## Install

```bash
git clone https://github.com/internetcreators/PingScope.git /opt/pingscope
cd /opt/pingscope
sudo ./install.sh
```

The installer asks for your hostname and email, sets up Docker, HTTPS and the firewall, and prints a one-time setup code for the first admin account. See **[docs/INSTALL.md](docs/INSTALL.md)** for the full guide, installing without Docker, updates, backups and troubleshooting.

## Built with

| Part | Technology |
|---|---|
| Backend | Python 3.12, [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/), asyncio |
| Storage | SQLite (single file, WAL mode); per-round data plus hourly summaries |
| Measurements | [fping](https://fping.org/) for ICMP, built-in async DNS and TCP probes, [mtr](https://github.com/traviscross/mtr) for traceroutes |
| Frontend | Plain HTML, CSS and JavaScript with canvas graphs; no build step, no external CDN |
| HTTPS | [Caddy](https://caddyserver.com/) with automatic Let's Encrypt certificates |
| Deployment | Docker Compose (or systemd without Docker) |

## Importing from SmokePing

**Admin → Import & export:** paste your `Targets` section (or upload the file), click **Preview**, then **Import**. From the command line:

```bash
docker exec -i pingscope python -m pingscope import /dev/stdin < Targets
```

What the importer does:

- Nested groups (`+`, `++`, `+++`) become groups and subgroups, using `title` as the name.
- `probe` is inherited like in SmokePing:
  - `FPing` → ping
  - `FPing6` → ping over IPv6 only
  - `DNS` → DNS (with `lookup` and `server`)
  - `TCPPing` → TCP
- Root servers and the large public resolvers get their IPv6 addresses added automatically.
- The preview points out outdated addresses and duplicate targets.

Afterwards, **Admin → IPv6** looks up AAAA records for targets that are still IPv4-only. You review each suggestion and apply the ones you want.

Visitors do see target **names**. If a name contains an address (for example "Google 8.8.8.8"), rename the target if that address should stay private.

## Configuration

Settings are managed in **Admin → Settings** and apply without a restart. They cover:

- the site title and welcome text, and whether the dashboard is public;
- the measurement interval, probes per round and timeouts;
- data retention;
- mtr intervals and the loss threshold.

Server settings (`.env`, written by `install.sh`):

| Variable | Default | Purpose |
|---|---|---|
| `PINGSCOPE_DOMAIN` | | Your hostname(s) for HTTPS, comma-separated |
| `ACME_EMAIL` | | Email for Let's Encrypt notices |
| `COMPOSE_PROFILES` | `https` | `https` starts Caddy; empty runs without HTTPS |
| `PINGSCOPE_LISTEN` | `127.0.0.1` | Listen address (`0.0.0.0` without HTTPS) |
| `PINGSCOPE_PORT` | `8080` | Internal port |
| `PINGSCOPE_TRUSTED_PROXIES` | `127.0.0.1` | Reverse proxies whose `X-Forwarded-*` headers are trusted |
| `PINGSCOPE_DB` | `/data/pingscope.db` | Database file |

## How data is stored

- Each round (every minute by default), each series stores all probe round-trip times. This full-resolution data is kept for 14 days (configurable).
- Older data is summarised per hour (20 quantiles plus loss) and kept indefinitely.
- The 3-hour, day and week graphs use full-resolution data; month and year use the hourly summaries.
- mtr history is kept for 30 days (configurable).

## API

These endpoints are read-only. They are public when the dashboard is public, and visitors get the address-free view.

- `GET /api/tree`: groups, targets, current values
- `GET /api/series?key=<target id>-<4|6>&range=3h|1d|7d|30d|1y`: graph data
- `GET /api/mtr?key=<target id>-<4|6>[&before=ts][&at=ts][&only_issues=true]`: traceroute history

## Adding a probe type

1. Create `pingscope/probes/myprobe.py`:
   - Define a class that extends `Probe`.
   - Set `name`, `label`, `description` and `count_setting`.
   - Implement `async def run(self, series, settings)`. It returns `{series.key: Result(sent, lost, [rtt_ms, ...])}`.
2. Decorate the class with `@register` and add the module to the import line in `pingscope/probes/__init__.py`.

The new type appears in the admin panel automatically. `dns.py` and `tcp.py` are short examples.

## CLI

```
python -m pingscope                                 start server
python -m pingscope create-admin                    create an admin account
python -m pingscope import FILE [--replace] [--no-known-ipv6] [--site-title]
python -m pingscope export [FILE]                   JSON export of groups, targets and settings
python -m pingscope backup FILE                     consistent copy of the database
```
