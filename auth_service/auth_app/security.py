import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .config import SERVICE_DIR, settings

KEY_DIR = SERVICE_DIR / "keys"
_private_pem: bytes = b""
_public_pem: bytes = b""


def load_keys() -> None:
    """Load the RSA key pair, or create it on first start (RS256 signing)."""
    global _private_pem, _public_pem
    KEY_DIR.mkdir(exist_ok=True)
    priv, pub = KEY_DIR / "private.pem", KEY_DIR / "public.pem"
    if not priv.exists() or not pub.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        pub.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    _private_pem, _public_pem = priv.read_bytes(), pub.read_bytes()


def public_key_pem() -> str:
    return _public_pem.decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def _make_token(user, token_type: str, ttl: timedelta, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id), "username": user.username, "role": user.role,
        "type": token_type, "jti": uuid.uuid4().hex, "iat": now, "exp": now + ttl,
        **(extra or {}),
    }
    return jwt.encode(payload, _private_pem, algorithm="RS256")


def make_access_token(user) -> tuple[str, int]:
    ttl = timedelta(minutes=settings.access_ttl_min)
    extra = {"name": user.name or user.username, "email": user.email or "", "picture": user.avatar_url or ""}
    return _make_token(user, "access", ttl, extra), int(ttl.total_seconds())


def make_refresh_token(user) -> str:
    return _make_token(user, "refresh", timedelta(days=settings.refresh_ttl_days))


def decode_token(token: str, expected_type: str) -> dict:
    """Raises jwt.PyJWTError when invalid, expired or of the wrong type."""
    data = jwt.decode(token, _public_pem, algorithms=["RS256"])
    if data.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return data
