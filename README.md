# mcdx — switch between Codex auth profiles

`mcdx` is a small CLI for switching between saved
[OpenAI Codex](https://github.com/openai/codex) login credentials, so you can
use personal and work accounts on the same machine.

It manages only:

```text
~/.codex/auth.json
```

It does not copy `config.toml`, sessions, history, SQLite state, plugins, skills,
or temporary runtime files. Runtime state and `config.toml` stay owned by Codex
itself — after a switch, `mcdx` asks Codex to restart its app-server daemon so
the new credentials are actually picked up.

## Requirements

- Python 3.10+
- The `codex` CLI on your `PATH` (used by `mcdx add`, and to reload the
  app-server daemon after `add`/`switch`)

## Install

```bash
# Recommended: pipx (isolated environment, mcdx lands on your PATH)
pipx install mcdx

# Or plain pip
pip install --user mcdx
```

Published on [PyPI](https://pypi.org/project/mcdx/).

### Install from a clone

```bash
git clone https://github.com/zackaryjing/multi-account-codex
mkdir -p ~/.local/bin
ln -sf "$PWD/multi-account-codex/mcdx.py" ~/.local/bin/mcdx
chmod +x multi-account-codex/mcdx.py
```

Make sure `~/.local/bin` is on your `PATH`.

## Usage

```bash
mcdx save-current main   # save your current login as a profile
mcdx add alt             # log in with Codex device auth, saved as a profile
mcdx list                # list saved profiles (* marks the active one)
mcdx current             # show the active profile
mcdx switch main         # switch the active Codex auth
mcdx remove old-name     # delete a profile
mcdx rename a b          # rename a profile
mcdx doctor              # check paths, Codex binary and daemon status
```

`mcdx add` and `mcdx switch` accept:

| Flag                   | Effect                                                        |
| ---------------------- | ------------------------------------------------------------- |
| `-y`, `--yes`          | answer yes to prompts, including the daemon restart           |
| `--no-daemon-restart`  | leave the Codex app-server daemon running as it is            |

## Where things live

Profiles are stored under:

```text
~/.local/share/mcdx/profiles/<name>/
```

Each profile contains an `auth.json` copy and `metadata.json`. Credentials are
not encrypted — the profiles directory is created with `0700` permissions.

Environment overrides:

| Variable            | Default        | Purpose                    |
| ------------------- | -------------- | -------------------------- |
| `MCDX_CODEX_HOME`   | `~/.codex`     | Codex home directory       |
| `MCDX_DATA_HOME`    | `~/.local/share/mcdx` | mcdx data directory |
| `MCDX_CODEX_BIN`    | `codex`        | Codex binary for `add` and daemon restarts |

## Behavior

- `mcdx add <name>` temporarily moves the active auth aside, runs
  `codex login --device-auth`, saves the resulting `auth.json`, and leaves the
  new profile active.
- `mcdx switch <name>` saves the current auth as `_last` before switching.
- Duplicate credentials are detected by `account_id` and `auth.json` SHA-256.
- Codex keeps a long-lived app-server daemon that reads `auth.json` once at
  startup, and sessions talk to that daemon rather than to the file. After
  `add`/`switch`, mcdx therefore runs `codex app-server daemon restart`; without
  it the daemon keeps serving the previous account no matter what `auth.json`
  says. mcdx asks first (default yes) when it can, and never restarts a daemon
  that is not running. Restarting interrupts any codex sessions live on that
  daemon, so scripts should pass `-y` to accept that, or `--no-daemon-restart`
  and reload later.

## Development

```bash
python3 tests.py    # run the test suite
```

## License

[MIT](LICENSE)
