from dataclasses import dataclass, field


@dataclass(frozen=True)
class Corretora:
    id: str
    nome: str
    cnpj: str
    aliases: list[str] = field(default_factory=list)
    header_lines: list[str] = field(default_factory=list)
    site: str | None = None
