#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MCDX = ROOT / "mcdx.py"


def write_auth(path: Path, account_id: str, token: str = "token") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "id_token": f"id-{token}",
                    "access_token": f"access-{token}",
                    "refresh_token": f"refresh-{token}",
                    "account_id": account_id,
                },
                "last_refresh": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def write_fake_codex(path: Path, codex_home: Path, state: Path) -> None:
    """Stand-in for the codex CLI: device-auth login plus app-server daemon control.

    Daemon status is read from `state/daemon_status` so tests can flip it, and
    every restart is appended to `state/daemon_restarts`. Login behaviour is
    driven by `state/login_mode` (ok | fail | sigint | sigterm), and the token
    suffix by `state/login_token`.
    """
    path.write_text(
        f'''#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
from pathlib import Path

codex_home = Path({str(codex_home)!r})
state = Path({str(state)!r})
args = sys.argv[1:]


def state_or(name, default):
    f = state / name
    return f.read_text().strip() if f.exists() else default


if args[:3] == ["app-server", "daemon", "version"]:
    print(json.dumps({{"status": state_or("daemon_status", "not running")}}))
    raise SystemExit(0)

if args[:3] == ["app-server", "daemon", "restart"]:
    with (state / "daemon_restarts").open("a") as fh:
        fh.write("restart\\n")
    print(json.dumps({{"status": "restarted"}}))
    raise SystemExit(0)

if args[:1] == ["login"]:
    mode = state_or("login_mode", "ok")
    if mode == "fail":
        print("fake codex: device auth denied", file=sys.stderr)
        raise SystemExit(1)
    if mode in ("sigint", "sigterm"):
        # Stand in for Ctrl+C / a closing terminal killing mcdx mid-login.
        os.kill(os.getppid(), signal.SIGINT if mode == "sigint" else signal.SIGTERM)
        time.sleep(30)
        raise SystemExit(0)
    token = state_or("login_token", "new")
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text(json.dumps({{
        "auth_mode": "chatgpt",
        "tokens": {{"id_token": "id-" + token, "access_token": "access-" + token,
                   "refresh_token": "refresh-" + token, "account_id": "acct-new"}},
    }}))
    raise SystemExit(0)

print(f"fake codex: unexpected args {{args}}", file=sys.stderr)
raise SystemExit(64)
''',
        encoding="utf-8",
    )
    path.chmod(0o755)


