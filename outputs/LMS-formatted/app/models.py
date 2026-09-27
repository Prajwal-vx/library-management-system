from datetime import datetime, date, timezone
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    member: Mapped["Member"] = relationship(back_populates="user", uselist=False)


class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    member_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    borrow_limit: Mapped[int] = mapped_column(Integer, default=5)
    membership_status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user: Mapped[User] = relationship(back_populates="member")


class Shelf(Base):
    __tablename__ = "shelves"
    id: Mapped[int] = mapped_column(primary_key=True)
    section: Mapped[str] = mapped_column(String(80))
    aisle: Mapped[str] = mapped_column(String(40))
    shelf_code: Mapped[str] = mapped_column(String(40), unique=True)
    capacity: Mapped[int] = mapped_column(Integer, default=100)


class Title(Base):
    __tablename__ = "titles"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(250), index=True)
    author: Mapped[str] = mapped_column(String(200), index=True)
    isbn: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    category: Mapped[str] = mapped_column(String(100), default="General")
    edition: Mapped[str | None] = mapped_column(String(60), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(150), nullable=True)
    published_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_outdated_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    copies: Mapped[list["Copy"]] = relationship(back_populates="title")


class Copy(Base):
    __tablename__ = "copies"
    __table_args__ = (UniqueConstraint("barcode", name="uq_copy_barcode"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id"), index=True)
    barcode: Mapped[str] = mapped_column(String(80), index=True)
    qr_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    rfid_tag: Mapped[str | None] = mapped_column(
        String(160), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    shelf_id: Mapped[int | None] = mapped_column(
        ForeignKey("shelves.id"), nullable=True
    )
    acquired_date: Mapped[date] = mapped_column(Date, default=date.today)
    replacement_cost: Mapped[float] = mapped_column(Float, default=0)
    title: Mapped[Title] = relationship(back_populates="copies")
    shelf: Mapped[Shelf | None] = relationship()


class Loan(Base):
    __tablename__ = "loans"
    __table_args__ = (
        Index(
            "uq_active_loan_copy",
            "copy_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    copy_id: Mapped[int] = mapped_column(ForeignKey("copies.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    issued_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    issue_date: Mapped[date] = mapped_column(Date, default=date.today)
    due_date: Mapped[date] = mapped_column(Date)
    return_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    renewals: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    copy: Mapped[Copy] = relationship()
    member: Mapped[Member] = relationship()


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    ready_copy_id: Mapped[int | None] = mapped_column(
        ForeignKey("copies.id"), nullable=True
    )


class Fine(Base):
    __tablename__ = "fines"
    __table_args__ = (
        UniqueConstraint("loan_id", "reason", name="uq_fine_loan_reason"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"))
    amount: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(20), default="overdue")
    status: Mapped[str] = mapped_column(String(20), default="unpaid")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    loan: Mapped[Loan] = relationship()


class LossDamageLog(Base):
    __tablename__ = "loss_damage_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    copy_id: Mapped[int] = mapped_column(ForeignKey("copies.id"), index=True)
    reported_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str] = mapped_column(Text, default="")
    replacement_cost: Mapped[float] = mapped_column(Float, default=0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    copy: Mapped[Copy] = relationship()


class Softcopy(Base):
    __tablename__ = "softcopies"
    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id"), index=True)
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    access_level: Mapped[str] = mapped_column(String(20), default="all_members")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(String(500))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    channel: Mapped[str] = mapped_column(String(20), default="in_app")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
