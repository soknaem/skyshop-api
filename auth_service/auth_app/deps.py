from dataclasses import dataclass
from urllib.parse import unquote

from fastapi import Header, HTTPException


@dataclass
class Actor:
    id: int
    role: str
    name: str


def actor(x_user_id: str | None = Header(default=None), x_user_role: str = Header(default=""),
          x_user_name: str = Header(default="")) -> Actor:
    """Who is calling (the gateway verified the JWT and forwards it in headers)."""
    if not x_user_id or not x_user_id.isdigit():
        raise HTTPException(401, "Not authenticated")
    return Actor(int(x_user_id), x_user_role, unquote(x_user_name))
