"""Load brokerage-note PDFs from NOTAS_DIR into the database.

Each nota is filed under the investidor whose CPF is printed on it (created on
first sight) for the usuario given by CARTEIRA_USUARIO_EMAIL, or the only
usuario in the database.

Usage:
    uv run python carrega_notas.py
"""

from contas import usuario_do_cli
from database import connect_sistema
from extrai_nota_de_negociacao import PdfImagemError, extrair_notas
from loader import carregar
from settings import load_settings
from transformer import transformar


def main() -> None:
    settings = load_settings()

    parsed = skipped_image = skipped_duplicate = failed = 0
    negociacoes_total = 0

    with connect_sistema() as conn:
        usuario_id = usuario_do_cli(conn)

        for pdf_path in sorted(settings.notas_dir.glob("*.pdf")):
            try:
                resultados = extrair_notas(pdf_path)
            except PdfImagemError:
                skipped_image += 1
                continue
            except Exception as exc:
                print(f"FAIL {pdf_path.name}: {type(exc).__name__}: {exc}")
                failed += 1
                continue

            # A PDF may bundle several notas (one per trading day).
            for resultado in resultados:
                doc = transformar(resultado)
                if carregar(conn, usuario_id, doc, pdf_path.name):
                    parsed += 1
                    negociacoes_total += len(doc.negociacoes)
                else:
                    skipped_duplicate += 1

    print(
        f"Resumo:"
        f"  carregado={parsed}"
        f"  duplicado={skipped_duplicate}"
        f"  skipped_image={skipped_image}"
        f"  failed={failed}"
        f"  negociacoes={negociacoes_total}"
    )

    if failed == 0:
        print("\nPróximo passo: revise ativos não identificados com:")
        print("  uv run python revisa_ativos.py")


if __name__ == "__main__":
    main()
