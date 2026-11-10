from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from app.config import settings
from app.database import get_db
from app.models import (Copy, Fine, Loan, LossDamageLog, Member, Notification, Reservation, Shelf, Title, User)
from app.security import hash_password, verify_password, create_token, revoke_token, consume_login_attempt, clear_login_attempts
from app.services import RuleViolation, issue_copy, return_loan, renew_loan
from app.routes.common import templates, page_user, page_role

router = APIRouter()

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request): return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": None})

@router.post("/login", response_class=HTMLResponse)
def login_form(request: Request, email: str = Form(max_length=255), password: str = Form(max_length=128), db: Session = Depends(get_db)):
    client_key = f"{request.client.host if request.client else 'unknown'}:{email.lower().strip()}"
    retry_after = consume_login_attempt(client_key)
    if retry_after:
        raise HTTPException(429, "Too many sign-in attempts; try again later", headers={"Retry-After": str(retry_after)})
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if not user or user.status != "active" or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Email or password is incorrect."}, status_code=401)
    clear_login_attempts(client_key)
    response = RedirectResponse("/", status_code=303); response.set_cookie("lms_token", create_token(user), httponly=True, samesite="lax", secure=True, max_age=settings.access_token_minutes*60, path="/")
    return response

@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("lms_token")
    if token:
        try:
            revoke_token(token, db)
        except Exception:
            db.rollback()
    r = RedirectResponse("/login", status_code=303); r.delete_cookie("lms_token", httponly=True, samesite="lax", secure=True, path="/"); return r

@router.get("/", response_class=HTMLResponse)
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

@router.get("/books", response_class=HTMLResponse)
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

