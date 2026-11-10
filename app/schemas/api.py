from pydantic import BaseModel, Field

class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    author: str = Field(min_length=1, max_length=200)
    isbn: str | None = None
    category: str = "General"
    edition: str | None = None
    publisher: str | None = None
    published_year: int | None = Field(default=None, ge=1000, le=2200)
    description: str | None = None

class CopyIn(BaseModel):
    title_id: int
    barcode: str = Field(min_length=1, max_length=80)
    qr_code: str | None = None
    rfid_tag: str | None = None
    shelf_id: int | None = None
    replacement_cost: float = Field(default=0, ge=0)

class MemberIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=128)
    member_code: str = Field(min_length=1, max_length=40)
    borrow_limit: int = Field(default=5, ge=1, le=50)

class CirculationIn(BaseModel):
    member_id: int | None = None
    copy_id: int | None = None
    barcode: str | None = None

class ReturnIn(BaseModel):
    condition: str = "ok"
