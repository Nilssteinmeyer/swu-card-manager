"""E-mail sending abstraction.

For local/LAN operation no SMTP is configured yet: tokens are written to
the log (and shown in the dev UI) so password reset and email verification
can be tested end-to-end. When we go online, set SMTP_HOST/SMTP_PORT/
SMTP_USER/SMTP_PASS/SMTP_FROM in config — send_email() then uses real SMTP.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger("swu_manager.mail")

_SMTP_CONFIG: dict[str, str] = {}


def configure(host: str, port: int, user: str, password: str, sender: str, use_tls: bool = True) -> None:
    _SMTP_CONFIG.update({
        "host": host, "port": port, "user": user,
        "password": password, "sender": sender, "use_tls": use_tls,
    })


def is_configured() -> bool:
    return bool(_SMTP_CONFIG.get("host"))


def send_email(to: str, subject: str, body_text: str) -> bool:
    """Send via SMTP if configured; otherwise log the mail (local mode)."""
    if not is_configured():
        log.info(
            f"[LOCAL-MAIL] To: {to} | Subject: {subject}\n{body_text}",
            extra={"event": "local_mail"},
        )
        return False  # signal: not actually sent
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = _SMTP_CONFIG["sender"]
        msg["To"] = to
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        with smtplib.SMTP(_SMTP_CONFIG["host"], int(_SMTP_CONFIG["port"]), timeout=20) as server:
            if _SMTP_CONFIG.get("use_tls", True):
                server.starttls()
            server.login(_SMTP_CONFIG["user"], _SMTP_CONFIG["password"])
            server.send_message(msg)
        return True
    except Exception as e:
        log.error(f"SMTP send failed: {e}", extra={"event": "smtp_error"})
        return False
