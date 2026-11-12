from dataclasses import dataclass
from urllib.parse import unquote

from fastapi import Header, HTTPException


@dataclass
class User:
    id: int
    role: str
    name: str
    email: str

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"


def current_user(x_user_id: str | None = Header(default=None), x_user_role: str = Header(default=""),
                 x_user_name: str = Header(default=""), x_user_email: str = Header(default="")) -> User:
    """The gateway verified the JWT and forwards who the caller is. Buying needs a login."""
    if not x_user_id or not x_user_id.isdigit():
        raise HTTPException(401, "Please log in first")
    return User(id=int(x_user_id), role=x_user_role, name=unquote(x_user_name), email=unquote(x_user_email))
