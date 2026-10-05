"""Builders for transformer records with sensible defaults."""

import dataclasses
from datetime import date

from transformer import DocumentoTransformado, NegociacaoRecord, NotaRecord


def _vazios(cls) -> dict:
    return {f.name: None for f in dataclasses.fields(cls)}


def nota(cpf: str, nota_id: str = "1001", data: date = date(2025, 3, 10), **kw) -> NotaRecord:
    valores = _vazios(NotaRecord) | {
        "nota_id": nota_id,
        "corretora_id": "nu_invest",
        "doc_type": "NotaCorretagem",
        "data_pregao": data,
        "cpf_cliente": cpf,
        "codigo_cliente": "123",
        "nome_cliente": "Cliente Teste",
    }
    return NotaRecord(**(valores | kw))


def negociacao(
    raw_ticker: str = "PETR4F PN N2",
    sentido: str = "entrada",
    quantidade: float = 100,
    preco: float = 10.0,
    data: date = date(2025, 3, 10),
    nota_id: str = "1001",
    linha: int = 1,
    **kw,
) -> NegociacaoRecord:
    valor = quantidade * preco
    valores = _vazios(NegociacaoRecord) | {
        "nota_id": nota_id,
        "corretora_id": "nu_invest",
        "doc_type": "NotaCorretagem",
        "linha_na_nota": linha,
        "raw_ticker": raw_ticker,
        "data": data,
        "sentido": sentido,
        "tipo": "compra" if sentido == "entrada" else "venda",
        "quantidade": quantidade,
        "preco_unitario": preco,
        "valor_bruto": valor,
        "taxas_proporcionais": 0.0,
        "valor_liquido": valor,
    }
    return NegociacaoRecord(**(valores | kw))


def documento(cpf: str, *negociacoes: NegociacaoRecord, nota_id: str = "1001", data: date = date(2025, 3, 10)):
    negs = list(negociacoes) or [negociacao(nota_id=nota_id, data=data)]
    return DocumentoTransformado(nota=nota(cpf, nota_id=nota_id, data=data), negociacoes=negs)
