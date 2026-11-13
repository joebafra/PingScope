# Changelog

All notable changes to PingScope, newest first. Versions are numbered 0.010, 0.011, 0.012 and so on. A release is a Git tag (`v0.011`) and a Docker image tag (`joebafra/pingscope:0.011`); `latest` always follows the `main` branch. The commit after the date is the one shown next to the version in the dashboard footer and under *Admin → Settings → Status*.

## 0.024 — 2026-11-13 · [d0cc404](https://github.com/joebafra/PingScope/commit/d0cc404)

### Fixed

- **The dashboard loaded slower since 0.023.** The packet loss of the last hour (for *Worst first*) was computed every round with a query that read all stored measurements instead of only the last hour, while holding the database lock, so pages and graphs had to wait. It now uses the time index: on 14 days of data (3 million rounds) 8 ms instead of 227 ms with everything in memory, and much more on a server where the database doesn't fit in memory. The same fix speeds up loading the last values at startup.

## 0.023 — 2026-11-12 · [c9fd3d2](https://github.com/joebafra/PingScope/commit/c9fd3d2)

### Added

- **Pause or maintenance per target** (*Pause…* in the target list, also for a selection): for an hour, 4 hours, a day or until you resume. *Pause* stops measuring, *maintenance* keeps measuring without alerts; both end by themselves and show on the dashboard.
- **Webhook alerts** next to Telegram, Pushover and email: generic JSON, or ready for Slack/Mattermost/Rocket.Chat/Google Chat (`text`) and Discord (`content`), with a switch per group and in the test alert. The URL can also come from `WEBHOOK_URL`.
- **DNS attempts per query**, like dig: an unanswered query is sent again (up to 3 times) and the answered attempt's time counts. Default 1; per target under *Probe settings*. The SmokePing import gives DNS targets 3 attempts of 5000 ms, as SmokePing's DNS probe (dig with its defaults) does, which explains why SmokePing showed less DNS loss.
- **Probe type filter** (ping, DNS, TCP) next to the search box on the dashboard.
- **"Since your last visit"**: logged-in users get a note with the problems since they last had the dashboard open.
- Saved secrets (API tokens, the webhook URL) can be removed in *Settings*.

### Changed

- **The alert history records problems of every group**, also without notifications (outages, and packet loss from *History: record packet loss from*, default 10%). Messages still only go out for groups with notifications on, and "resolved" and reminders only for alerts whose start was sent.
- **Worst first** also sorts by the loss over the last hour and then by latency, so the order means something when everything is fine right now. Paused targets go last.

### Existing installations

- The database gets a few new columns on the first start (DNS attempts and pause per target, webhook switch per group); nothing else changes in the data. `deploy.sh` makes a backup before updating, as always.
- Measurements stay the same: 1 DNS attempt unless you change it, no target is paused, the webhook is off until you enter a URL.
- The *History* page and the *History* count in the menu now also show problems of groups without notifications. No extra messages are sent.

## 0.022 — 2026-11-12 · [8ca1aaa](https://github.com/joebafra/PingScope/commit/8ca1aaa)

### Changed

