"""Reset one user's password.

Usage:
    python -m tools.reset_password --username alice [--password new-password]
If password is omitted, a random one is generated and printed.
"""
from __future__ import annotations

import argparse
import secrets

from sqlalchemy import func, select

from app.core.config import Settings
from app.core.security import hash_password
from app.db.models import User
from app.db.session import Database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--password")
    args = parser.parse_args()
    password = args.password or secrets.token_urlsafe(12)
    if len(password) < 8:
        print("password must be at least 8 characters")
        return 2
    settings = Settings.from_env()
    database = Database(settings)
    database.create_all()
    session = database.session()
    try:
        user = session.scalar(select(User).where(func.lower(User.username) == args.username.casefold()))
        if user is None:
            print(f"user not found: {args.username}")
            return 1
        user.password_hash = hash_password(password, settings.bcrypt_rounds)
        session.commit()
        print(f"password reset for {user.username}: {password}")
        return 0
    finally:
        session.close()
        database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())