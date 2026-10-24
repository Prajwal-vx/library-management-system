from datetime import date, timedelta
from sqlalchemy import select
from app.database import Base, engine, SessionLocal
from app.models import User, Member, Shelf, Title, Copy, Loan
from app.security import hash_password

Base.metadata.create_all(engine)
db = SessionLocal()
try:
    admin = db.scalar(select(User).where(User.email == "admin@library.local"))
    if not admin:
        admin = User(
            name="Avery Admin",
            email="admin@library.local",
            password_hash=hash_password("AdminPass123!"),
            role="admin",
        )
        db.add(admin)
        lib = User(
            name="Lee Librarian",
            email="librarian@library.local",
            password_hash=hash_password("Librarian123!"),
            role="librarian",
        )
        db.add(lib)
        student = User(
            name="Sam Student",
            email="student@library.local",
            password_hash=hash_password("StudentPass123!"),
            role="member",
        )
        db.add(student)
        db.flush()
        db.add(Member(user_id=student.id, member_code="STU-1001", borrow_limit=5))
        shelf = Shelf(section="Fiction", aisle="A", shelf_code="A-01", capacity=80)
        db.add(shelf)
        db.flush()
        books = [
            Title(
                title="The Left Hand of Darkness",
                author="Ursula K. Le Guin",
                isbn="9780441478125",
                category="Science Fiction",
                publisher="Ace Books",
                published_year=1969,
            ),
            Title(
                title="A Brief History of Time",
                author="Stephen Hawking",
                isbn="9780553380163",
                category="Science",
                publisher="Bantam",
                published_year=1988,
            ),
            Title(
                title="The Pragmatic Programmer",
                author="Andrew Hunt and David Thomas",
                isbn="9780135957059",
                category="Technology",
                publisher="Addison-Wesley",
                published_year=2019,
            ),
        ]
        db.add_all(books)
        db.flush()
        db.add_all(
            [
                Copy(
                    title_id=books[0].id,
                    barcode="LIB-0001",
                    qr_code="QR-LIB-0001",
                    shelf_id=shelf.id,
                    replacement_cost=18.5,
                ),
                Copy(
                    title_id=books[0].id,
                    barcode="LIB-0002",
                    shelf_id=shelf.id,
                    replacement_cost=18.5,
                ),
                Copy(
                    title_id=books[1].id,
                    barcode="LIB-0003",
                    shelf_id=shelf.id,
                    replacement_cost=22,
                ),
                Copy(
                    title_id=books[2].id,
                    barcode="LIB-0004",
                    shelf_id=shelf.id,
                    replacement_cost=35,
                ),
            ]
        )
        db.commit()
    else:
        print("Demo data already exists; no changes made.")
finally:
    db.close()
print(
    "Seed complete. Demo users: admin@library.local / AdminPass123!, librarian@library.local / Librarian123!, student@library.local / StudentPass123!"
)
