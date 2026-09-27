from datetime import date, timedelta
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.config import settings
from app.models import (Copy, Fine, Loan, Member, Notification, Reservation, Title, User,
                        LossDamageLog)

class RuleViolation(Exception): pass

def calculate_fine(due_date: date, returned_on: date, rate: float | None = None, cap: float | None = None) -> float:
    days = max(0, (returned_on - due_date).days)
    return round(min(days * (settings.fine_rate_per_day if rate is None else rate),
                     settings.fine_cap if cap is None else cap), 2)

def issue_copy(db: Session, member: Member, copy: Copy, actor: User, today: date | None = None) -> Loan:
    today = today or date.today()
    # Lock the copy row where the database supports row-level locking.
    copy = db.scalar(select(Copy).where(Copy.id == copy.id).with_for_update())
    db.refresh(member)
    if member.membership_status != "active": raise RuleViolation("Membership is not active")
    active = db.scalar(select(func.count()).select_from(Loan).where(Loan.member_id == member.id, Loan.status == "active"))
    if active >= member.borrow_limit: raise RuleViolation("Member has reached their borrowing limit")
    ready_hold = db.scalar(select(Reservation).where(Reservation.title_id == copy.title_id,
        Reservation.member_id == member.id, Reservation.ready_copy_id == copy.id,
        Reservation.status == "ready"))
    if copy.status == "reserved":
        if not ready_hold: raise RuleViolation("Copy is reserved for another member")
    elif copy.status != "available":
        raise RuleViolation("Copy is not available")
    reservation = db.scalar(select(Reservation).where(Reservation.title_id == copy.title_id,
        Reservation.member_id == member.id, Reservation.status == "pending").order_by(Reservation.reserved_at))
    waiting = db.scalar(select(Reservation).where(Reservation.title_id == copy.title_id,
        Reservation.status == "pending").order_by(Reservation.reserved_at))
    if not ready_hold and waiting and (not reservation or waiting.id != reservation.id): raise RuleViolation("This title is reserved for another member")
    copy.status = "issued"
    loan = Loan(copy_id=copy.id, member_id=member.id, issued_by=actor.id, issue_date=today,
                due_date=today + timedelta(days=settings.loan_days))
    if ready_hold: ready_hold.status = "fulfilled"
    elif reservation: reservation.status = "fulfilled"
    db.add(loan); db.flush(); return loan

def return_loan(db: Session, loan: Loan, actor: User, condition: str = "ok", today: date | None = None) -> Fine | None:
    today = today or date.today()
    if loan.status != "active": raise RuleViolation("Loan is already closed")
    if condition not in {"ok", "damaged", "lost"}: raise RuleViolation("Condition must be ok, damaged, or lost")
    loan = db.scalar(select(Loan).where(Loan.id == loan.id).with_for_update())
    loan.status = "returned"; loan.return_date = today
    copy = db.get(Copy, loan.copy_id); copy.status = "available" if condition == "ok" else condition
    fine = None
    amount = calculate_fine(loan.due_date, today)
    if amount:
        fine = Fine(loan_id=loan.id, amount=amount, reason="overdue"); db.add(fine)
    if condition != "ok":
        db.add(LossDamageLog(copy_id=copy.id, reported_by=actor.id, type=condition,
            notes="Recorded during return", replacement_cost=copy.replacement_cost))
        if condition == "lost" and copy.replacement_cost > 0:
            db.add(Fine(loan_id=loan.id, amount=copy.replacement_cost, reason="lost"))
    pending = db.scalar(select(Reservation).where(Reservation.title_id == copy.title_id,
        Reservation.status == "pending").order_by(Reservation.reserved_at))
    if pending and condition == "ok":
        pending.status = "ready"; pending.ready_copy_id = copy.id; copy.status = "reserved"
        db.add(Notification(member_id=pending.member_id, type="reservation_ready",
                            message=f"A copy of '{copy.title.title}' is ready for pickup."))
    db.flush(); return fine

def renew_loan(db: Session, loan: Loan, today: date | None = None) -> Loan:
    if loan.status != "active": raise RuleViolation("Only active loans can be renewed")
    if loan.due_date < (today or date.today()): raise RuleViolation("Overdue loans cannot be renewed")
    if loan.renewals >= 2: raise RuleViolation("Maximum renewals reached")
    loan = db.scalar(select(Loan).where(Loan.id == loan.id).with_for_update())
    if db.scalar(select(Reservation.id).where(Reservation.title_id == loan.copy.title_id, Reservation.status == "pending")):
        raise RuleViolation("A reservation is waiting for this title")
    loan.due_date += timedelta(days=settings.loan_days); loan.renewals += 1; db.flush(); return loan
