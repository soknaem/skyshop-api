import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    price: float = Field(gt=0)
    compare_price: float | None = Field(default=None, gt=0)
    stock_qty: int = Field(ge=0)
    brand: str = Field(default="", max_length=80)
    category_id: int | None = None
    warranty: str = Field(default="", max_length=60)
    featured: bool = False
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"
    specs: dict[str, str] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _clean(self):
        if self.compare_price is not None and self.compare_price <= self.price:
            self.compare_price = None  # only keep it when it is a real "old price"
        self.specs = {k.strip(): v.strip() for k, v in list(self.specs.items())[:30] if k.strip() and v.strip()}
        self.images = [i.strip() for i in self.images if i.strip()][:8]
        return self


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(default="", max_length=80)
    icon: str = Field(default="package", max_length=30)
    sort_order: int = 0


class CartItemIn(BaseModel):
    product_id: int
    qty: int = Field(default=1, ge=1, le=999)


class CartItemPatch(BaseModel):
    qty: int = Field(ge=0, le=999)  # 0 removes the item


class CheckoutIn(BaseModel):
    fulfillment: Literal["PICKUP", "DELIVERY"]
    full_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    shipping_address: str | None = Field(default=None, max_length=500)

    @field_validator("full_name", "email", "phone", "shipping_address", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if v is not None and not EMAIL_RE.match(v):
            raise ValueError("Email address is not valid")
        return v


class StatusIn(BaseModel):
    status: Literal["PROCESSING", "READY", "OUT_FOR_DELIVERY", "COMPLETED"]
