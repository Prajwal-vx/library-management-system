from datetime import date, datetime, timedelta
from pathlib import Path
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from app.config import settings
from app.database import get_db
from app.models import *
from app.security import current_user, roles, verify_password, create_token, hash_password, consume_login_attempt, clear_login_attempts
from app.services import RuleViolation, issue_copy, return_loan, renew_loan

router = APIRouter(prefix="/api", tags=["Library API"])

class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=250); author: str = Field(min_length=1, max_length=200)
    isbn: str | None = None; category: str = "General"; edition: str | None = None
    publisher: str | None = None; published_year: int | None = Field(default=None, ge=1000, le=2200)
    description: str | None = None
class CopyIn(BaseModel):
    title_id: int; barcode: str = Field(min_length=1, max_length=80); qr_code: str | None = None
    rfid_tag: str | None = None; shelf_id: int | None = None; replacement_cost: float = Field(default=0, ge=0)
class MemberIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    member_code: str = Field(min_length=1, max_length=40)
    borrow_limit: int = Field(default=5, ge=1, le=50)
class CirculationIn(BaseModel):
    member_id: int | None = None; copy_id: int | None = None; barcode: str | None = None
class ReturnIn(BaseModel):
    condition: str = "ok"

@router.post("/auth/token")
def login(request: Request, email: str = Form(max_length=255), password: str = Form(max_length=128), db: Session = Depends(get_db)):
    client_key = request.client.host if request.client else "unknown"
    retry_after = consume_login_attempt(client_key)
    if retry_after:
        raise HTTPException(429, "Too many sign-in attempts; try again later", headers={"Retry-After": str(retry_after)})
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if not user or user.status != "active" or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    clear_login_attempts(client_key)
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role}

