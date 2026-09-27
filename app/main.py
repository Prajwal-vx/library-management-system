from datetime import date, datetime, timedelta
from pathlib import Path
import secrets
import hashlib
from urllib.parse import urlsplit
from fastapi import FastAPI, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from app.config import settings
from app.database import Base, engine, get_db
from app.models import *
from app.security import (current_user, roles, verify_password, create_token, hash_password,
                          revoke_token, consume_login_attempt, clear_login_attempts)
from app.services import RuleViolation, issue_copy, return_loan, renew_loan, calculate_fine

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Library Management System", version="1.0.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)

@app.middleware("http")
async def protect_cookie_mutations(request: Request, call_next):
    # Browser forms authenticate with an HttpOnly cookie. Require same-origin
    # provenance for any state change so sibling sites cannot forge desk actions.
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.cookies.get("lms_token"):
        supplied = request.headers.get("origin") or request.headers.get("referer")
        expected = urlsplit(str(request.base_url))
        actual = urlsplit(supplied) if supplied else None
        if not actual or (expected.scheme, expected.netloc) != (actual.scheme, actual.netloc):
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
    return await call_next(request)

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

def commit_or_409(db, fn):
    try:
        result = fn(); db.commit(); return result
    except RuleViolation as e:
        db.rollback(); raise HTTPException(409, str(e))
    except Exception:
        db.rollback(); raise

@app.get("/health")
def health(): return {"status": "ok"}

@app.post("/api/auth/token")
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

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request): return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": None})

@app.post("/login", response_class=HTMLResponse)
def login_form(request: Request, email: str = Form(max_length=255), password: str = Form(max_length=128), db: Session = Depends(get_db)):
    client_key = request.client.host if request.client else "unknown"
    retry_after = consume_login_attempt(client_key)
    if retry_after:
        raise HTTPException(429, "Too many sign-in attempts; try again later", headers={"Retry-After": str(retry_after)})
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if not user or user.status != "active" or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Email or password is incorrect."}, status_code=401)
    clear_login_attempts(client_key)
    response = RedirectResponse("/", status_code=303); response.set_cookie("lms_token", create_token(user), httponly=True, samesite="strict", secure=settings.cookie_secure, max_age=settings.access_token_minutes*60)
    return response

def page_user(request: Request, db: Session) -> User:
    token = request.cookies.get("lms_token")
    from fastapi.security import HTTPAuthorizationCredentials
    import jwt
    if not token: raise HTTPException(303, headers={"Location": "/login"})
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if db.get(RevokedToken, hashlib.sha256(token.encode("utf-8")).hexdigest()):
            raise ValueError("Session has been revoked")
        u = db.get(User, int(payload["sub"]))
        if not u or u.status != "active": raise ValueError()
        return u
    except Exception: raise HTTPException(303, headers={"Location": "/login"})

def page_role(user: User, *allowed):
    if user.role not in allowed: raise HTTPException(403, "Access denied")

@app.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("lms_token")
    if token:
        try:
            revoke_token(token, db)
        except Exception:
            db.rollback()
    r = RedirectResponse("/login", status_code=303); r.delete_cookie("lms_token", httponly=True, samesite="strict", secure=settings.cookie_secure, path="/"); return r

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = page_user(request, db)
    if user.role == "member":
        member = db.scalar(select(Member).where(Member.user_id == user.id))
        loans = db.scalars(select(Loan).options(joinedload(Loan.copy).joinedload(Copy.title)).where(Loan.member_id == member.id, Loan.status == "active")).all()
        fines = db.scalars(select(Fine).join(Loan).where(Loan.member_id == member.id, Fine.status.in_(["unpaid", "payment_requested"]))).all()
        return templates.TemplateResponse(request=request, name="member.html", context={"request":request,"user":user,"loans":loans,"fines":fines,"today":date.today()})
    cards = {"titles": db.scalar(select(func.count()).select_from(Title).where(Title.deleted_at.is_(None))) or 0,
             "copies": db.scalar(select(func.count()).select_from(Copy)) or 0,
             "issued": db.scalar(select(func.count()).select_from(Copy).where(Copy.status=="issued")) or 0,
             "members": db.scalar(select(func.count()).select_from(Member).where(Member.membership_status=="active")) or 0,
             "overdue": db.scalar(select(func.count()).select_from(Loan).where(Loan.status=="active",Loan.due_date<date.today())) or 0}
    recent = db.scalars(select(Loan).options(joinedload(Loan.copy).joinedload(Copy.title),joinedload(Loan.member).joinedload(Member.user)).order_by(desc(Loan.created_at)).limit(10)).all()
    top = db.execute(select(Title.title,func.count(Loan.id).label("n")).select_from(Title).join(Copy,Copy.title_id==Title.id).join(Loan,Loan.copy_id==Copy.id).group_by(Title.id).order_by(desc("n")).limit(5)).all()
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"request":request,"user":user,"cards":cards,"recent":recent,"top":top})

