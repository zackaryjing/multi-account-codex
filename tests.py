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


def run(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        [sys.executable, str(MCDX), *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and cp.returncode != 0:
        raise AssertionError(f"command failed: {args}\nstdout={cp.stdout}\nstderr={cp.stderr}")
    return cp


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mcdx-test-") as td:
        base = Path(td)
        codex_home = base / "codex"
        data_home = base / "data"
        env = os.environ.copy()
        env["MCDX_CODEX_HOME"] = str(codex_home)
        env["MCDX_DATA_HOME"] = str(data_home)

        write_auth(codex_home / "auth.json", "acct-main", "main")
        run(env, "save-current", "main", "-y")
        assert "main" in run(env, "list").stdout
        assert "main" in run(env, "current").stdout

        write_auth(codex_home / "auth.json", "acct-alt", "alt")
        run(env, "save-current", "alt", "-y")
        run(env, "switch", "main")
        active = json.loads((codex_home / "auth.json").read_text())
        assert active["tokens"]["account_id"] == "acct-main"
        assert (data_home / "profiles" / "_last" / "auth.json").exists()
        assert "current: main" in run(env, "current").stdout

        run(env, "rename", "alt", "renamed-alt")
        assert "renamed-alt" in run(env, "list").stdout
        assert not (data_home / "profiles" / "alt").exists()
        renamed_meta = json.loads((data_home / "profiles" / "renamed-alt" / "metadata.json").read_text())
        assert renamed_meta["name"] == "renamed-alt"
        run(env, "switch", "renamed-alt")
        active = json.loads((codex_home / "auth.json").read_text())
        assert active["tokens"]["account_id"] == "acct-alt"
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

        fake = base / "fake-codex"
        fake.write_text(
            f"""#!/bin/sh
mkdir -p {codex_home!s}
cat > {codex_home / 'auth.json'} <<'JSON'
{{"auth_mode":"chatgpt","tokens":{{"id_token":"id-new","access_token":"access-new","refresh_token":"refresh-new","account_id":"acct-new"}}}}
JSON
""",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env["MCDX_CODEX_BIN"] = str(fake)
        run(env, "add", "new", "-y")
        assert "new" in run(env, "current").stdout

    print("tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
