# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Report it privately instead:

1. Go to the [Security tab](https://github.com/joebafra/PingScope/security) of this repository.
2. Click **Report a vulnerability** and describe the problem: what an attacker can do, the steps to reproduce it, and the PingScope version or commit.

You'll get a reply as soon as possible. Once a fix is released, the advisory is published with credit to you, unless you prefer otherwise.

## Supported versions

Only the latest version on the `main` branch gets security fixes. Servers with [automatic updates](docs/INSTALL.md#automatic-updates-deploy-from-github) install them within minutes.

## What counts as a vulnerability

PingScope is designed so that a visitor of a public dashboard never learns more than target names and measurements. Things we especially want to hear about:

- A way for a visitor (not logged in) to see hostnames, IP addresses, DNS query names, notification settings, Telegram chat IDs or targets in hidden groups.
- A way for a *Viewer* account to change anything, or for anyone to reach the admin API without an admin session.
- Ways to read the Telegram or Lettermint tokens, bypass the login rate limit, or take over a fresh install without the setup code.
- Command injection through target fields (hosts are passed to `fping` and `mtr`).
