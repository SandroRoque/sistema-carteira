import fitz

from extractors import sinacor
from models import NotaCorretagem


def extract(page: fitz.Page) -> NotaCorretagem:
    """Market-standard layout: see extractors/sinacor.py."""
    return sinacor.extrair(page, "brasil_plural")