- **One alert line per host.** A host that is measured twice, such as a ping target and a DNS target for the same name server, or the same host in two groups, gave two nearly identical lines in one message (in a contact's chat, without group names, they looked like a duplicate). It is now one line with the worst state and both measurements, for example "packet loss 40% (threshold 10%) · DNS query 40%, ping 10%".
- A target that goes down right away is listed first in the message, before packet loss.

## 0.021 — 2026-11-12 · [20ebb2b](https://github.com/joebafra/PingScope/commit/20ebb2b)

### Fixed

- **A DNS hiccup no longer shows as an outage.** When the lookup of a hostname target failed once (a timeout or a server failure of the DNS server PingScope uses), its address was forgotten and the round was recorded as 100% packet loss. Thanks to the users who analysed this.

### Changed

- **DNS problems of hostname targets:** when the A/AAAA lookup fails (the record is gone, or the DNS server times out or fails), the **last known address is measured for 24 hours**. The target stays up as long as it answers, shows a purple **DNS** badge and an explanation on its graphs ("DNS: lookup timed out, since 08:22. Measuring the last known address until …"), counts as a problem on the dashboard, and gets a **DNS alert** after the usual number of rounds, with a message when DNS works again. After 24 hours without a working lookup it is shown as **down**, with a down alert. A failing lookup is retried every round.
- The last known address and an ongoing DNS problem are kept in the database, so a restart during a DNS problem keeps measuring.
- Alert history: DNS alerts have their own label; down alerts of a hostname that doesn't resolve say why.

## 0.020 — 2026-11-11 · [6347f36](https://github.com/joebafra/PingScope/commit/6347f36)

### Added

- **Export with measurement history.** *Admin → Import & export → Export → Include measurement history* downloads the groups, targets and settings together with the measurements behind the graphs (per round for the last 14 days, hourly summaries for older data), the traceroutes and the finished alerts, as one compressed file (`.jsonl.gz`, about half the size of the database). Choose that file under *Import* on another PingScope server, or on this one, and the history comes along: targets get new ids and the history follows them. Measurements that already exist are kept, so importing the same file twice adds nothing. Users and API tokens are not part of an export.
- Command line: `python -m pingscope export --history FILE.jsonl.gz`, and `import` reads such files too.

## 0.019 — 2026-11-10 · [a95589f](https://github.com/joebafra/PingScope/commit/a95589f)

### Changed

- **`install.sh` asks for the port** PingScope listens on (8080 by default) and uses it everywhere: `.env`, the firewall, the health check and the printed address. It warns when a port is already in use on the server, names the program that uses it, and suggests a free one. Useful when a UniFi controller already listens on 8080. A port in `.env` is kept when you run the installer again, and `sudo PINGSCOPE_PORT=9090 ./install.sh` sets it in advance. Before, the installer always wrote 8080.

### Docs

- Troubleshooting for a port that's already in use, also for the Docker Hub image (`-e PINGSCOPE_PORT=9090`).

## 0.018 — 2026-11-10 · [566a571](https://github.com/joebafra/PingScope/commit/566a571)

### Changed

- The commit next to the version in the footer now links to this changelog, and *Admin → Settings → Status* links to it too.
- Every version in this changelog shows its commit after the date, the same commit the footer shows. Builds (Docker image, `deploy.sh`, `install.sh`) use the last commit that changes more than `CHANGELOG.md`, so adding a version's hash here doesn't change the hash the running version shows.

## 0.017 — 2026-11-10 · [a9c3fac](https://github.com/joebafra/PingScope/commit/a9c3fac)

### Changed

- **The read-only API only answers the dashboard.** `/api/tree`, `/api/series`, `/api/mtr` and `/api/history` now answer only the dashboard in the browser (recognised by its `X-PingScope` header on a same-origin request) and logged-in users. Robots, scanners and scripts get `404 Not Found`, as if there were no API. **Using the API from your own scripts?** Turn on *Admin → Settings → Open API for scripts* (new, off by default), or log in with a Viewer account. `/api/session` stays open, because the install and update scripts use it as a health check.

### Added

- `/robots.txt` that keeps crawlers away from `/api/` and `/admin`.

## 0.016 — 2026-11-10 · [54f6812](https://github.com/joebafra/PingScope/commit/54f6812)

### Added

- **Show icons** setting (*Admin → Settings → Site*, on by default). Off removes the small line icons from the dashboard and the admin panel and shows text only: a hidden group reads "Core network (hidden)", a target without alerts "(no alerts)". Icon-only buttons (copy, light/dark) stay.

## 0.015 — 2026-11-10 · [896a9e6](https://github.com/joebafra/PingScope/commit/896a9e6)

### Changed

- **No more emoji in the interface.** The lock, bell, globe, folder and magnifier are now small monochrome line icons in the text colour, so they look the same on every system and fit the rest of the design. Notification messages (Telegram, Pushover, email) are unchanged.
- *Public dashboard* setting: the help text says that the read-only API follows it, and that turning it off requires a login for the dashboard and the API.

### Fixed

- **SmokePing import of custom probes** such as `FPingFast` or `FPing4` whose `*** Probes ***` section wasn't pasted (often it is in a separate `@include` file): the probe type is recognised by its name (`FPing…` → ping, `FPing6…` → IPv6 ping, `DNS…` → DNS, `TCPPing…` → TCP) instead of always becoming ping, and there is one warning per probe with the number of targets, plus advice to paste the Probes section to keep its settings. Previously every target gave its own "not supported" warning.
- SmokePing import: `@include` lines give a clear warning to paste the contents of that file instead of "not understood".

### Docs

- README: how the collector runs in parallel (asyncio, fping and mtr as separate processes), and which SmokePing settings (`forks`, `concurrentprobes`) aren't needed.

## 0.014 — 2026-11-10 · [4003309](https://github.com/joebafra/PingScope/commit/4003309)

### Fixed

- **Public API: only the fields the dashboard needs.** Visitors (not logged in) got the new *measure every* settings of groups in `/api/tree`, because visitor data was filtered with a block-list. It is now an allow-list (id, name, parent, order), so new settings can't leak this way again. Thanks to the user who reported it.
- `/api/session` tells only logged-in users the server's time zone.

### Docs

- README: the read-only API is what the public dashboard shows; turn *Public dashboard* off to require a login.

## 0.013 — 2026-11-09 · [9747efc](https://github.com/joebafra/PingScope/commit/9747efc)

### Added

- **Measure every N rounds**, per group (inherited by subgroups) and per target: every round as before, or every 2, 3, 5, 10, 15, 30 or 60 rounds. For example a gateway every minute and a website every 5 minutes. Graphs, statistics and alerts follow the target's own rounds, and the probe line shows its interval.
- SmokePing import: a probe `step` that is a multiple of 60 s (such as `step = 300`) becomes *measure every 5 rounds* on its targets.

### Changed

- Admin target list: status reads "IPv4 · 6.30 ms" instead of "v4 6.30 ms".

## 0.012 — 2026-11-09 · [b633d81](https://github.com/joebafra/PingScope/commit/b633d81)

### Added

- **Reverse DNS in traceroutes**: the hostname (PTR record) of every hop is shown after its address, for IPv4 and IPv6. mtr still runs without DNS (`-n`), names are looked up afterwards with a short timeout and a cache. Visitors only see a name where they also see the address. Can be turned off under *Settings → Traceroutes → Show reverse DNS names*.

## 0.011 — 2026-11-08 · [059e1de](https://github.com/joebafra/PingScope/commit/059e1de)

### Added

- **Per-target probe settings** (*Edit target → Probe settings*): probes per round, spacing, timeout and ping packet size. Empty uses the setting from *Admin → Settings → Measurements*.
- **When targets are measured**: together at the start of each round (default), or spread over the round so not everything is sent at once (like SmokePing's `offset = random`). Set for all targets, overridable per target.
- **Ping packet size** setting (default 56 bytes, like SmokePing's `packetsize`).
- **Probe line under the graphs**, like SmokePing: *probe: 20 ICMP Echo Pings (56 Bytes) every 60 seconds*.
- **SmokePing import** reads the `*** Probes ***` section: a probe such as `++ FPingGateway` passes its `pings`, `packetsize`, `timeout`, `hostinterval` and `offset = random` on to the targets that use it.
- **More periods**: last hour, 6 hours and 12 hours. Choose which periods are offered in *Settings → Periods*. A target page shows all periods; with one IP family selected they sit in two columns, like SmokePing's target page.
- **Time zone setting**: times in each visitor's own time zone, or the server's time zone for everyone. Fixes graphs that looked an hour off in browsers that report UTC for privacy.
- **Pushover notifications** next to Telegram and email, with a switch per group.
- **Version and commit** of the running build next to "Powered by PingScope" and under *Admin → Settings → Status*.
- **Light / dark / system switch** in the header of the dashboard and the admin panel.
- **Show IP addresses to visitors**, per group (off by default), with an opt-out per target.
- **Copy button** next to addresses on the dashboard.
- **Docker image on Docker Hub** ([`joebafra/pingscope`](https://hub.docker.com/r/joebafra/pingscope)) for amd64 and arm64, built automatically from `main`.

### Changed

- **Graph axes and grid**, like SmokePing: round Y steps (1, 2 or 5 × 10ⁿ ms) with as many lines as fit, time ticks on round times (00:00, Mondays, the 1st of a month) and vertical grid lines.
- **Median line**, like SmokePing: coloured levels per measurement with thin grey steps between them, instead of separate thick blocks.
- Group cards with one graph show IPv4/IPv6 and the current value in the title row; more space under the group subtitle.
- Version numbering: 0.011 instead of 1.0.0-beta.

### Fixed

- The "Send test alert" button text ran outside its box.
- The Docker image no longer contains build provenance with the e-mail address of the person who pushed.

## 0.010 — 2026-11-06 · [c27f4ca](https://github.com/joebafra/PingScope/commit/c27f4ca)

First public release (published as `v1.0.0-beta`).

- Ping (fping), DNS and TCP probes, IPv4 and IPv6 side by side.
- SmokePing-style graphs with a synced crosshair, drag-to-zoom and click-for-traceroute.
- mtr traceroutes, every minute during problems.
- Alerts by Telegram and email per group, with a preview, extra chats for customers and an alert history.
- Admin panel with nested groups, hidden groups, Admin and Viewer accounts, SmokePing import and JSON export.
- Public dashboard without hostnames or IP addresses; Modern and Classic styles.
- One-command install with Docker Compose, Let's Encrypt HTTPS and automatic updates with backup and rollback.
