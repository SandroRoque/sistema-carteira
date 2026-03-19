import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv_file(dotenv_path: Path) -> None:
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


@dataclass(frozen=True)
class Settings:
    notas_dir: Path
    prototype_pdf_names: tuple[str, ...]


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parent
    _load_dotenv_file(project_root / ".env")

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
