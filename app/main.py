from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.config import settings
from app.database import Base, engine
from app.routes import api, web
from app.routes.common import templates

Base.metadata.create_all(bind=engine)
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Library Management System", version="1.0.0", description="Library catalogue, membership, and circulation API.")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web.router)
app.include_router(api.router)

@app.middleware("http")
async def protect_cookie_mutations(request: Request, call_next):
    is_mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    is_auth_submission = request.url.path in {"/login", "/api/auth/token"}
    response = None
    if is_mutation and (request.cookies.get("lms_token") or is_auth_submission):
        supplied = request.headers.get("origin") or request.headers.get("referer")
        expected = urlsplit(str(request.base_url))
        try:
            actual = urlsplit(supplied) if supplied else None
            same_origin = bool(actual and (expected.scheme, expected.netloc) == (actual.scheme, actual.netloc))
        except ValueError:
            same_origin = False
        if not same_origin:
            response = JSONResponse({"detail": "Same-origin request required"}, status_code=403)
    if response is None:
        response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path in {"/login", "/api/auth/token"}:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response

@app.get("/health")
def health():
    return {"status": "ok"}

@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/") or request.url.path == "/health":
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return HTMLResponse(Jinja2Templates(directory="app/templates").get_template("404.html").render(request=request), status_code=404)

@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if exc.status_code == 303:
        return RedirectResponse(exc.headers.get("Location", "/login"), status_code=303)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    return HTMLResponse(templates.get_template("error.html").render(
        request=request, status_code=exc.status_code, message=exc.detail
    ), status_code=exc.status_code, headers=exc.headers)

@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    message = "Please check the submitted fields and try again."
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=422)
    return HTMLResponse(templates.get_template("error.html").render(
        request=request, status_code=422, message=message
    ), status_code=422)

@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "An unexpected error occurred."}, status_code=500)
    return HTMLResponse(templates.get_template("error.html").render(
        request=request, status_code=500, message="Please try again. If the issue continues, contact your library administrator."
    ), status_code=500)
