"""Media service: receives image uploads, validates and optimises them, serves them back.
Stored on local disk (media_service/storage); metadata in media_db."""
import asyncio
import io
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import Base, ensure_database, engine, get_session
from .models import MediaFile

OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}
ALLOWED = {"JPEG", "PNG", "WEBP", "GIF"}
NAME_RE = re.compile(r"^[a-f0-9]{32}(_thumb)?\.webp$")
Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_database()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Media Service", version="1.0", lifespan=lifespan)


@app.middleware("http")
async def internal_key_guard(request: Request, call_next):
    if request.url.path.startswith("/files/") or request.url.path in OPEN_PATHS:
        if request.url.path.startswith("/files/") and request.headers.get("x-internal-key") != settings.internal_key:
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        return await call_next(request)
    if request.headers.get("x-internal-key") != settings.internal_key:
        return JSONResponse({"detail": "Forbidden: call this service through the gateway"}, status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"service": "media", "status": "ok"}


def _process(data: bytes, name: str) -> dict:
    """CPU work: verify it is a real image, fix rotation, resize, save WEBP + thumbnail."""
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        probe.verify()
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(415, "This file is not a valid image")
    if fmt not in ALLOWED:
        raise HTTPException(415, "Only JPG, PNG, WEBP or GIF images are allowed")
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
    img.thumbnail((settings.max_dimension, settings.max_dimension))
    stem = uuid.uuid4().hex
    path = settings.storage_dir / f"{stem}.webp"
    img.save(path, "WEBP", quality=86, method=4)
    thumb = img.copy()
    thumb.thumbnail((settings.thumb_dimension, settings.thumb_dimension))
    thumb.save(settings.storage_dir / f"{stem}_thumb.webp", "WEBP", quality=80, method=4)
    return {"filename": f"{stem}.webp", "width": img.width, "height": img.height, "size": path.stat().st_size}


def _process_avatar(data: bytes) -> dict:
    """Square 256 px profile picture."""
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        probe.verify()
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(415, "This file is not a valid image")
    if fmt not in ALLOWED:
        raise HTTPException(415, "Only JPG, PNG, WEBP or GIF images are allowed")
    img = ImageOps.fit(ImageOps.exif_transpose(img).convert("RGB"), (256, 256))
    stem = uuid.uuid4().hex
    path = settings.storage_dir / f"{stem}.webp"
    img.save(path, "WEBP", quality=85)
    return {"filename": f"{stem}.webp", "size": path.stat().st_size}


@app.post("/avatar", status_code=201)
async def upload_avatar(file: UploadFile = File(...), x_user_id: str | None = Header(default=None),
                        session: AsyncSession = Depends(get_session)):
    """Any logged-in user may upload a profile picture (max 2 MB)."""
    limit = 2 * 1024 * 1024
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(400, "The file is empty")
    if len(data) > limit:
        raise HTTPException(413, "Picture is too large (max 2 MB)")
    info = await asyncio.to_thread(_process_avatar, data)
    session.add(MediaFile(filename=info["filename"], original_name="avatar", size_bytes=info["size"], width=256, height=256,
                          uploaded_by=int(x_user_id) if x_user_id and x_user_id.isdigit() else None))
    await session.commit()
    return {"url": f"/media/{info['filename']}"}


@app.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...), x_user_id: str | None = Header(default=None),
                 session: AsyncSession = Depends(get_session)):
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(400, "The file is empty")
    if len(data) > limit:
        raise HTTPException(413, f"Image is too large (max {settings.max_upload_mb} MB)")
    info = await asyncio.to_thread(_process, data, file.filename or "")
    session.add(MediaFile(filename=info["filename"], original_name=(file.filename or "")[:200],
                          size_bytes=info["size"], width=info["width"], height=info["height"],
                          uploaded_by=int(x_user_id) if x_user_id and x_user_id.isdigit() else None))
    await session.commit()
    stem = info["filename"][:-5]
    return {"url": f"/media/{info['filename']}", "thumb_url": f"/media/{stem}_thumb.webp",
            "width": info["width"], "height": info["height"], "size": info["size"]}


@app.get("/files/{filename}")
async def get_file(filename: str):
    if not NAME_RE.match(filename):
        raise HTTPException(404, "Not found")
    path = settings.storage_dir / filename
    if not path.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/images")
async def list_images(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(MediaFile).order_by(MediaFile.id.desc()).limit(100))).all()
    return [{"id": m.id, "url": f"/media/{m.filename}", "thumb_url": f"/media/{m.filename[:-5]}_thumb.webp",
             "original_name": m.original_name, "size": m.size_bytes, "width": m.width, "height": m.height,
             "created_at": m.created_at.isoformat()} for m in rows]
