"""Put the push keys on this PC, so the phone apps get lock-screen alerts.

    python -m installer.setup_phone_alerts C:\\path\\to\\downloaded-key.json
    python -m installer.setup_phone_alerts --apple C:\\path\\to\\AuthKey_ABC123DEFG.p8 --team TEAMID
    python -m installer.setup_phone_alerts --remove

Android phones need the Firebase key from your own Firebase project, and
iPhones need the push key from your Apple Developer account
(docs/notifications.md says how to make each). A key is checked, then
copied into the data folder, into a folder only this account and
administrators can read. It never goes into the repository or .env. Run it
again with a new file to replace a key.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import Config
from agent.datafolder import use_data_in_use
from agent.notifications import apns, fcm
from agent.security.certs import secure_directory


def _write(target: Path, data: bytes) -> None:
    secure_directory(target.parent)
    temporary = target.with_suffix(".part")
    temporary.write_bytes(data)
    os.replace(temporary, target)


def install_key(config, source: Path) -> Path:
    """Check ``source`` and copy it into place. Raises fcm.FcmError."""
    account = fcm.load_service_account(source)
    target = fcm.key_path(config)
    _write(target, source.read_bytes())
    print(f"Firebase project: {account.project_id}")
    return target


def install_apple_key(
    config, source: Path, team_id: str, key_id: str | None = None, sandbox: bool = False
) -> Path:
    """Check the .p8 file and the IDs, then copy them into place. The Key ID
    comes from Apple's file name (AuthKey_<Key ID>.p8) unless given.
    Raises apns.ApnsError."""
    if key_id is None:
        named = re.fullmatch(r"AuthKey_([A-Z0-9]{10})\.p8", source.name)
        if not named:
            raise apns.ApnsError("Give the Key ID with --key-id (it's on the key's page at Apple).")
        key_id = named.group(1)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise apns.ApnsError("That file can't be read.") from exc
    apns.read_key(data)
    apns.check_ids(key_id, team_id)
    _write(apns.key_path(config), data)
    settings = {"key_id": key_id, "team_id": team_id, "sandbox": sandbox}
    _write(apns.settings_path(config), json.dumps(settings).encode())
    print(f"Apple key {key_id}, team {team_id}" + (" (development builds)" if sandbox else ""))
    return apns.key_path(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up lock-screen alerts for the phone apps")
    parser.add_argument("key", nargs="?", help="The Firebase service account key (.json)")
    parser.add_argument("--apple", help="The Apple push key (AuthKey_<Key ID>.p8)")
    parser.add_argument("--team", help="Your Apple team ID (10 letters and numbers)")
    parser.add_argument("--key-id", help="The Apple Key ID, if the file was renamed")
    parser.add_argument(
        "--sandbox", action="store_true", help="For a development build installed from Xcode"
    )
    parser.add_argument("--remove", action="store_true", help="Remove both keys from this PC")
    parser.add_argument("--config", help="Path to config.yaml")
    args = parser.parse_args(argv)
    config = Config.load(args.config)
    use_data_in_use(config)
    if args.remove:
        targets = [fcm.key_path(config), apns.key_path(config), apns.settings_path(config)]
        found = [t for t in targets if t.exists()]
        for target in found:
            target.unlink()
        if found:
            print("Removed. Phones no longer get lock-screen alerts from this PC.")
        else:
            print("There was no key on this PC.")
        return 0
    if args.apple:
        if not args.team:
            parser.error("give your Apple team ID with --team")
        try:
            target = install_apple_key(
                config, Path(args.apple), args.team.strip().upper(), args.key_id, args.sandbox
            )
        except apns.ApnsError as exc:
            print(f"Not set up: {exc}")
            return 1
    elif args.key:
        try:
            target = install_key(config, Path(args.key))
        except fcm.FcmError as exc:
            print(f"Not set up: {exc}")
            return 1
    else:
        parser.error("give the downloaded Firebase key, --apple with the Apple key, or --remove")
    print(f"Saved to {target}")
    print("You can delete the downloaded copy now. Restart the agent, then turn on")
    print("lock-screen alerts in the phone app's Settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
