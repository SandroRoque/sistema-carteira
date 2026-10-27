"""Outgoing e-mail: the sign-up confirmation and the new-password link.

Sent through the SMTP server in settings.smtp(), or printed to the terminal
with EMAIL_NO_TERMINAL=true during local development. Without either, or
without URL_PUBLICA for the links, e-mail is unavailable: the pages that need
it say so, and the public sign-up stays closed.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

import settings

_log = logging.getLogger(__name__)
_TIMEOUT = 15  # seconds


def disponivel() -> bool:
    return settings.url_publica() is not None and (
        settings.email_no_terminal() or settings.smtp() is not None
    )


def link(caminho: str) -> str:
    """Absolute link to a page of the app, for an e-mail."""
    base = settings.url_publica()
    if base is None:
        raise RuntimeError("URL_PUBLICA não está definida.")
    return base + caminho


def enviar(para: str, assunto: str, texto: str) -> None:
    """Send a plain-text message. Runs after the response (FastAPI background
    task), so a failure is only logged: by exception type, since the message
    carries a token."""
    try:
        if settings.email_no_terminal():
            print(f"--- e-mail para {para}: {assunto}\n{texto}\n---", flush=True)
            return
        config = settings.smtp()
        if config is None:
            _log.warning("e-mail não enviado: SMTP_HOST não está definido")
            return
        msg = EmailMessage()
        msg["From"] = config.remetente
        msg["To"] = para
        msg["Subject"] = assunto
        msg["Message-ID"] = make_msgid()
        msg.set_content(texto)
        contexto = ssl.create_default_context()
        if config.porta == 465:
            servidor = smtplib.SMTP_SSL(config.host, config.porta, timeout=_TIMEOUT, context=contexto)
        else:
            servidor = smtplib.SMTP(config.host, config.porta, timeout=_TIMEOUT)
        with servidor:
            if config.porta != 465:
                servidor.starttls(context=contexto)
            if config.usuario:
                servidor.login(config.usuario, config.senha or "")
            servidor.send_message(msg)
    except Exception as exc:  # noqa: BLE001 - logged, the request already answered
        _log.error("falha ao enviar e-mail: %s", type(exc).__name__)
