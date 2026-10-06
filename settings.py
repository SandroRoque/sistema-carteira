import os
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent


def load_dotenv(dotenv_path: Path = _PROJECT_ROOT / ".env") -> None:
    """Populate os.environ from a .env file without overriding real env vars."""
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def database_url() -> str:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError(
            "A variável de ambiente DATABASE_URL não está definida "
            "(ex.: postgresql+psycopg://carteira:carteira@localhost:5432/carteira)."
        )
    return url


def cpf_hmac_key() -> bytes:
    """Secret used to pseudonymize CPFs (see contas.pseudonimizar_cpf).

    Losing or rotating it breaks the link between new uploads and existing
    investidores, so it must be kept like any other production secret.
    """
    load_dotenv()
    key = os.environ.get("CPF_HMAC_KEY", "")
    if len(key) < 32:
        raise ValueError(
            "CPF_HMAC_KEY ausente ou curta (mínimo 32 caracteres). "
            'Gere uma com: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return key.encode()


def cookie_secure() -> bool:
    """Session cookie only over HTTPS. Set COOKIE_SECURE=false for local http."""
    load_dotenv()
    return os.environ.get("COOKIE_SECURE", "true").lower() not in ("0", "false", "no")


def cadastro_aberto() -> bool:
    """Anyone may create an account (default true). CADASTRO_ABERTO=false closes
    sign-up; accounts are then created with `admin.py criar-usuario`."""
    load_dotenv()
    return os.environ.get("CADASTRO_ABERTO", "true").lower() not in ("0", "false", "no")


@dataclass(frozen=True)
class Settings:
    notas_dir: Path
    prototype_pdf_names: tuple[str, ...]


def load_settings() -> Settings:
    load_dotenv()

    notas_dir_value = os.environ.get("NOTAS_DIR")
    if not notas_dir_value:
        raise ValueError("A variável de ambiente NOTAS_DIR não está definida.")

    prototype_raw = os.environ.get(
        "PROTOTYPE_PDF_NAMES",
        "",
    )
    prototype_pdf_names = tuple(
        name.strip() for name in prototype_raw.split(",") if name.strip()
    )

    return Settings(
        notas_dir=Path(notas_dir_value),
        prototype_pdf_names=prototype_pdf_names,
    )
