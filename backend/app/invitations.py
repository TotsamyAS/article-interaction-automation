"""Run by the user only: prints an access link. Never log its output."""
import argparse
import sys
from urllib.parse import urlencode

from .access import AccessService, Signatures, key_from_runtime
from .config import load_settings
from .database import Database
from .errors import DomainError


def main():
    parser = argparse.ArgumentParser(description="Многоразовые приглашения; вывод содержит ссылку доступа.")
    parser.add_argument("code", help="Псевдоним участника или исследователя, без ФИО/email")
    parser.add_argument("--role", choices=("participant", "researcher"), default="participant")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    try:
        signatures = Signatures(key_from_runtime())
        settings = load_settings()
        database = Database(settings)
        database.initialize()
        access = AccessService(database, signatures)
        if args.revoke:
            access.revoke(args.code)
            print("Приглашение отозвано; результаты сохранены.")
            return
        principal = access.provision(args.code, args.role)
        query = urlencode({"invitation": access.invitation(principal)})
        print(f"{settings.public_base_url.rstrip('/')}/api/access/enter?{query}")
    except (DomainError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
