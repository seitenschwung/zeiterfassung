"""Create a value for ZEIT_PASSWORD_HASH: python -m app.hash_password (asks for the password, hidden)."""

import getpass
import sys

from app.auth import hash_password


def main() -> int:
    first = getpass.getpass("Passwort: ")
    if len(first) < 12:
        print("Zu kurz: mindestens 12 Zeichen.", file=sys.stderr)
        return 1
    if getpass.getpass("Passwort wiederholen: ") != first:
        print("Die Eingaben stimmen nicht überein.", file=sys.stderr)
        return 1
    print(hash_password(first))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
