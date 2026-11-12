from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class LoginIn(BaseModel):
    username: str  # username or email
    password: str


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    phone: str = Field(default="", max_length=30)


class RefreshIn(BaseModel):
    refresh_token: str


class OAuthCallbackIn(BaseModel):
    code: str
    redirect_uri: str


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(default="", max_length=30)
    avatar_url: str | None = Field(default=None, max_length=500)   # only "/media/..." (our own uploads) or ""


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=8, max_length=72)


class AdminUserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    phone: str = Field(default="", max_length=30)
    role: Literal["ADMIN", "CUSTOMER"] = "CUSTOMER"


class AdminUserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    role: Literal["ADMIN", "CUSTOMER"] | None = None
    is_active: bool | None = None


class AdminResetPassword(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=72)   # empty = generate a temporary one


class UserOut(BaseModel):
    id: int
    username: str
    name: str
    email: str
    phone: str
    avatar_url: str
    provider: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
