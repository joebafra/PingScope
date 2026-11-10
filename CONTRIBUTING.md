# Contributing to PingScope

Thanks for your interest! Bug reports, ideas and pull requests are all welcome.

## Reporting bugs and ideas

Open an [issue](https://github.com/joebafra/PingScope/issues) with:

- what you did, what you expected and what happened instead;
- the commit you run (`sudo ./deploy.sh --status`, or `git log -1 --oneline` in `/opt/pingscope`);
- for measurement problems: the relevant lines from `docker logs pingscope`, and if it's about packet loss, the output of `python -m pingscope ping-test --only <target name>`.

Security problems go through [SECURITY.md](SECURITY.md), not a public issue.

## Running PingScope locally

PingScope needs Linux (or WSL), Python 3.12+, `fping` and `mtr`:

```bash
sudo apt install -y fping mtr-tiny python3-venv
```

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
```

```bash
PINGSCOPE_LISTEN=127.0.0.1 venv/bin/python -m pingscope
```

Open <http://127.0.0.1:8080/admin> and use the setup code from the log. The database lives in `data/pingscope.db`; delete it to start over. You can load the example config with:

```bash
venv/bin/python -m pingscope import examples/smokeping-example.txt
```

Or run the full stack with Docker: `docker compose up -d --build`.

## Guidelines

- **Keep it small.** The only Python dependencies are FastAPI and Uvicorn, and the frontend is plain HTML, CSS and JavaScript without a build step or external scripts. Please keep it that way; a new dependency needs a very good reason.
- **Privacy for visitors is a hard rule.** Anything sent to a visitor who is not logged in must not contain DNS query names, notification settings or data of hidden groups, and hostnames and IP addresses only for targets whose group shows them (`_shows_address` in `pingscope/web.py`). When you add a field to a public endpoint, check the visitor view.
- **Database changes** are migrations in `Store.__init__` (`pingscope/db.py`): add columns with `ALTER TABLE ... ADD COLUMN` and a default, so existing installs upgrade automatically on start.
- **Settings** are declared once in `pingscope/settings.py` (default, type, range and help text); the admin panel builds its form from that schema.
- **New probe types** are a module in `pingscope/probes/`; see [Adding a probe type](README.md#adding-a-probe-type).
- **UI text** is in English and explains what a setting does in plain words.
- **Commits** have a short subject that says what changed for the user, and a body that explains why.

## Versions and the changelog

Versions are numbered 0.010, 0.011, 0.012 and so on. For a new version:

1. Set `__version__` in `pingscope/__init__.py`.
2. Add a section at the top of [CHANGELOG.md](CHANGELOG.md): *Added*, *Changed*, *Fixed*.
3. After merging to `main`, publish a GitHub release with tag `v0.012`. The Docker image then also gets the tag `0.012`.

## Before you open a pull request

There is no automated test suite yet, so please check by hand:

1. `python -m py_compile pingscope/*.py pingscope/probes/*.py` passes.
2. The server starts and a measurement round runs (`Round HH:MM: N series` in the log).
3. The dashboard works both logged out and logged in, in the *Modern* and *Classic* style, and on a narrow (phone) screen.
4. If you changed an endpoint: the visitor view still contains no addresses (`curl -s -H 'X-PingScope: 1' http://127.0.0.1:8080/api/tree` while logged out).
5. If you changed `install.sh` or `deploy.sh`: `bash -n install.sh && bash -n deploy.sh`.

Mention in the pull request what you tested. By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
