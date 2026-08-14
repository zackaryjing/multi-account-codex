#!/usr/bin/env python3
"""Switch between saved Codex auth profiles.

This tool intentionally manages only ~/.codex/auth.json. Runtime state and
config.toml stay owned by Codex itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__version__ = "0.1.1"


PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
RESERVED_LAST = "_last"
RESERVED_PROFILES = {RESERVED_LAST}


class McdxError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def codex_home() -> Path:
    return Path(os.environ.get("MCDX_CODEX_HOME", Path.home() / ".codex")).expanduser()


def data_home() -> Path:
    if "MCDX_DATA_HOME" in os.environ:
        return Path(os.environ["MCDX_DATA_HOME"]).expanduser()
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")).expanduser()
    return root / "mcdx"


def auth_path() -> Path:
    return codex_home() / "auth.json"


def profiles_dir() -> Path:
    return data_home() / "profiles"


def profile_dir(name: str) -> Path:
    validate_profile_name(name)
    return profiles_dir() / name


def profile_auth(name: str) -> Path:
    return profile_dir(name) / "auth.json"


def profile_meta(name: str) -> Path:
    return profile_dir(name) / "metadata.json"


def validate_profile_name(name: str) -> None:
    if not name or name in {".", ".."} or not PROFILE_RE.match(name):
        raise McdxError("profile name must contain only letters, numbers, '.', '_' and '-'")


def ensure_dirs() -> None:
    codex_home().mkdir(parents=True, exist_ok=True)
    profiles_dir().mkdir(parents=True, exist_ok=True)
    os.chmod(profiles_dir(), 0o700)
    migrate_reserved_profiles()


def migrate_reserved_profiles() -> None:
    old = profiles_dir() / "_current"
    new = profiles_dir() / RESERVED_LAST
    if old.exists() and not new.exists():
        os.replace(old, new)
        meta = new / "metadata.json"
        if meta.exists():
            info = auth_info(new / "auth.json")
            existing = load_json(meta)
            write_metadata(RESERVED_LAST, info, created_at=existing.get("created_at"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise McdxError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise McdxError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise McdxError(f"expected JSON object in {path}")
    return data


def auth_info(path: Path) -> dict[str, Any]:
    data = load_json(path)
    tokens = data.get("tokens")
    if tokens is not None and not isinstance(tokens, dict):
        raise McdxError(f"invalid tokens object in {path}")
    info = {
        "auth_mode": data.get("auth_mode"),
        "account_id": tokens.get("account_id") if isinstance(tokens, dict) else None,
        "auth_sha256": sha256_file(path),
    }
    if not info["auth_mode"]:
        raise McdxError(f"auth file is missing auth_mode: {path}")
    return info


def load_metadata(name: str) -> dict[str, Any] | None:
    path = profile_meta(name)
    if not path.exists():
        return None
    return load_json(path)


def list_profile_names() -> list[str]:
    base = profiles_dir()
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "auth.json").exists())


def current_info() -> dict[str, Any] | None:
    path = auth_path()
    if not path.exists():
        return None
    return auth_info(path)


def find_matching_profiles(info: dict[str, Any], exclude: str | None = None) -> list[str]:
    matches: list[str] = []
    for name in list_profile_names():
        if exclude and name == exclude:
            continue
        meta = load_metadata(name) or {}
        same_hash = meta.get("auth_sha256") == info.get("auth_sha256")
        same_account = (
            info.get("account_id")
            and meta.get("account_id") == info.get("account_id")
            and meta.get("auth_mode") == info.get("auth_mode")
        )
        if same_hash or same_account:
            matches.append(name)
    return matches


def prompt_yes_no(message: str, default: bool = False, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    suffix = "[Y/n]" if default else "[y/N]"
    if not sys.stdin.isatty():
        print(f"{message} {suffix} no (non-interactive default)", file=sys.stderr)
        return default
    answer = input(f"{message} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def atomic_copy(src: Path, dst: Path, mode: int = 0o600) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(dst.parent), delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with src.open("rb") as f:
            shutil.copyfileobj(f, tmp)
    os.chmod(tmp_path, mode)
    os.replace(tmp_path, dst)


def write_metadata(name: str, info: dict[str, Any], created_at: str | None = None) -> None:
    meta_path = profile_meta(name)
    existing = load_metadata(name) if meta_path.exists() else None
    metadata = {
        "name": name,
        "auth_mode": info.get("auth_mode"),
        "account_id": info.get("account_id"),
        "auth_sha256": info.get("auth_sha256"),
        "created_at": created_at or (existing or {}).get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    tmp = meta_path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, meta_path)


def save_profile(name: str, src_auth: Path, yes: bool = False, allow_duplicate: bool = False) -> bool:
    validate_profile_name(name)
    ensure_dirs()
    info = auth_info(src_auth)
    target_dir = profile_dir(name)
    target_auth = profile_auth(name)

    duplicates = find_matching_profiles(info, exclude=name)
    if duplicates and not allow_duplicate:
        joined = ", ".join(duplicates)
        if not prompt_yes_no(f"same credential already exists as: {joined}. Save anyway?", False, yes):
            return False

    if target_auth.exists():
        if not prompt_yes_no(f"profile '{name}' already exists. Overwrite?", False, yes):
            return False

    target_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(target_dir, 0o700)
    created_at = (load_metadata(name) or {}).get("created_at") if profile_meta(name).exists() else None
    atomic_copy(src_auth, target_auth)
    write_metadata(name, info, created_at)
    print(f"saved profile '{name}' ({format_identity(info)})")
    return True


def format_identity(info: dict[str, Any] | None) -> str:
    if not info:
        return "no auth"
    account = info.get("account_id") or "unknown-account"
    mode = info.get("auth_mode") or "unknown-mode"
    digest = str(info.get("auth_sha256") or "")[:12]
    return f"{mode} account={account} sha256={digest}"


def matching_current_profile() -> str | None:
    info = current_info()
    if not info:
        return None
    matches = find_matching_profiles(info)
    named_matches = [name for name in matches if name not in RESERVED_PROFILES]
    if named_matches:
        return named_matches[0]
    return matches[0] if matches else None


def cmd_list(_: argparse.Namespace) -> int:
    ensure_dirs()
    active_name = matching_current_profile()
    names = list_profile_names()
    if not names:
        print("no profiles")
        return 0
    for name in names:
        meta = load_metadata(name) or {}
        marker = "*" if name == active_name else ""
        print(
            f"{marker:1} {name:20} "
            f"{meta.get('auth_mode', '-'):10} "
            f"account={meta.get('account_id') or '-'} "
            f"sha256={str(meta.get('auth_sha256') or '-')[:12]} "
            f"updated={meta.get('updated_at', '-')}"
        )
    return 0


def cmd_current(_: argparse.Namespace) -> int:
    info = current_info()
    if not info:
        print("current: no auth.json")
        return 1
    match = matching_current_profile()
    if match:
        print(f"current: {match} ({format_identity(info)})")
    else:
        print(f"current: unmanaged ({format_identity(info)})")
    return 0


def cmd_save_current(args: argparse.Namespace) -> int:
    path = auth_path()
    if not path.exists():
        raise McdxError(f"current auth does not exist: {path}")
    return 0 if save_profile(args.name, path, yes=args.yes) else 2


def restore_auth_from_backup(backup: Path | None) -> None:
    current = auth_path()
    if backup and backup.exists():
        atomic_copy(backup, current)
    elif current.exists():
        current.unlink()


def cmd_add(args: argparse.Namespace) -> int:
    validate_profile_name(args.name)
    ensure_dirs()
    current = auth_path()
    backup: Path | None = None
    with tempfile.TemporaryDirectory(prefix="mcdx-add-") as td:
        td_path = Path(td)
        if current.exists():
            backup = td_path / "auth.backup.json"
            atomic_copy(current, backup)
            current.unlink()

        codex_bin = os.environ.get("MCDX_CODEX_BIN", "codex")
        print(f"running: {codex_bin} login --device-auth")
        result = subprocess.run([codex_bin, "login", "--device-auth"])
        if result.returncode != 0:
            restore_auth_from_backup(backup)
            raise McdxError("codex login failed; previous auth restored")
        if not current.exists():
            restore_auth_from_backup(backup)
            raise McdxError("codex login did not create auth.json; previous auth restored")
        try:
            saved = save_profile(args.name, current, yes=args.yes)
        except Exception:
            restore_auth_from_backup(backup)
            raise
        if not saved:
            restore_auth_from_backup(backup)
            print("add canceled; previous auth restored")
            return 2
        print(f"active profile is now '{args.name}'")
        return 0


def cmd_switch(args: argparse.Namespace) -> int:
    ensure_dirs()
    target = profile_auth(args.name)
    if not target.exists():
        raise McdxError(f"profile not found: {args.name}")
    target_info = auth_info(target)
    active = current_info()
    if active and active.get("auth_sha256") == target_info.get("auth_sha256"):
        print(f"already using '{args.name}' ({format_identity(target_info)})")
        return 0
    if active:
        save_profile(RESERVED_LAST, auth_path(), yes=True, allow_duplicate=True)
    atomic_copy(target, auth_path())
    print(f"switched to '{args.name}' ({format_identity(target_info)})")
    if active:
        print(f"previous auth saved as '{RESERVED_LAST}' ({format_identity(active)})")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    ensure_dirs()
    pdir = profile_dir(args.name)
    if not pdir.exists():
        raise McdxError(f"profile not found: {args.name}")
    active = current_info()
    meta = load_metadata(args.name) or {}
    if active and meta.get("auth_sha256") == active.get("auth_sha256") and not args.force:
        raise McdxError("refusing to remove active profile without --force")
    shutil.rmtree(pdir)
    print(f"removed profile '{args.name}'")
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    ensure_dirs()
    old_name = args.old_name
    new_name = args.new_name
    validate_profile_name(old_name)
    validate_profile_name(new_name)
    if old_name == new_name:
        print(f"profile already named '{new_name}'")
        return 0

    old_dir = profile_dir(old_name)
    new_dir = profile_dir(new_name)
    if not old_dir.exists():
        raise McdxError(f"profile not found: {old_name}")
    if new_dir.exists():
        raise McdxError(f"profile already exists: {new_name}")

    os.replace(old_dir, new_dir)
    info = auth_info(profile_auth(new_name))
    existing = load_metadata(new_name) or {}
    write_metadata(new_name, info, created_at=existing.get("created_at"))
    print(f"renamed profile '{old_name}' -> '{new_name}'")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    def missing_status(path: Path) -> str:
        ancestor = path.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        writable = ancestor.exists() and os.access(ancestor, os.W_OK | os.X_OK)
        return f"missing (nearest existing parent {ancestor}: {'writable' if writable else 'not writable'})"

    print(f"codex_home: {codex_home()}")
    print(f"data_home:  {data_home()}")
    print(f"codex bin:  {shutil.which(os.environ.get('MCDX_CODEX_BIN', 'codex')) or 'not found'}")
    print(f"auth:       {format_identity(current_info()) if auth_path().exists() else 'missing'}")
    print(f"profiles:   {len(list_profile_names())}")
    for path in [codex_home(), profiles_dir()]:
        if path.exists():
            print(f"{path}: exists")
        else:
            print(f"{path}: {missing_status(path)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcdx", description="Switch between Codex auth profiles")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list saved profiles").set_defaults(func=cmd_list)
    sub.add_parser("current", help="show active profile").set_defaults(func=cmd_current)
    sub.add_parser("doctor", help="check mcdx and Codex auth paths").set_defaults(func=cmd_doctor)

    p = sub.add_parser("save-current", help="save current ~/.codex/auth.json as a profile")
    p.add_argument("name")
    p.add_argument("-y", "--yes", action="store_true", help="answer yes to overwrite/duplicate prompts")
    p.set_defaults(func=cmd_save_current)

    p = sub.add_parser("add", help="login with Codex device auth and save as a profile")
    p.add_argument("name")
    p.add_argument("-y", "--yes", action="store_true", help="answer yes to overwrite/duplicate prompts")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("switch", help="switch active Codex auth to a profile")
    p.add_argument("name")
    p.set_defaults(func=cmd_switch)

    p = sub.add_parser("remove", help="remove a saved profile")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="allow removing the active profile")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("rename", help="rename a saved profile")
    p.add_argument("old_name")
    p.add_argument("new_name")
    p.set_defaults(func=cmd_rename)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except McdxError as exc:
        print(f"mcdx: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("mcdx: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