@router.get("/dashboard")
def api_dashboard(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return {"total_titles":db.scalar(select(func.count()).select_from(Title).where(Title.deleted_at.is_(None))),"total_copies":db.scalar(select(func.count()).select_from(Copy)),"issued_books":db.scalar(select(func.count()).select_from(Copy).where(Copy.status=="issued")),"active_members":db.scalar(select(func.count()).select_from(Member).where(Member.membership_status=="active")),"overdue_books":db.scalar(select(func.count()).select_from(Loan).where(Loan.status=="active",Loan.due_date<date.today()))}

@router.get("/titles")
def list_titles(q:str="",user:User=Depends(current_user),db:Session=Depends(get_db)):
    stmt=select(Title).where(Title.deleted_at.is_(None))
    if q:stmt=stmt.where((Title.title.ilike(f"%{q}%"))|(Title.author.ilike(f"%{q}%"))|(Title.isbn.ilike(f"%{q}%")))
    return [{"id":t.id,"title":t.title,"author":t.author,"isbn":t.isbn,"category":t.category,"available":db.scalar(select(func.count()).select_from(Copy).where(Copy.title_id==t.id,Copy.status=="available"))} for t in db.scalars(stmt.limit(300)).all()]

@router.post("/titles",status_code=201)
def create_title(data:TitleIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    t=Title(**data.model_dump());db.add(t)
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"A title with that ISBN already exists")
    db.refresh(t);return {"id":t.id,"title":t.title}

@router.patch("/titles/{title_id}")
def update_title(title_id:int,data:TitleIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    t=db.get(Title,title_id)
    if not t or t.deleted_at:raise HTTPException(404,"Title not found")
    for k,v in data.model_dump().items():setattr(t,k,v)
    db.commit();return {"id":t.id,"title":t.title}

@router.delete("/titles/{title_id}",status_code=204)
def archive_title(title_id:int,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    t=db.get(Title,title_id)
    if not t:raise HTTPException(404,"Title not found")
    if db.scalar(select(Loan.id).join(Copy).where(Copy.title_id==title_id,Loan.status=="active")):raise HTTPException(409,"Title has active loans")
    t.deleted_at=datetime.utcnow();db.commit()

@router.post("/copies",status_code=201)
def create_copy(data:CopyIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    if not db.get(Title,data.title_id):raise HTTPException(404,"Title not found")
    c=Copy(**data.model_dump());db.add(c)
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"Barcode or RFID tag is already assigned")
    db.refresh(c);return {"id":c.id,"barcode":c.barcode}

@router.get("/copies/{barcode}/location")
def find_copy(barcode:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    c=db.scalar(select(Copy).options(joinedload(Copy.title),joinedload(Copy.shelf)).where(Copy.barcode==barcode))
    if not c:raise HTTPException(404,"Copy not found")
    return {"barcode":c.barcode,"title":c.title.title,"status":c.status,"shelf":c.shelf.shelf_code if c.shelf else None,"section":c.shelf.section if c.shelf else None,"aisle":c.shelf.aisle if c.shelf else None}

@router.post("/members",status_code=201)
def create_member(data:MemberIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    u=User(name=data.name.strip(),email=data.email.lower().strip(),password_hash=hash_password(data.password),role="member");db.add(u);db.flush();m=Member(user_id=u.id,member_code=data.member_code,borrow_limit=data.borrow_limit);db.add(m)
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"Email or member code is already in use")
    return {"id":m.id,"member_code":m.member_code}

@router.post("/circulation/issue",status_code=201)
def api_issue(data:CirculationIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    m=db.get(Member,data.member_id) if data.member_id else None
    c=db.get(Copy,data.copy_id) if data.copy_id else db.scalar(select(Copy).where(Copy.barcode==data.barcode)) if data.barcode else None
    if not m or not c:raise HTTPException(404,"Member or copy not found")
    try:
        loan=issue_copy(db,m,c,user);db.commit();return {"loan_id":loan.id,"due_date":loan.due_date}
    except RuleViolation as e:db.rollback();raise HTTPException(409,str(e))
    except IntegrityError:db.rollback();raise HTTPException(409,"Copy was issued concurrently; reload and retry")

@router.post("/circulation/{loan_id}/return")
def api_return(loan_id:int,data:ReturnIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if not loan:raise HTTPException(404,"Loan not found")
    try:
        fine=return_loan(db,loan,user,data.condition);db.commit();return {"loan_id":loan.id,"fine":fine.amount if fine else 0,"status":loan.status}
    except RuleViolation as e:db.rollback();raise HTTPException(409,str(e))

@router.post("/circulation/{loan_id}/renew")
def api_renew(loan_id:int,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if not loan:raise HTTPException(404,"Loan not found")
    try:renew_loan(db,loan);db.commit();return {"due_date":loan.due_date,"renewals":loan.renewals}
    except RuleViolation as e:db.rollback();raise HTTPException(409,str(e))

@router.post("/reservations",status_code=201)
def reserve(title_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    member=user.member
    if user.role!="member" or not member or member.membership_status!="active":raise HTTPException(403,"Active member account required")
    if not db.get(Title,title_id):raise HTTPException(404,"Title not found")
    existing=db.scalar(select(Reservation).where(Reservation.title_id==title_id,Reservation.member_id==member.id,Reservation.status.in_(["pending","ready"])))
    if existing:raise HTTPException(409,"You already have an active reservation")
    r=Reservation(title_id=title_id,member_id=member.id);db.add(r);db.flush()
    available=db.scalar(select(Copy).where(Copy.title_id==title_id,Copy.status=="available").order_by(Copy.id).with_for_update())
    if available:
        r.status="ready";r.ready_copy_id=available.id;available.status="reserved"
        db.add(Notification(member_id=member.id,type="reservation_ready",message=f"A copy is ready for pickup. Barcode: {available.barcode}."))
    db.commit();return {"reservation_id":r.id,"status":r.status}

@router.post("/loans/{loan_id}/renew")
def member_renew(loan_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if user.role!="member" or not user.member or not loan or loan.member_id!=user.member.id:raise HTTPException(404,"Loan not found")
    try:renew_loan(db,loan);db.commit();return {"due_date":loan.due_date}
    except RuleViolation as e:db.rollback();raise HTTPException(409,str(e))

@router.get("/me")
def my_account(user:User=Depends(current_user),db:Session=Depends(get_db)):
    if user.role!="member" or not user.member:raise HTTPException(403,"Member account required")
    m=user.member
    loans=db.scalars(select(Loan).options(joinedload(Loan.copy).joinedload(Copy.title)).where(Loan.member_id==m.id,Loan.status=="active")).all()
    fines=db.scalars(select(Fine).join(Loan).where(Loan.member_id==m.id)).all()
    return {"member_code":m.member_code,"borrow_limit":m.borrow_limit,"loans":[{"id":l.id,"title":l.copy.title.title,"barcode":l.copy.barcode,"due_date":l.due_date,"overdue":l.due_date<date.today()} for l in loans],"fines":[{"amount":f.amount,"reason":f.reason,"status":f.status} for f in fines]}

@router.post("/fines/{fine_id}/request-payment")
def member_pay_fine(fine_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    f=db.get(Fine,fine_id)
    if user.role!="member" or not user.member or not f or db.get(Loan,f.loan_id).member_id!=user.member.id:raise HTTPException(404,"Fine not found")
    if f.status!="unpaid": raise HTTPException(409,"Fine is not awaiting payment")
    f.status="payment_requested"
    db.add(Notification(member_id=user.member.id,type="payment_requested",message=f"Payment requested for fine #{f.id}. A librarian must confirm receipt."))
    db.commit();return {"status":f.status,"message":"Payment request sent; a librarian must confirm receipt."}

@router.post("/softcopies",status_code=201)
async def upload_softcopy(title_id:int,access_level:str="all_members",file:UploadFile=File(...),user:User=Depends(roles("admin")),db:Session=Depends(get_db)):
    if access_level not in {"all_members","staff_only"}:raise HTTPException(400,"Invalid access level")
    if not db.get(Title,title_id):raise HTTPException(404,"Title not found")
    if file.content_type not in {"application/pdf","application/epub+zip","application/octet-stream"}:raise HTTPException(400,"Only PDF and EPUB files are supported")
    contents=await file.read(settings.max_upload_mb*1024*1024+1)
    if len(contents)>settings.max_upload_mb*1024*1024:raise HTTPException(413,"File exceeds upload limit")
    suffix=Path(file.filename or "").suffix.lower()
    if suffix==".pdf" and not contents.startswith(b"%PDF-"):raise HTTPException(400,"File content is not a PDF")
    if suffix==".epub" and (not contents.startswith(b"PK") or b"mimetype" not in contents[:4096]):raise HTTPException(400,"File content is not a valid EPUB archive")
    if suffix not in {".pdf",".epub"}:raise HTTPException(400,"Use a .pdf or .epub file")
    stored=secrets.token_hex(24)+Path(file.filename or "book.pdf").suffix.lower()
    Path(settings.upload_dir,stored).write_bytes(contents)
    s=Softcopy(title_id=title_id,stored_name=stored,original_name=Path(file.filename or "softcopy").name,uploaded_by=user.id,access_level=access_level);db.add(s);db.commit();return {"id":s.id,"title_id":title_id,"access_level":access_level}

@router.get("/softcopies/{softcopy_id}/download")
def download_softcopy(softcopy_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    s=db.get(Softcopy,softcopy_id)
    if not s:raise HTTPException(404,"Softcopy not found")
    if s.access_level=="staff_only" and user.role not in {"admin","librarian"}:raise HTTPException(403,"Staff access required")
    if s.access_level=="all_members" and user.role=="member":
        entitled=db.scalar(select(Loan.id).join(Copy).where(Loan.member_id==user.member.id,Copy.title_id==s.title_id)) if user.member else None
        if not entitled:raise HTTPException(403,"Borrow this title to access its softcopy")
    path=Path(settings.upload_dir,s.stored_name).resolve()
    if not path.is_file() or path.parent!=Path(settings.upload_dir).resolve():raise HTTPException(404,"File unavailable")
    return FileResponse(path,filename=s.original_name,media_type="application/octet-stream")

@router.post("/notifications/run")
def run_notifications(user:User=Depends(roles("admin")),db:Session=Depends(get_db)):
    today=date.today();made=0
    loans=db.scalars(select(Loan).where(Loan.status=="active",Loan.due_date<=today+timedelta(days=3))).all()
    for loan in loans:
        kind="overdue" if loan.due_date<today else "due_soon"
        duplicate=db.scalar(select(Notification.id).where(Notification.member_id==loan.member_id,Notification.type==kind,func.date(Notification.sent_at)==today.isoformat()))
        if not duplicate:
            db.add(Notification(member_id=loan.member_id,type=kind,message=f"'{loan.copy.title.title}' is {'overdue' if kind=='overdue' else 'due soon'} (due {loan.due_date})."));made+=1
    db.commit();return {"notifications_created":made,"channel":"in_app"}

