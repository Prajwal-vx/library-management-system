from datetime import date
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import Base
from app.models import User, Member, Title, Copy, Loan, Reservation
from app.services import calculate_fine, issue_copy, return_loan, renew_loan, RuleViolation

@pytest.fixture
def db():
    engine=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user=User(name="Librarian",email="l@test",password_hash="x",role="librarian")
        member_user=User(name="Member",email="m@test",password_hash="x",role="member")
        session.add_all([user,member_user]);session.flush()
        member=Member(user_id=member_user.id,member_code="M1",borrow_limit=1)
        title=Title(title="Test",author="Author",category="Test")
        session.add_all([member,title]);session.flush()
        copy=Copy(title_id=title.id,barcode="C1");session.add(copy);session.commit()
        yield session,user,member,copy

def test_fine_is_pure_and_capped():
    due=date(2025,1,1)
    assert calculate_fine(due,due,rate=1.25,cap=10)==0
    assert calculate_fine(due,date(2025,1,4),rate=1.25,cap=10)==3.75
    assert calculate_fine(due,date(2025,2,1),rate=1.25,cap=10)==10

def test_issue_and_return_sync_copy_and_loan(db):
    session,actor,member,copy=db
    loan=issue_copy(session,member,copy,actor,today=date(2025,1,1));session.commit()
    assert copy.status=="issued" and loan.status=="active"
    with pytest.raises(RuleViolation): issue_copy(session,member,copy,actor,today=date(2025,1,1))
    loan.due_date=date(2025,1,2)
    fine=return_loan(session,loan,actor,today=date(2025,1,5));session.commit()
    assert loan.status=="returned" and copy.status=="available" and fine.amount==3

def test_borrow_limit_is_enforced(db):
    session,actor,member,copy=db
    loan=issue_copy(session,member,copy,actor,today=date(2025,1,1));session.commit()
    title=copy.title; other=Copy(title_id=title.id,barcode="C2");session.add(other);session.commit()
    with pytest.raises(RuleViolation,match="borrowing limit"):
        issue_copy(session,member,other,actor,today=date(2025,1,1))

def test_renewal_limits_and_status(db):
    session,actor,member,copy=db
    loan=issue_copy(session,member,copy,actor,today=date(2025,1,1));session.commit()
    renew_loan(session,loan,today=date(2025,1,2));renew_loan(session,loan,today=date(2025,1,2))
    with pytest.raises(RuleViolation,match="Maximum renewals"):
        renew_loan(session,loan,today=date(2025,1,2))

def test_database_rejects_two_active_loans_for_one_copy(db):
    session,actor,member,copy=db
    first=Loan(copy_id=copy.id,member_id=member.id,issued_by=actor.id,
               issue_date=date(2025,1,1),due_date=date(2025,1,15),status="active")
    session.add(first);session.commit()
    second=Loan(copy_id=copy.id,member_id=member.id,issued_by=actor.id,
                issue_date=date(2025,1,1),due_date=date(2025,1,15),status="active")
    session.add(second)
    with pytest.raises(IntegrityError): session.commit()
    session.rollback()

def test_ready_reservation_can_be_checked_out_by_hold_member(db):
    session,actor,member,copy=db
    hold=Reservation(title_id=copy.title_id,member_id=member.id,status="ready",ready_copy_id=copy.id)
    copy.status="reserved";session.add(hold);session.commit()
    loan=issue_copy(session,member,copy,actor,today=date(2025,1,1));session.commit()
    assert loan.status=="active"
    assert copy.status=="issued"
    assert hold.status=="fulfilled"
