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
itself.

## Requirements

- Python 3.10+
- The `codex` CLI on your `PATH` (only needed for `mcdx add`)

## Install

```bash
# Recommended: pipx (isolated environment, mcdx lands on your PATH)
pipx install git+https://github.com/zackaryjing/multi-account-codex

# Or plain pip
pip install --user git+https://github.com/zackaryjing/multi-account-codex
```

*(Once the package is published to PyPI, this becomes a simple
`pipx install mcdx`.)*

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
mcdx doctor              # check paths and Codex binary
```

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
| `MCDX_CODEX_BIN`    | `codex`        | Codex binary for `add`     |

## Behavior

- `mcdx add <name>` temporarily moves the active auth aside, runs
  `codex login --device-auth`, saves the resulting `auth.json`, and leaves the
  new profile active.
- `mcdx switch <name>` saves the current auth as `_last` before switching.
- Duplicate credentials are detected by `account_id` and `auth.json` SHA-256.

## Development

```bash
python3 tests.py    # run the test suite
```

## License

[MIT](LICENSE)
