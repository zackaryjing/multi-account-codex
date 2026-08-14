# multi_codex

`mcdx` is a small personal CLI for switching OpenAI Codex login credentials.

It manages only:

```text
~/.codex/auth.json
```

It does not copy `config.toml`, sessions, history, SQLite state, plugins, skills,
or temporary runtime files.

## Install

```bash
mkdir -p ~/.local/bin
ln -sf ~/allprojects/agent_project/multi_codex/mcdx.py ~/.local/bin/mcdx
chmod +x ~/allprojects/agent_project/multi_codex/mcdx.py
```

`~/.local/bin` must be in `PATH`.

## Commands

```bash
mcdx save-current main
mcdx list
mcdx current
mcdx add alt
mcdx switch main
mcdx switch alt
mcdx remove old-name
mcdx doctor
```

Profiles are stored under:

```text
~/.local/share/mcdx/profiles/<name>/
```

Each profile contains an `auth.json` copy and `metadata.json`. Credentials are
not encrypted.

## Behavior

- `mcdx add <name>` temporarily moves the active auth aside, runs
  `codex login --device-auth`, saves the resulting `auth.json`, and leaves the
  new profile active.
- `mcdx switch <name>` saves the current auth as `_last` before switching.
- Duplicate credentials are detected by `account_id` and `auth.json` SHA-256.