@router.post("/books/create")
def book_create(request:Request,title:str=Form(),author:str=Form(),isbn:str=Form(""),category:str=Form("General"),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    t=Title(title=title.strip(),author=author.strip(),isbn=isbn.strip() or None,category=category.strip() or "General")
    db.add(t)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A title with that ISBN already exists")
    return RedirectResponse("/books?message=Saved+successfully",303)

@router.post("/copies/create")
def copy_create(request:Request,title_id:int=Form(),barcode:str=Form(),shelf_id:int|None=Form(None),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    db.add(Copy(title_id=title_id,barcode=barcode.strip(),shelf_id=shelf_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That barcode is already assigned")
    return RedirectResponse("/books?message=Copy+added+successfully",303)

@router.post("/titles/{title_id}/outdated")
def outdated(request:Request,title_id:int,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian"); t=db.get(Title,title_id)
    if not t: raise HTTPException(404,"Title not found")
    t.is_outdated_flag=not t.is_outdated_flag; db.commit(); return RedirectResponse("/books?message=Collection+review+status+updated",303)

@router.get("/members",response_class=HTMLResponse)
def members_page(request:Request,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    members=db.scalars(select(Member).options(joinedload(Member.user)).order_by(Member.member_code)).all()
    loan_counts={m.id:(db.scalar(select(func.count()).select_from(Loan).where(Loan.member_id==m.id,Loan.status=="active")) or 0) for m in members}
    return templates.TemplateResponse(request=request, name="members.html", context={"request":request,"user":user,"members":members,"loan_counts":loan_counts})

@router.post("/members/create")
def member_create(request:Request,name:str=Form(),email:str=Form(),member_code:str=Form(),password:str=Form(),borrow_limit:int=Form(5),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    try:
        u=User(name=name.strip(),email=email.lower().strip(),password_hash=hash_password(password),role="member");db.add(u);db.flush()
        db.add(Member(user_id=u.id,member_code=member_code.strip(),borrow_limit=borrow_limit));db.commit()
    except (ValueError,IntegrityError):
        db.rollback();raise HTTPException(400,"Check password length and ensure email/member code are unique")
    return RedirectResponse("/members?message=Member+added+successfully",303)

@router.get("/circulation",response_class=HTMLResponse)
def circulation_page(request:Request,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    members=db.scalars(select(Member).options(joinedload(Member.user)).order_by(Member.member_code)).all()
    loans=db.scalars(select(Loan).options(joinedload(Loan.copy).joinedload(Copy.title),joinedload(Loan.member).joinedload(Member.user)).where(Loan.status=="active").order_by(Loan.due_date)).all()
    error=request.query_params.get("error")
    return templates.TemplateResponse(request=request, name="circulation.html", context={"request":request,"user":user,"members":members,"loans":loans ,"today":date.today(),"error":error})

@router.post("/circulation/issue")
def issue_form(request:Request,member_id:int=Form(),barcode:str=Form(),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    member=db.get(Member,member_id);copy=db.scalar(select(Copy).where(Copy.barcode==barcode.strip()))
    if not member or not copy:return RedirectResponse("/circulation?error=Member+or+barcode+not+found",303)
    try: issue_copy(db,member,copy,user);db.commit()
    except RuleViolation as e:db.rollback();return RedirectResponse("/circulation?error="+str(e).replace(" ","+"),303)
    except IntegrityError:db.rollback();return RedirectResponse("/circulation?error=Copy+changed+while+issuing.+Refresh+and+retry",303)
    return RedirectResponse("/circulation?message=Copy+issued+successfully",303)

@router.post("/reservations/create")
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

@router.post("/circulation/{loan_id}/return")
def return_form(request:Request,loan_id:int,condition:str=Form("ok"),db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian");loan=db.get(Loan,loan_id)
    if not loan:raise HTTPException(404,"Loan not found")
    try:return_loan(db,loan,user,condition);db.commit()
    except RuleViolation as e:db.rollback();raise HTTPException(400,str(e))
    return RedirectResponse("/circulation?message=Copy+returned+successfully",303)

@router.post("/circulation/{loan_id}/renew")
def renew_form(request:Request,loan_id:int,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian");loan=db.get(Loan,loan_id)
    if not loan:raise HTTPException(404,"Loan not found")
    try:renew_loan(db,loan);db.commit()
    except RuleViolation as e:db.rollback();raise HTTPException(400,str(e))
    return RedirectResponse("/circulation?message=Loan+renewed+successfully",303)

@router.get("/fines",response_class=HTMLResponse)
def fines_page(request:Request,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    fines=db.scalars(select(Fine).options(joinedload(Fine.loan).joinedload(Loan.member).joinedload(Member.user),joinedload(Fine.loan).joinedload(Loan.copy).joinedload(Copy.title)).order_by(desc(Fine.created_at))).all()
    return templates.TemplateResponse(request=request, name="fines.html", context={"request":request,"user":user,"fines":fines})

@router.post("/fines/{fine_id}/pay")
def pay_fine(request:Request,fine_id:int,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    f=db.get(Fine,fine_id)
    if not f:raise HTTPException(404,"Fine not found")
    if f.status not in {"unpaid", "payment_requested"}: raise HTTPException(409,"Fine is already settled")
    f.status="paid";f.paid_at=datetime.utcnow();db.commit();return RedirectResponse("/fines?message=Payment+recorded+successfully",303)

@router.post("/fines/{fine_id}/request-payment")
def request_fine_payment(request:Request,fine_id:int,db:Session=Depends(get_db)):
    user=page_user(request,db)
    if user.role!="member" or not user.member: raise HTTPException(403,"Member account required")
    fine=db.get(Fine,fine_id)
    if not fine or db.get(Loan,fine.loan_id).member_id!=user.member.id: raise HTTPException(404,"Fine not found")
    if fine.status!="unpaid": raise HTTPException(409,"Fine is not awaiting payment")
    fine.status="payment_requested"
    db.add(Notification(member_id=user.member.id,type="payment_requested",message=f"Payment requested for fine #{fine.id}. A librarian must confirm receipt."))
    db.commit();return RedirectResponse("/?message=Payment+request+sent",303)

@router.get("/reports",response_class=HTMLResponse)
def reports_page(request:Request,db:Session=Depends(get_db)):
    user=page_user(request,db);page_role(user,"admin","librarian")
    overdue=db.scalars(select(Loan).options(joinedload(Loan.copy).joinedload(Copy.title),joinedload(Loan.member).joinedload(Member.user)).where(Loan.status=="active",Loan.due_date<date.today()).order_by(Loan.due_date)).all()
    low_use=db.scalars(select(Title).where(Title.deleted_at.is_(None),Title.is_outdated_flag.is_(True))).all()
    losses=db.scalars(select(LossDamageLog).options(joinedload(LossDamageLog.copy).joinedload(Copy.title)).order_by(desc(LossDamageLog.created_at))).all()
    most=db.execute(select(Title.title,func.count(Loan.id).label("n")).select_from(Title).join(Copy,Copy.title_id==Title.id).join(Loan,Loan.copy_id==Copy.id).group_by(Title.id).order_by(desc("n")).limit(10)).all()
    return templates.TemplateResponse(request=request, name="reports.html", context={"request":request,"user":user,"overdue":overdue,"low_use":low_use,"losses":losses,"most":most})

