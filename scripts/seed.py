"""Seed sample catalogue data and optional local demo accounts.

Demo accounts are only created or rotated when all three SEED_*_PASSWORD
environment variables are supplied. The operation is safe to rerun after the
catalogue has already been seeded.
"""

import os

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Copy, Member, Shelf, Title, User
from app.security import hash_password


DEMO_ACCOUNTS = (
    ("admin", "Avery Admin", "admin@library.local", "admin", None),
    ("librarian", "Lee Librarian", "librarian@library.local", "librarian", None),
    ("member", "Sam Student", "student@library.local", "member", "STU-1001"),
)


def seed_catalogue(db):
    if db.scalar(select(Title.id).limit(1)):
        return False

    shelf = Shelf(section="Fiction", aisle="A", shelf_code="A-01", capacity=80)
    db.add(shelf)
    db.flush()

    books = [
        Title(title="The Left Hand of Darkness", author="Ursula K. Le Guin", isbn="9780441478125", category="Science Fiction", publisher="Ace Books", published_year=1969),
        Title(title="A Brief History of Time", author="Stephen Hawking", isbn="9780553380163", category="Science", publisher="Bantam", published_year=1988),
        Title(title="The Pragmatic Programmer", author="Andrew Hunt and David Thomas", isbn="9780135957059", category="Technology", publisher="Addison-Wesley", published_year=2019),
    ]
    db.add_all(books)
    db.flush()
    db.add_all([
        Copy(title_id=books[0].id, barcode="LIB-0001", qr_code="QR-LIB-0001", shelf_id=shelf.id, replacement_cost=18.5),
        Copy(title_id=books[0].id, barcode="LIB-0002", shelf_id=shelf.id, replacement_cost=18.5),
        Copy(title_id=books[1].id, barcode="LIB-0003", shelf_id=shelf.id, replacement_cost=22),
        Copy(title_id=books[2].id, barcode="LIB-0004", shelf_id=shelf.id, replacement_cost=35),
    ])
    return True


def seed_accounts(db, passwords):
    if not all(passwords.values()):
        return False

    for key, name, email, role, member_code in DEMO_ACCOUNTS:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(name=name, email=email, password_hash=hash_password(passwords[key]), role=role)
            db.add(user)
            db.flush()
        else:
            user.password_hash = hash_password(passwords[key])

        if member_code and db.scalar(select(Member.id).where(Member.user_id == user.id)) is None:
            db.add(Member(user_id=user.id, member_code=member_code, borrow_limit=5))

    return True


def main():
    Base.metadata.create_all(engine)
    passwords = {
        "admin": os.environ.get("SEED_ADMIN_PASSWORD"),
        "librarian": os.environ.get("SEED_LIBRARIAN_PASSWORD"),
        "member": os.environ.get("SEED_MEMBER_PASSWORD"),
    }
    if any(passwords.values()) and not all(passwords.values()):
        raise RuntimeError("Set all three SEED_*_PASSWORD variables or leave all unset")

    with SessionLocal() as db:
        catalog_created = seed_catalogue(db)
        accounts_created_or_rotated = seed_accounts(db, passwords)
        db.commit()

    if catalog_created:
        print("Sample catalogue seeded.")
    if accounts_created_or_rotated:
        print("Demo accounts created or passwords updated.")
    elif not passwords["admin"]:
        print("No demo accounts created. Set all three SEED_*_PASSWORD variables to create them.")


if __name__ == "__main__":
    main()
