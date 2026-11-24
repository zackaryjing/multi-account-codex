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
mcdx add alt -b          # same, but with the browser login flow (no device code)
mcdx list                # list saved profiles (* marks the active one)
mcdx current             # show the active profile
mcdx switch main         # switch the active Codex auth
mcdx use main            # alias for switch
mcdx update main         # log in again to refresh an expired profile
mcdx remove old-name     # delete a profile
mcdx rename a b          # rename a profile
mcdx proxy set http://127.0.0.1:10808   # proxy for codex logins and daemon restarts
mcdx daemon restart      # restart the Codex app-server daemon (picks up the proxy)
mcdx doctor              # check paths, Codex binary, proxy and daemon status
```

`mcdx add`, `mcdx update`, and `mcdx switch` accept:

| Flag                   | Effect                                                        |
| ---------------------- | ------------------------------------------------------------- |
| `-y`, `--yes`          | answer yes to prompts, including the daemon restart           |
| `-b`, `--browser`      | use the browser login flow instead of device auth (`add`/`update`) |
| `--no-daemon-restart`  | leave the Codex app-server daemon running as it is            |

## Transfer a profile to another machine

Export a saved profile on the source machine.

```bash
mcdx export main ./main.mcdx
```

Copy `main.mcdx` to the destination machine. With a version of `mcdx` that
supports profile transfers, import it and use `switch` to activate it.

```bash
mcdx import ./main.mcdx
mcdx switch main
```

Import uses the original profile name and requires no Codex login. Use
`mcdx import ./main.mcdx --name work` to choose a different name if `main`
already exists. `-y` accepts the prompt to import duplicate credentials under
a different name.

Export creates a new file with `0600` permissions. The file contains
unencrypted login credentials, so transfer it privately.

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
| `MCDX_PROXY`        | (unset)        | proxy URL for codex subprocesses, overriding the stored config |

## Proxy

On machines that can only reach `chatgpt.com` through a local proxy (e.g.
v2rayN/Clash behind mirrored WSL networking), both `codex login` and the
long-lived app-server daemon need the proxy in their environment. Configure it
once per machine:

```bash
mcdx proxy set http://127.0.0.1:10808
```

mcdx then injects `http_proxy`/`https_proxy`/`all_proxy` into every codex
subprocess it spawns:

- `mcdx add` / `mcdx update` device-auth logins
- app-server daemon restarts (after `add`/`switch`/`update`, and `mcdx daemon restart`)

Since daemons inherit their environment at spawn time, `mcdx daemon restart` is
also the recovery command when a daemon was started elsewhere without the
proxy and codex hangs on network errors.

A configured proxy replaces `http_proxy`/`https_proxy`/`all_proxy` that the
shell already exported, so the stored config always wins.

mcdx cannot affect codex processes you start yourself — launch those with the
proxy already in your shell environment (or through a wrapper that exports
it). `mcdx proxy show` displays the active value, `mcdx proxy clear` removes
it, and `MCDX_PROXY` overrides the stored config for one invocation.

## Behavior

- `mcdx add <name>` temporarily moves the active auth aside, runs
  `codex login --device-auth` (or `codex login` with `-b`, which opens a URL and
  listens on localhost for the OAuth callback), saves the resulting `auth.json`,
  and leaves the new profile active.
- `mcdx update <name>` re-runs that login flow for an existing profile and
  replaces its credentials in place, so an expired profile can be refreshed
  without `remove` + `add`. On failure the profile and the active auth are left
  untouched.
- `mcdx switch <name>` saves the current auth as `_last` before switching.
- Duplicate credentials are detected by `account_id` and `auth.json` SHA-256.
- An interrupted `add`/`update` is rolled back immediately for catchable exits
  (Ctrl+C, SIGTERM/SIGHUP from a closing terminal) and on the next `mcdx` run
  for a hard kill, via a journal under the data directory.
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
