"""Administrator tasks for the Kenya forecast server.

    python manage.py create-admin      create an administrator account (do this once, first)
    python manage.py reset-password    set a new password for any account
    python manage.py list-users        show all accounts
"""
import getpass
import sqlite3
import sys
from datetime import datetime

from werkzeug.security import generate_password_hash

from app import DB_PATH, EAT, init_db


def ask_password():
    while True:
        pw = getpass.getpass("Password (at least 8 characters, typing is hidden): ")
        if len(pw) < 8:
            print("Too short, try again.")
            continue
        if pw != getpass.getpass("Type it again: "):
            print("The two passwords don't match, try again.")
            continue
        return pw


def main():
    init_db()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    con = sqlite3.connect(DB_PATH)
    if cmd == "create-admin":
        username = input("Username for the administrator (e.g. admin): ").strip().lower()
        name = input("Full name: ").strip()
        if not username or not name:
            sys.exit("Username and name are both needed.")
        try:
            con.execute("INSERT INTO users (username, name, password_hash, role, active, created_at) VALUES (?,?,?,?,1,?)",
                        (username, name, generate_password_hash(ask_password()), "admin", datetime.now(EAT).isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            sys.exit("That username already exists. Use reset-password instead.")
        con.commit()
        print(f"Administrator '{username}' created. Log in at /staff")
    elif cmd == "reset-password":
        username = input("Username: ").strip().lower()
        if not con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            sys.exit("No account with that username.")
        con.execute("UPDATE users SET password_hash = ?, active = 1 WHERE username = ?", (generate_password_hash(ask_password()), username))
        con.commit()
        print("Password updated.")
    elif cmd == "list-users":
        for r in con.execute("SELECT username, name, role, active, last_login FROM users ORDER BY username"):
            print(f"{r[0]:20} {r[1]:30} {r[2]:6} {'active' if r[3] else 'disabled':9} last login: {r[4] or 'never'}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
