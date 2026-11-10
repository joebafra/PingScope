# Changelog

All notable changes to PingScope, newest first. Versions are numbered 0.010, 0.011, 0.012 and so on. A release is a Git tag (`v0.011`) and a Docker image tag (`joebafra/pingscope:0.011`); `latest` always follows the `main` branch.

## 0.015 — 2026-11-10

### Changed

- **No more emoji in the interface.** The lock, bell, globe, folder and magnifier are now small monochrome line icons in the text colour, so they look the same on every system and fit the rest of the design. Notification messages (Telegram, Pushover, email) are unchanged.
- *Public dashboard* setting: the help text says that the read-only API follows it, and that turning it off requires a login for the dashboard and the API.

### Fixed

- **SmokePing import of custom probes** such as `FPingFast` or `FPing4` whose `*** Probes ***` section wasn't pasted (often it is in a separate `@include` file): the probe type is recognised by its name (`FPing…` → ping, `FPing6…` → IPv6 ping, `DNS…` → DNS, `TCPPing…` → TCP) instead of always becoming ping, and there is one warning per probe with the number of targets, plus advice to paste the Probes section to keep its settings. Previously every target gave its own "not supported" warning.
- SmokePing import: `@include` lines give a clear warning to paste the contents of that file instead of "not understood".

### Docs

- README: how the collector runs in parallel (asyncio, fping and mtr as separate processes), and which SmokePing settings (`forks`, `concurrentprobes`) aren't needed.

## 0.014 — 2026-11-10

### Fixed

- **Public API: only the fields the dashboard needs.** Visitors (not logged in) got the new *measure every* settings of groups in `/api/tree`, because visitor data was filtered with a block-list. It is now an allow-list (id, name, parent, order), so new settings can't leak this way again. Thanks to the user who reported it.
- `/api/session` tells only logged-in users the server's time zone.

### Docs

- README: the read-only API is what the public dashboard shows; turn *Public dashboard* off to require a login.

## 0.013 — 2026-11-09

### Added

- **Measure every N rounds**, per group (inherited by subgroups) and per target: every round as before, or every 2, 3, 5, 10, 15, 30 or 60 rounds. For example a gateway every minute and a website every 5 minutes. Graphs, statistics and alerts follow the target's own rounds, and the probe line shows its interval.
- SmokePing import: a probe `step` that is a multiple of 60 s (such as `step = 300`) becomes *measure every 5 rounds* on its targets.

### Changed

- Admin target list: status reads "IPv4 · 6.30 ms" instead of "v4 6.30 ms".

## 0.012 — 2026-11-09

### Added

- **Reverse DNS in traceroutes**: the hostname (PTR record) of every hop is shown after its address, for IPv4 and IPv6. mtr still runs without DNS (`-n`), names are looked up afterwards with a short timeout and a cache. Visitors only see a name where they also see the address. Can be turned off under *Settings → Traceroutes → Show reverse DNS names*.

## 0.011 — 2026-11-08

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

## 0.010 — 2026-11-06

First public release (published as `v1.0.0-beta`).

- Ping (fping), DNS and TCP probes, IPv4 and IPv6 side by side.
- SmokePing-style graphs with a synced crosshair, drag-to-zoom and click-for-traceroute.
- mtr traceroutes, every minute during problems.
- Alerts by Telegram and email per group, with a preview, extra chats for customers and an alert history.
- Admin panel with nested groups, hidden groups, Admin and Viewer accounts, SmokePing import and JSON export.
- Public dashboard without hostnames or IP addresses; Modern and Classic styles.
- One-command install with Docker Compose, Let's Encrypt HTTPS and automatic updates with backup and rollback.
