"""KHQR adapter. Works with an NBC token (eyJ...), a KHQR.dev token (rbk...) or mock mode."""
import hashlib
import io
import base64
import time
import uuid

from .config import settings

_client = None
IS_RELAY = settings.khqr_token.startswith("rbk")


def _get_client():
    global _client
    if _client is None:
        from bakong_khqr import KHQR
        _client = KHQR(settings.khqr_token) if settings.khqr_token else KHQR()
    return _client


def _png_data_uri(text: str) -> str:
    import qrcode
    buf = io.BytesIO()
    qrcode.make(text).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def create(amount: float, bill_number: str) -> dict:
    """Blocking call. Returns {qr, md5, tran_id, image}."""
    if settings.mock_mode:
        qr = f"MOCK-KHQR|{uuid.uuid4().hex}|{amount}|{settings.shop_currency}"
        md5 = hashlib.md5(qr.encode()).hexdigest()
        return {"qr": qr, "md5": md5, "tran_id": bill_number, "image": _png_data_uri(qr)}

    khqr = _get_client()
    if IS_RELAY:
        res = khqr.create_qr(amount=amount)
        qr, md5, tran_id = str(res), res.md5, res.tran_id
    else:
        if not settings.khqr_account_id:
            raise RuntimeError("KHQR_ACCOUNT_ID is required for an NBC token")
        qr = str(khqr.create_qr(
            account_id=settings.khqr_account_id,
            merchant_name=settings.khqr_merchant_name,
            merchant_city=settings.khqr_merchant_city,
            amount=amount,
            currency=settings.shop_currency,
            store_label=settings.khqr_store_label,
            phone_number=settings.khqr_phone or None,
            bill_number=bill_number,
            terminal_label=settings.khqr_terminal_label,
            static=False,
            expiration=settings.khqr_expiration_days,
        ))
        md5, tran_id = khqr.generate_md5(qr), bill_number
    return {"qr": qr, "md5": md5, "tran_id": tran_id, "image": khqr.qr_image(qr, format="base64_uri")}


def check(md5: str, created_ts: float, mock_paid: bool) -> str:
    """Blocking call. PAID / UNPAID (NBC) or PAID / SCANNED / UNPAID / EXPIRED (relay)."""
    if settings.mock_mode:
        auto = settings.mock_paid_after > 0 and time.time() - created_ts >= settings.mock_paid_after
        return "PAID" if (mock_paid or auto) else "UNPAID"
    res = _get_client().check_payment(md5)
    return res if isinstance(res, str) else res[0]


def details(md5: str):
    if settings.mock_mode:
        return {"mock": True}
    try:
        return _get_client().get_payment(md5)
    except Exception:
        return None
