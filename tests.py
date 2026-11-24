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


def note_proxy():
    # Record which proxy came in through the environment, so tests can assert
    # mcdx injected the configured proxy into this subprocess.
    proxy = os.environ.get("https_proxy") or os.environ.get("all_proxy") or ""
    with (state / "proxy_seen").open("a") as fh:
        fh.write(proxy + "\\n")


if args[:3] == ["app-server", "daemon", "version"]:
    print(json.dumps({{"status": state_or("daemon_status", "not running")}}))
    raise SystemExit(0)

if args[:3] == ["app-server", "daemon", "restart"]:
    note_proxy()
    with (state / "daemon_restarts").open("a") as fh:
        fh.write("restart\\n")
    print(json.dumps({{"status": "restarted"}}))
    raise SystemExit(0)

if args[:1] == ["login"]:
    note_proxy()
    with (state / "login_args").open("a") as fh:
        fh.write(" ".join(args[1:]) + "\\n")
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


def test_profile_transfer(env: dict[str, str], base: Path) -> None:
    original = Path(env["MCDX_DATA_HOME"]) / "profiles" / "main"
    auth_bytes = (original / "auth.json").read_bytes()
    archive = base / "main.mcdx"
    exported = run(env, "export", "main", str(archive))
    assert "access-main" not in exported.stdout
    assert archive.stat().st_mode & 0o777 == 0o600
    contents = archive.read_bytes()
    assert run(env, "export", "alt", str(archive), check=False).returncode == 1
    assert archive.read_bytes() == contents

    destination = base / "destination"
    profiles = destination / "data" / "profiles"
    active_auth = destination / "codex" / "auth.json"
    dest_env = {
        **env,
        "MCDX_DATA_HOME": str(profiles.parent),
        "MCDX_CODEX_HOME": str(active_auth.parent),
        "MCDX_CODEX_BIN": str(destination / "missing-codex"),
    }
    write_auth(active_auth, "acct-destination")
    active_bytes = active_auth.read_bytes()
    run(dest_env, "import", str(archive))
    imported = profiles / "main"
    assert (imported / "auth.json").read_bytes() == auth_bytes
    assert (imported / "auth.json").stat().st_mode & 0o777 == 0o600
    assert imported.stat().st_mode & 0o777 == 0o700
    metadata = json.loads((imported / "metadata.json").read_text())
    original_metadata = json.loads((original / "metadata.json").read_text())
    assert metadata["auth_sha256"] == original_metadata["auth_sha256"]
    assert metadata["created_at"] == original_metadata["created_at"]
    assert active_auth.read_bytes() == active_bytes
    assert "main" in run(dest_env, "list").stdout

    conflict = run(dest_env, "import", str(archive), "-y", check=False)
    assert conflict.returncode == 1 and "profile already exists" in conflict.stderr
    assert (imported / "auth.json").read_bytes() == auth_bytes
    duplicate = run(dest_env, "import", str(archive), "--name", "work", check=False)
    assert duplicate.returncode == 2 and "same credential already exists" in duplicate.stderr
    run(dest_env, "import", str(archive), "--name", "work", "-y")
    assert (profiles / "work" / "auth.json").read_bytes() == auth_bytes

    bundle = json.loads(contents)
    invalid = base / "invalid.mcdx"
    for corrupted in (
        {**bundle, "format": "mcdx-profile-v2"},
        {**bundle, "name": "../outside"},
        {**bundle, "name": "broken", "auth_json": "{}"},
    ):
        invalid.write_text(json.dumps(corrupted), encoding="utf-8")
        assert run(dest_env, "import", str(invalid), check=False).returncode == 1
    assert sorted(p.name for p in profiles.iterdir()) == ["main", "work"]
    assert not (profiles.parent / "outside").exists()

    run(dest_env, "switch", "main", "--no-daemon-restart")
    assert active_auth.read_bytes() == auth_bytes
    assert "current: main" in run(dest_env, "current").stdout


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
        # Pretend the developer's shell exported a different proxy: the mcdx
        # config must win over it whenever one is set.
        env["https_proxy"] = "http://127.0.0.1:17777"
        env["all_proxy"] = "socks5h://127.0.0.1:17777"
        write_fake_codex(base / "fake-codex", codex_home, state)

        write_auth(auth, "acct-main", "main")
        run(env, "save-current", "main", "-y")
        assert "main" in run(env, "list").stdout
        assert "main" in run(env, "current").stdout

        write_auth(auth, "acct-alt", "alt")
        run(env, "save-current", "alt", "-y")
        test_profile_transfer(env, base)

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

        # -b/--browser switches the login flow from device auth to the browser flow.
        (state / "daemon_status").write_text("not running")
        run(env, "add", "browser-prof", "-b", "-y")
        assert (state / "login_args").read_text().splitlines()[-1] == ""
        run(env, "add", "device-prof", "-y")
        assert (state / "login_args").read_text().splitlines()[-1] == "--device-auth"
        run(env, "update", "browser-prof", "-b", "-y")
        assert (state / "login_args").read_text().splitlines()[-1] == ""

        # Proxy config: set/show/clear plus scheme validation.
        assert "no proxy configured" in run(env, "proxy", "show").stdout
        bad = run(env, "proxy", "set", "ftp://nope", check=False)
        assert bad.returncode == 1
        assert "scheme" in bad.stderr
        run(env, "proxy", "set", "http://127.0.0.1:19999")
        assert "http://127.0.0.1:19999" in run(env, "proxy", "show").stdout
        assert "http://127.0.0.1:19999" in run(env, "doctor").stdout

        # Daemon restarts after switch, and the standalone command, inherit it.
        (state / "daemon_status").write_text("running")
        restarts_before = len(restarts(state))
        run(env, "switch", "main", "-y")
        assert len(restarts(state)) == restarts_before + 1
        assert (state / "proxy_seen").read_text().splitlines()[-1] == "http://127.0.0.1:19999"

        restarts_before = len(restarts(state))
        run(env, "daemon", "restart")
        assert len(restarts(state)) == restarts_before + 1
        assert (state / "proxy_seen").read_text().splitlines()[-1] == "http://127.0.0.1:19999"

        # The login of an update is injected too (daemon not running -> no restart).
        (state / "daemon_status").write_text("not running")
        run(env, "update", "main", "-y")
        assert (state / "proxy_seen").read_text().splitlines()[-1] == "http://127.0.0.1:19999"

        # MCDX_PROXY overrides the stored config (socks URLs go through all_proxy).
        override = dict(env)
        override["MCDX_PROXY"] = "socks5h://127.0.0.1:18888"
        shown = run(override, "proxy", "show").stdout
        assert "socks5h://127.0.0.1:18888" in shown and "MCDX_PROXY" in shown
        run(override, "daemon", "restart")
        assert (state / "proxy_seen").read_text().splitlines()[-1] == "socks5h://127.0.0.1:18888"

        # Clearing falls back to the plain environment.
        run(env, "proxy", "clear")
        assert "no proxy configured" in run(env, "proxy", "show").stdout
        run(env, "daemon", "restart")
        baseline = env.get("https_proxy") or env.get("all_proxy") or ""
        assert (state / "proxy_seen").read_text().splitlines()[-1] == baseline

    print("tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