@app.get("/books", response_class=HTMLResponse)
def books_page(request:Request,q:str="",db:Session=Depends(get_db)):
    user=page_user(request,db); page_role(user,"admin","librarian","member")
    query=select(Title).where(Title.deleted_at.is_(None))
    if q: query=query.where((Title.title.ilike(f"%{q}%"))|(Title.author.ilike(f"%{q}%"))|(Title.isbn.ilike(f"%{q}%")))
    titles=db.scalars(query.order_by(Title.title).limit(300)).all()
    title_ids=[t.id for t in titles]
    counts={title_id:0 for title_id in title_ids}
    counts.update(dict(db.execute(select(Copy.title_id,func.count(Copy.id)).where(Copy.status=="available",Copy.title_id.in_(title_ids)).group_by(Copy.title_id)).all()) if title_ids else {})
    copies_by_title={title_id:[] for title_id in title_ids}
    copies=db.scalars(select(Copy).options(joinedload(Copy.shelf)).where(Copy.title_id.in_(title_ids)).order_by(Copy.barcode)).all() if title_ids else []
    for copy in copies: copies_by_title[copy.title_id].append(copy)
    shelves=db.scalars(select(Shelf).order_by(Shelf.shelf_code)).all()
    return templates.TemplateResponse(request=request, name="books.html", context={"request":request,"user":user,"titles":titles,"counts":counts,"copies_by_title":copies_by_title,"shelves":shelves,"q":q})

