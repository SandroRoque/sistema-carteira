from database import DB_PATH, connect, init_db
from extrai_nota_de_negociacao import PdfImagemError, extrair
from loader import carregar
from settings import load_settings
from transformer import transformar


def main() -> None:
    settings = load_settings()
    init_db()

    parsed = skipped_image = skipped_duplicate = failed = 0
    negociacoes_total = 0

    with connect() as conn:
        for pdf_path in sorted(settings.notas_dir.glob("*.pdf")):
            try:
                resultado = extrair(pdf_path)
            except PdfImagemError:
                skipped_image += 1
                continue
            except Exception as exc:
                print(f"FAIL {pdf_path.name}: {type(exc).__name__}: {exc}")
                failed += 1
                continue

            doc = transformar(resultado)
            carregado = carregar(conn, doc, pdf_path.name)
            if carregado:
                parsed += 1
                negociacoes_total += len(doc.negociacoes)
            else:
                skipped_duplicate += 1

    print(f"\nDB: {DB_PATH}")
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
        print("  sqlite3 carteira.db 'SELECT * FROM ativos WHERE revisado = 0 ORDER BY tipo, nome'")


if __name__ == "__main__":
    main()
