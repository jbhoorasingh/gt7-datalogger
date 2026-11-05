#!/usr/bin/env python3
"""Make the key the release workflow signs updates with (app.self_update).

    backend/.venv/bin/python desktop/scripts/update_key.py new ~/gt7-update-signing-key.pem
    backend/.venv/bin/python desktop/scripts/update_key.py public <key.pem> [--add]

Write the key outside the repository (the repo ignores *.pem, but a home folder
path keeps it out of every checkout and worktree).

`new` writes a fresh Ed25519 private key to <key.pem> (PKCS#8 PEM, readable by
you only; it refuses to overwrite a file) and prints its public half. `public`
prints the public half of an existing key; with --add it also writes it into
UPDATE_KEYS in backend/app/self_update.py (once; the keys already there stay).
The private key goes to the release workflow, and nowhere else:

    gh secret set UPDATE_SIGNING_KEY --env desktop-release < <key.pem>

Keep two offline copies of the file, then delete it. An install trusts only the
keys it was built with, so a lost key means a release signed with a new one
reaches nobody until they install it by hand; a new key is added to
UPDATE_KEYS one release before the old one is dropped (docs/internals/releasing.md).

Uses pycryptodome, which the backend already depends on.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from pathlib import Path

from Crypto.PublicKey import ECC

SELF_UPDATE = Path(__file__).resolve().parents[2] / "backend" / "app" / "self_update.py"
_KEYS = re.compile(r"^UPDATE_KEYS: tuple\[str, \.\.\.\] = \((.*?)\)$", re.M | re.S)


def public_half(key: ECC.EccKey) -> str:
    return base64.b64encode(key.public_key().export_key(format="raw")).decode()


def add_key(public: str, path: Path = SELF_UPDATE) -> bool:
    """Add `public` to UPDATE_KEYS in `path`; False when it is already there."""
    text = path.read_text(encoding="utf-8")
    found = _KEYS.search(text)
    if found is None:
        raise SystemExit(f"no UPDATE_KEYS tuple found in {path}")
    keys = re.findall(r'"([^"]+)"', found.group(1))
    if public in keys:
        return False
    keys.append(public)
    if len(keys) == 1:
        tuple_text = f'("{keys[0]}",)'
    else:
        tuple_text = "(\n" + "".join(f'    "{k}",\n' for k in keys) + ")"
    start, end = found.span(1)
    text = text[: start - 1] + tuple_text + text[end + 1 :]
    path.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("new", "public"))
    parser.add_argument("key", type=Path)
    parser.add_argument("--add", action="store_true",
                        help="public: also add it to UPDATE_KEYS in self_update.py")
    parser.add_argument("--into", type=Path, default=SELF_UPDATE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.action == "public":
        key = ECC.import_key(args.key.read_text())
        if key.curve != "Ed25519" or not key.has_private():
            print(f"{args.key} is not an Ed25519 private key", file=sys.stderr)
            return 1
        public = public_half(key)
        print(public)
        if args.add:
            added = add_key(public, args.into)
            print(f"{'added to' if added else 'already in'} UPDATE_KEYS in {args.into}")
        return 0
    key = ECC.generate(curve="ed25519")
    try:
        fd = os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"{args.key} exists; not overwriting it", file=sys.stderr)
        return 1
    with os.fdopen(fd, "w") as fh:
        fh.write(key.export_key(format="PEM"))
    print(f"private key: {args.key}")
    print(f'public half, for UPDATE_KEYS: "{public_half(key)}"')
    print(f"next: gh secret set UPDATE_SIGNING_KEY --env desktop-release < {args.key}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
