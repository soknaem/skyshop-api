"""Sends email with SMTP (optional). Without SMTP settings nothing is sent and the caller logs the link instead."""
import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from .config import settings

log = logging.getLogger("auth.mail")


def configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def _send(to: str, subject: str, text: str, html: str | None) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, 465, context=ssl.create_default_context(), timeout=15) as s:
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
        if settings.smtp_tls:
            s.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


async def send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    if not configured():
        return False
    try:
        await asyncio.to_thread(_send, to, subject, text, html)
        return True
    except Exception as e:
        log.warning("could not send email to %s: %s", to, e)
        return False


def reset_email(name: str, link: str) -> tuple[str, str, str]:
    subject = f"Reset your {settings.shop_name} password"
    text = (f"Hi {name},\n\nWe received a request to reset your password. Open this link within "
            f"{settings.reset_ttl_min} minutes:\n{link}\n\nIf you did not ask for this, ignore this email; your password stays the same.\n")
    html = (f"<p>Hi {name},</p><p>We received a request to reset your password. The link works for "
            f"{settings.reset_ttl_min} minutes:</p><p><a href=\"{link}\">Reset my password</a></p>"
            "<p>If you did not ask for this, ignore this email. Your password stays the same.</p>")
    return subject, text, html