def run(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        [sys.executable, str(MCDX), *args],
        env=env,
        stdin=subprocess.DEVNULL,  # nothing here should ever block on a prompt
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and cp.returncode != 0:
        raise AssertionError(f"command failed: {args}\nstdout={cp.stdout}\nstderr={cp.stderr}")
    return cp


def restarts(state: Path) -> list[str]:
    log = state / "daemon_restarts"
    return log.read_text().splitlines() if log.exists() else []


def account_of(path: Path) -> str:
    return json.loads(path.read_text())["tokens"]["account_id"]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mcdx-test-") as td:
        base = Path(td)
        codex_home = base / "codex"
        data_home = base / "data"
        auth = codex_home / "auth.json"
        state = base / "fake-state"
        state.mkdir()
        # The whole suite runs against the fake, so it never touches the real
        # codex CLI -- or the real app-server daemon holding your credentials.
        env = os.environ.copy()
        env["MCDX_CODEX_HOME"] = str(codex_home)
        env["MCDX_DATA_HOME"] = str(data_home)
        env["MCDX_CODEX_BIN"] = str(base / "fake-codex")
        write_fake_codex(base / "fake-codex", codex_home, state)

        write_auth(auth, "acct-main", "main")
        run(env, "save-current", "main", "-y")
        assert "main" in run(env, "list").stdout
        assert "main" in run(env, "current").stdout

        write_auth(auth, "acct-alt", "alt")
        run(env, "save-current", "alt", "-y")

        # No daemon running: switching is a plain file swap, no restart needed.
        run(env, "switch", "main")
        assert account_of(auth) == "acct-main"
        assert (data_home / "profiles" / "_last" / "auth.json").exists()
        assert "current: main" in run(env, "current").stdout
        assert restarts(state) == []

        # A running daemon still serves the previous account, so mcdx asks.
        (state / "daemon_status").write_text("running")
        asked = run(env, "switch", "alt")
        assert account_of(auth) == "acct-alt"  # the file swap still happens
        assert "still serving the previous account" in asked.stderr
        assert restarts(state) == []

        # -y answers that prompt and reloads the daemon.
        run(env, "switch", "main", "-y")
        assert restarts(state) == ["restart"]

        # --no-daemon-restart is the explicit opt-out.
        run(env, "switch", "alt", "-y", "--no-daemon-restart")
        assert restarts(state) == ["restart"]

        # Switching to the profile that is already active must still be able to
        # reload a daemon that is stuck on the previous account.
        already = run(env, "switch", "alt", "-y")
        assert "already using 'alt'" in already.stdout
        assert restarts(state) == ["restart", "restart"]

        run(env, "rename", "alt", "renamed-alt")
        assert "renamed-alt" in run(env, "list").stdout
        assert not (data_home / "profiles" / "alt").exists()
        renamed_meta = json.loads((data_home / "profiles" / "renamed-alt" / "metadata.json").read_text())
        assert renamed_meta["name"] == "renamed-alt"
        run(env, "switch", "renamed-alt", "-y")
        assert account_of(auth) == "acct-alt"
        assert "current: renamed-alt" in run(env, "current").stdout
        list_output = run(env, "list").stdout
        assert "* renamed-alt" in list_output
        assert "* _last" not in list_output
        conflict = run(env, "rename", "renamed-alt", "main", check=False)
        assert conflict.returncode == 1
        assert "profile already exists" in conflict.stderr

        duplicate = run(env, "save-current", "dupe", check=False)
        assert duplicate.returncode == 2
        assert "same credential already exists" in duplicate.stderr

        # add logs in via device auth and must also pick the new login up.
        before = len(restarts(state))
        run(env, "add", "new", "-y")
        assert "new" in run(env, "current").stdout
        assert len(restarts(state)) == before + 1
        assert "daemon:     running" in run(env, "doctor").stdout

        # An unusable codex binary must not turn a working switch into a failure.
        (state / "daemon_status").write_text("not running")
        broken = os.environ.copy()
        broken.update(env)
        broken["MCDX_CODEX_BIN"] = str(base / "missing-codex")
        unreachable = run(broken, "switch", "main", "-y")
        assert account_of(auth) == "acct-main"
        assert "could not query the Codex app-server daemon" in unreachable.stderr

        # `use` is an alias for switch.
        used = run(env, "use", "new")
        assert account_of(auth) == "acct-new"
        assert "switched to 'new'" in used.stdout

        # update re-logs in and replaces the profile's credentials in place.
        (state / "login_token").write_text("refreshed")
        (state / "daemon_status").write_text("running")
        restarts_before = len(restarts(state))
        refreshed = run(env, "update", "new", "-y")
        assert "updated profile 'new'" in refreshed.stdout
        assert len(restarts(state)) == restarts_before + 1
        (state / "daemon_status").write_text("not running")
        profile_new_auth = data_home / "profiles" / "new" / "auth.json"
        assert json.loads(profile_new_auth.read_text())["tokens"]["id_token"] == "id-refreshed"
        assert json.loads(auth.read_text())["tokens"]["id_token"] == "id-refreshed"
        assert not (data_home / "in-progress.json").exists()
        assert not (data_home / "in-progress-auth.json").exists()

        # A failed login leaves both the profile and the active auth alone.
        (state / "login_mode").write_text("fail")
        failed = run(env, "update", "new", check=False)
        assert failed.returncode == 1, failed
        assert "previous auth restored" in failed.stderr
        assert json.loads(profile_new_auth.read_text())["tokens"]["id_token"] == "id-refreshed"
        assert account_of(auth) == "acct-new"
        assert not (data_home / "in-progress.json").exists()

        # Ctrl+C during the web login rolls the swap back and exits 130.
        (state / "login_mode").write_text("sigint")
        interrupted = run(env, "update", "new", check=False)
        assert interrupted.returncode == 130, interrupted
        assert "no profile was changed" in interrupted.stderr
        assert json.loads(profile_new_auth.read_text())["tokens"]["id_token"] == "id-refreshed"
        assert account_of(auth) == "acct-new"
        assert not (data_home / "in-progress.json").exists()

        # A closing terminal (SIGHUP/SIGTERM) takes the same rollback path.
        (state / "login_mode").write_text("sigterm")
        terminated = run(env, "update", "new", check=False)
        assert terminated.returncode == 143, terminated
        assert account_of(auth) == "acct-new"
        assert not (data_home / "in-progress.json").exists()

        # A hard kill cannot roll back in-process; the journal does it next run.
        (state / "login_mode").write_text("ok")
        pending = data_home / "in-progress-auth.json"
        auth.unlink()
        pending.write_text(json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {"id_token": "id-main", "access_token": "access-main",
                       "refresh_token": "refresh-main", "account_id": "acct-main"},
        }))
        (data_home / "in-progress.json").write_text(json.dumps({"operation": "update", "name": "new"}))
        recovered = run(env, "list")
        assert "recovered from an interrupted" in recovered.stderr
        assert "update 'new'" in recovered.stderr
        assert account_of(auth) == "acct-main"
        assert not (data_home / "in-progress.json").exists()
        assert not pending.exists()

    print("tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