@app.post("/books/create")
def book_create(request:Request,title:str=Form(),author:str=Form(),isbn:str=Form(""),category:str=Form("General"),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    t=Title(title=title.strip(),author=author.strip(),isbn=isbn.strip() or None,category=category.strip() or "General"); db.add(t); db.commit(); return RedirectResponse("/books",303)

@app.post("/copies/create")
def copy_create(request:Request,title_id:int=Form(),barcode:str=Form(),shelf_id:int|None=Form(None),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    db.add(Copy(title_id=title_id,barcode=barcode.strip(),shelf_id=shelf_id)); db.commit(); return RedirectResponse("/books",303)

@app.post("/titles/{title_id}/outdated")
def outdated(request:Request,title_id:int,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian"); t=db.get(Title,title_id)
    if not t: raise HTTPException(404,"Title not found")
    t.is_outdated_flag=not t.is_outdated_flag; db.commit(); return RedirectResponse("/books",303)

@app.get("/members",response_class=HTMLResponse)
def members_page(request:Request,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    members=db.scalars(select(Member).options(joinedload(Member.user)).order_by(Member.member_code)).all()
    loan_counts={m.id:(db.scalar(select(func.count()).select_from(Loan).where(Loan.member_id==m.id,Loan.status=="active")) or 0) for m in members}
    return templates.TemplateResponse(request=request, name="members.html", context={"request":request,"user":user,"members":members,"loan_counts":loan_counts})

@app.post("/members/create")
def member_create(request:Request,name:str=Form(),email:str=Form(),member_code:str=Form(),password:str=Form(),borrow_limit:int=Form(5),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    try:
        u=User(name=name.strip(),email=email.lower().strip(),password_hash=hash_password(password),role="member");db.add(u);db.flush()
        db.add(Member(user_id=u.id,member_code=member_code.strip(),borrow_limit=borrow_limit));db.commit()
    except (ValueError,IntegrityError) as e:
        db.rollback();raise HTTPException(400,"Check password length and ensure email/member code are unique")
    return RedirectResponse("/members",303)

@app.get("/circulation",response_class=HTMLResponse)
def circulation_page(request:Request,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    members=db.scalars(select(Member).options(joinedload(Member.user)).order_by(Member.member_code)).all()
    loans=db.scalars(select(Loan).options(joinedload(Loan.copy).joinedload(Copy.title),joinedload(Loan.member).joinedload(Member.user)).where(Loan.status=="active").order_by(Loan.due_date)).all()
    copies=db.scalars(select(Copy).options(joinedload(Copy.title)).where(Copy.status=="available").order_by(Copy.barcode)).all()
    error=request.query_params.get("error")
    return templates.TemplateResponse(request=request, name="circulation.html", context={"request":request,"user":user,"members":members,"loans":loans,"copies":copies,"today":date.today(),"error":error})

@app.post("/circulation/issue")
def issue_form(request:Request,member_id:int=Form(),barcode:str=Form(),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    member=db.get(Member,member_id);copy=db.scalar(select(Copy).where(Copy.barcode==barcode.strip()))
    if not member or not copy:return RedirectResponse("/circulation?error=Member+or+barcode+not+found",303)
    try: issue_copy(db,member,copy,user);db.commit()
    except RuleViolation as e:db.rollback();return RedirectResponse("/circulation?error="+str(e).replace(" ","+"),303)
    except IntegrityError:db.rollback();return RedirectResponse("/circulation?error=Copy+changed+while+issuing.+Refresh+and+retry",303)
    return RedirectResponse("/circulation",303)

@app.post("/reservations/create")
def reservation_form(request:Request,title_id:int=Form(),db:Session=Depends(get_db)):
    user=page_user(request,db)
    if user.role!="member" or not user.member or user.member.membership_status!="active":raise HTTPException(403,"Active member account required")
    title=db.get(Title,title_id)
    if not title or title.deleted_at:raise HTTPException(404,"Title not found")
    existing=db.scalar(select(Reservation.id).where(Reservation.title_id==title_id,Reservation.member_id==user.member.id,Reservation.status.in_(["pending","ready"])))
    if existing:return RedirectResponse("/books?message=You+already+have+a+reservation",303)
    hold=Reservation(title_id=title_id,member_id=user.member.id);db.add(hold);db.flush()
    available=db.scalar(select(Copy).where(Copy.title_id==title_id,Copy.status=="available").order_by(Copy.id).with_for_update())
    if available:
        hold.status="ready";hold.ready_copy_id=available.id;available.status="reserved"
        db.add(Notification(member_id=user.member.id,type="reservation_ready",message=f"A copy of '{title.title}' is ready for pickup. Barcode: {available.barcode}."))
    db.commit();return RedirectResponse("/books?message=Reservation+saved",303)

@app.post("/circulation/{loan_id}/return")
def return_form(request:Request,loan_id:int,condition:str=Form("ok"),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian");loan=db.get(Loan,loan_id)
    if not loan:raise HTTPException(404,"Loan not found")
    try:return_loan(db,loan,user,condition);db.commit()
    except RuleViolation as e:db.rollback();raise HTTPException(400,str(e))
    return RedirectResponse("/circulation",303)

@app.post("/circulation/{loan_id}/renew")
def renew_form(request:Request,loan_id:int,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian");loan=db.get(Loan,loan_id)
    if not loan:raise HTTPException(404,"Loan not found")
    try:renew_loan(db,loan);db.commit()
    except RuleViolation as e:db.rollback();raise HTTPException(400,str(e))
    return RedirectResponse("/circulation",303)

@app.get("/fines",response_class=HTMLResponse)
def fines_page(request:Request,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    fines=db.scalars(select(Fine).options(joinedload(Fine.loan).joinedload(Loan.member).joinedload(Member.user),joinedload(Fine.loan).joinedload(Loan.copy).joinedload(Copy.title)).order_by(desc(Fine.created_at))).all()
    return templates.TemplateResponse(request=request, name="fines.html", context={"request":request,"user":user,"fines":fines})

@app.post("/fines/{fine_id}/pay")
def pay_fine(request:Request,fine_id:int,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    f=db.get(Fine,fine_id)
    if not f:raise HTTPException(404,"Fine not found")
    if f.status not in {"unpaid", "payment_requested"}: raise HTTPException(409,"Fine is already settled")
    f.status="paid";f.paid_at=datetime.utcnow();db.commit();return RedirectResponse("/fines",303)

@app.post("/fines/{fine_id}/request-payment")
def request_fine_payment(request:Request,fine_id:int,db:Session=Depends(get_db)):
    user=page_user(request,db)
    if user.role!="member" or not user.member: raise HTTPException(403,"Member account required")
    fine=db.get(Fine,fine_id)
    if not fine or db.get(Loan,fine.loan_id).member_id!=user.member.id: raise HTTPException(404,"Fine not found")
    if fine.status!="unpaid": raise HTTPException(409,"Fine is not awaiting payment")
    fine.status="payment_requested"
    db.add(Notification(member_id=user.member.id,type="payment_requested",message=f"Payment requested for fine #{fine.id}. A librarian must confirm receipt."))
    db.commit();return RedirectResponse("/",303)

@app.get("/reports",response_class=HTMLResponse)
def reports_page(request:Request,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    overdue=db.scalars(select(Loan).options(joinedload(Loan.copy).joinedload(Copy.title),joinedload(Loan.member).joinedload(Member.user)).where(Loan.status=="active",Loan.due_date<date.today()).order_by(Loan.due_date)).all()
    low_use=db.scalars(select(Title).where(Title.deleted_at.is_(None),Title.is_outdated_flag.is_(True))).all()
    losses=db.scalars(select(LossDamageLog).options(joinedload(LossDamageLog.copy).joinedload(Copy.title)).order_by(desc(LossDamageLog.created_at))).all()
    most=db.execute(select(Title.title,func.count(Loan.id).label("n")).select_from(Title).join(Copy,Copy.title_id==Title.id).join(Loan,Loan.copy_id==Copy.id).group_by(Title.id).order_by(desc("n")).limit(10)).all()
    return templates.TemplateResponse(request=request, name="reports.html", context={"request":request,"user":user,"overdue":overdue,"low_use":low_use,"losses":losses,"most":most})

@app.get("/api/dashboard")
def api_dashboard(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return {"total_titles":db.scalar(select(func.count()).select_from(Title).where(Title.deleted_at.is_(None))),"total_copies":db.scalar(select(func.count()).select_from(Copy)),"issued_books":db.scalar(select(func.count()).select_from(Copy).where(Copy.status=="issued")),"active_members":db.scalar(select(func.count()).select_from(Member).where(Member.membership_status=="active")),"overdue_books":db.scalar(select(func.count()).select_from(Loan).where(Loan.status=="active",Loan.due_date<date.today()))}

@app.get("/api/titles")
def list_titles(q:str="",user:User=Depends(current_user),db:Session=Depends(get_db)):
    stmt=select(Title).where(Title.deleted_at.is_(None))
    if q:stmt=stmt.where((Title.title.ilike(f"%{q}%"))|(Title.author.ilike(f"%{q}%"))|(Title.isbn.ilike(f"%{q}%")))
    return [{"id":t.id,"title":t.title,"author":t.author,"isbn":t.isbn,"category":t.category,"available":db.scalar(select(func.count()).select_from(Copy).where(Copy.title_id==t.id,Copy.status=="available"))} for t in db.scalars(stmt.limit(300)).all()]

@app.post("/api/titles",status_code=201)
def create_title(data:TitleIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    t=Title(**data.model_dump());db.add(t)
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"A title with that ISBN already exists")
    db.refresh(t);return {"id":t.id,"title":t.title}

@app.patch("/api/titles/{title_id}")
def update_title(title_id:int,data:TitleIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    t=db.get(Title,title_id)
    if not t or t.deleted_at:raise HTTPException(404,"Title not found")
    for k,v in data.model_dump().items():setattr(t,k,v)
    db.commit();return {"id":t.id,"title":t.title}

@app.delete("/api/titles/{title_id}",status_code=204)
def archive_title(title_id:int,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    t=db.get(Title,title_id)
    if not t:raise HTTPException(404,"Title not found")
    if db.scalar(select(Loan.id).join(Copy).where(Copy.title_id==title_id,Loan.status=="active")):raise HTTPException(409,"Title has active loans")
    t.deleted_at=datetime.utcnow();db.commit()

@app.post("/api/copies",status_code=201)
def create_copy(data:CopyIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    if not db.get(Title,data.title_id):raise HTTPException(404,"Title not found")
    c=Copy(**data.model_dump());db.add(c)
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"Barcode or RFID tag is already assigned")
    db.refresh(c);return {"id":c.id,"barcode":c.barcode}

@app.get("/api/copies/{barcode}/location")
def find_copy(barcode:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    c=db.scalar(select(Copy).options(joinedload(Copy.title),joinedload(Copy.shelf)).where(Copy.barcode==barcode))
    if not c:raise HTTPException(404,"Copy not found")
    return {"barcode":c.barcode,"title":c.title.title,"status":c.status,"shelf":c.shelf.shelf_code if c.shelf else None,"section":c.shelf.section if c.shelf else None,"aisle":c.shelf.aisle if c.shelf else None}

@app.post("/api/members",status_code=201)
def create_member(data:MemberIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    u=User(name=data.name.strip(),email=data.email.lower().strip(),password_hash=hash_password(data.password),role="member");db.add(u);db.flush();m=Member(user_id=u.id,member_code=data.member_code,borrow_limit=data.borrow_limit);db.add(m)
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"Email or member code is already in use")
    return {"id":m.id,"member_code":m.member_code}

@app.post("/api/circulation/issue",status_code=201)
def api_issue(data:CirculationIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    m=db.get(Member,data.member_id) if data.member_id else None
    c=db.get(Copy,data.copy_id) if data.copy_id else db.scalar(select(Copy).where(Copy.barcode==data.barcode)) if data.barcode else None
    if not m or not c:raise HTTPException(404,"Member or copy not found")
    try:
        loan=issue_copy(db,m,c,user);db.commit();return {"loan_id":loan.id,"due_date":loan.due_date}
    except RuleViolation as e:db.rollback();raise HTTPException(409,str(e))
    except IntegrityError:db.rollback();raise HTTPException(409,"Copy was issued concurrently; reload and retry")

@app.post("/api/circulation/{loan_id}/return")
def api_return(loan_id:int,data:ReturnIn,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if not loan:raise HTTPException(404,"Loan not found")
    try:
        fine=return_loan(db,loan,user,data.condition);db.commit();return {"loan_id":loan.id,"fine":fine.amount if fine else 0,"status":loan.status}
    except RuleViolation as e:db.rollback();raise HTTPException(409,str(e))

@app.post("/api/circulation/{loan_id}/renew")
def api_renew(loan_id:int,user:User=Depends(roles("admin","librarian")),db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if not loan:raise HTTPException(404,"Loan not found")
    try:renew_loan(db,loan);db.commit();return {"due_date":loan.due_date,"renewals":loan.renewals}
    except RuleViolation as e:db.rollback();raise HTTPException(409,str(e))

@app.post("/api/reservations",status_code=201)
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

@app.post("/api/loans/{loan_id}/renew")
def member_renew(loan_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    loan=db.get(Loan,loan_id)
    if user.role!="member" or not user.member or not loan or loan.member_id!=user.member.id:raise HTTPException(404,"Loan not found")
    try:renew_loan(db,loan);db.commit();return {"due_date":loan.due_date}
    except RuleViolation as e:db.rollback();raise HTTPException(409,str(e))

@app.get("/api/me")
def my_account(user:User=Depends(current_user),db:Session=Depends(get_db)):
    if user.role!="member" or not user.member:raise HTTPException(403,"Member account required")
    m=user.member
    loans=db.scalars(select(Loan).options(joinedload(Loan.copy).joinedload(Copy.title)).where(Loan.member_id==m.id,Loan.status=="active")).all()
    fines=db.scalars(select(Fine).join(Loan).where(Loan.member_id==m.id)).all()
    return {"member_code":m.member_code,"borrow_limit":m.borrow_limit,"loans":[{"id":l.id,"title":l.copy.title.title,"barcode":l.copy.barcode,"due_date":l.due_date,"overdue":l.due_date<date.today()} for l in loans],"fines":[{"amount":f.amount,"reason":f.reason,"status":f.status} for f in fines]}

@app.post("/api/fines/{fine_id}/request-payment")
def member_pay_fine(fine_id:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    f=db.get(Fine,fine_id)
    if user.role!="member" or not user.member or not f or db.get(Loan,f.loan_id).member_id!=user.member.id:raise HTTPException(404,"Fine not found")
    if f.status!="unpaid": raise HTTPException(409,"Fine is not awaiting payment")
    f.status="payment_requested"
    db.add(Notification(member_id=user.member.id,type="payment_requested",message=f"Payment requested for fine #{f.id}. A librarian must confirm receipt."))
    db.commit();return {"status":f.status,"message":"Payment request sent; a librarian must confirm receipt."}

@app.post("/api/softcopies",status_code=201)
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

@app.get("/api/softcopies/{softcopy_id}/download")
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

@app.post("/api/notifications/run")
def run_notifications(user:User=Depends(roles("admin")),db:Session=Depends(get_db)):
    today=date.today();made=0
    loans=db.scalars(select(Loan).where(Loan.status=="active",Loan.due_date<=today+timedelta(days=3))).all()
    for loan in loans:
        kind="overdue" if loan.due_date<today else "due_soon"
        duplicate=db.scalar(select(Notification.id).where(Notification.member_id==loan.member_id,Notification.type==kind,func.date(Notification.sent_at)==today.isoformat()))
        if not duplicate:
            db.add(Notification(member_id=loan.member_id,type=kind,message=f"'{loan.copy.title.title}' is {'overdue' if kind=='overdue' else 'due soon'} (due {loan.due_date})."));made+=1
    db.commit();return {"notifications_created":made,"channel":"in_app"}
