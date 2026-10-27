"""schema inicial

Revision ID: 0001
Revises: 
Create Date: 2026-10-05 20:42:07.640656

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ativos',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('tipo', sa.Text(), server_default='desconhecido', nullable=False),
    sa.Column('subtipo', sa.Text(), nullable=True),
    sa.Column('ticker', sa.Text(), nullable=True),
    sa.Column('nome', sa.Text(), nullable=True),
    sa.Column('cnpj_emissor', sa.Text(), nullable=True),
    sa.Column('emissor', sa.Text(), nullable=True),
    sa.Column('indexador', sa.Text(), nullable=True),
    sa.Column('taxa_prefixada', sa.Numeric(), nullable=True),
    sa.Column('percentual_do_indexador', sa.Numeric(), nullable=True),
    sa.Column('emissao', sa.Date(), nullable=True),
    sa.Column('vencimento', sa.Date(), nullable=True),
    sa.Column('revisado', sa.Boolean(), server_default='false', nullable=False),
    sa.CheckConstraint("tipo IN ('acao', 'fii', 'bdr', 'tesouro_direto', 'renda_fixa', 'recibo_subscricao', 'direito_subscricao', 'desconhecido')", name=op.f('ck_ativos_tipo_valido')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ativos')),
    sa.UniqueConstraint('ticker', name=op.f('uq_ativos_ticker'))
    )
    op.create_table('usuarios',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('nome', sa.Text(), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_usuarios'))
    )
    op.create_index('uq_usuarios_email_lower', 'usuarios', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('investidores',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('usuario_id', sa.BigInteger(), nullable=False),
    sa.Column('cpf', sa.Text(), nullable=False),
    sa.Column('nome', sa.Text(), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("cpf ~ '^[0-9]{11}$'", name=op.f('ck_investidores_cpf_digitos')),
    sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], name=op.f('fk_investidores_usuario_id_usuarios'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_investidores')),
    sa.UniqueConstraint('usuario_id', 'cpf', name='uq_investidores_usuario_cpf')
    )
    op.create_table('ticker_aliases',
    sa.Column('raw_text', sa.Text(), nullable=False),
    sa.Column('ativo_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['ativo_id'], ['ativos.id'], name=op.f('fk_ticker_aliases_ativo_id_ativos')),
    sa.PrimaryKeyConstraint('raw_text', name=op.f('pk_ticker_aliases'))
    )
    op.create_index(op.f('ix_ticker_aliases_ativo_id'), 'ticker_aliases', ['ativo_id'], unique=False)
    op.create_table('b3_arquivos_processados',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('investidor_id', sa.BigInteger(), nullable=False),
    sa.Column('arquivo', sa.Text(), nullable=False),
    sa.Column('processado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['investidor_id'], ['investidores.id'], name=op.f('fk_b3_arquivos_processados_investidor_id_investidores'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_b3_arquivos_processados')),
    sa.UniqueConstraint('investidor_id', 'arquivo', name='uq_b3_arquivos_investidor_arquivo')
    )
    op.create_table('notas',
    sa.Column('investidor_id', sa.BigInteger(), nullable=False),
    sa.Column('corretora_id', sa.Text(), nullable=False),
    sa.Column('doc_type', sa.Text(), nullable=False),
    sa.Column('nota_id', sa.Text(), nullable=False),
    sa.Column('data_pregao', sa.Date(), nullable=False),
    sa.Column('data_de_liquidacao', sa.Date(), nullable=True),
    sa.Column('cpf_cliente', sa.Text(), nullable=False),
    sa.Column('codigo_cliente', sa.Text(), nullable=False),
    sa.Column('nome_cliente', sa.Text(), nullable=True),
    sa.Column('assessor', sa.Text(), nullable=True),
    sa.Column('folha', sa.Text(), nullable=True),
    sa.Column('endereco', sa.Text(), nullable=True),
    sa.Column('cidade', sa.Text(), nullable=True),
    sa.Column('uf', sa.Text(), nullable=True),
    sa.Column('cep', sa.Text(), nullable=True),
    sa.Column('nota_de', sa.Text(), nullable=True),
    sa.Column('local', sa.Text(), nullable=True),
    sa.Column('emissor', sa.Text(), nullable=True),
    sa.Column('cnpj_emissor', sa.Text(), nullable=True),
    sa.Column('comando', sa.Text(), nullable=True),
    sa.Column('mercado', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('liquido_para', sa.Numeric(), nullable=True),
    sa.Column('debentures', sa.Numeric(), nullable=True),
    sa.Column('vendas_a_vista', sa.Numeric(), nullable=True),
    sa.Column('compras_a_vista', sa.Numeric(), nullable=True),
    sa.Column('opcoes_compras', sa.Numeric(), nullable=True),
    sa.Column('opcoes_vendas', sa.Numeric(), nullable=True),
    sa.Column('operacoes_a_termo', sa.Numeric(), nullable=True),
    sa.Column('valor_das_operacoes_com_titulos_publicos', sa.Numeric(), nullable=True),
    sa.Column('valor_das_operacoes', sa.Numeric(), nullable=True),
    sa.Column('valor_liquido_das_operacoes', sa.Numeric(), nullable=True),
    sa.Column('taxa_de_liquidacao', sa.Numeric(), nullable=True),
    sa.Column('taxa_de_registro', sa.Numeric(), nullable=True),
    sa.Column('total_clearing_cblc', sa.Numeric(), nullable=True),
    sa.Column('taxa_de_termo_opcoes', sa.Numeric(), nullable=True),
    sa.Column('taxa_a_n_a', sa.Numeric(), nullable=True),
    sa.Column('emolumentos', sa.Numeric(), nullable=True),
    sa.Column('total_bolsa', sa.Numeric(), nullable=True),
    sa.Column('corretagem', sa.Numeric(), nullable=True),
    sa.Column('iss', sa.Numeric(), nullable=True),
    sa.Column('irrf_sobre_operacoes', sa.Numeric(), nullable=True),
    sa.Column('outras', sa.Numeric(), nullable=True),
    sa.Column('total_corretagem_despesas', sa.Numeric(), nullable=True),
    sa.Column('taxa_operacional', sa.Numeric(), nullable=True),
    sa.Column('execucao', sa.Numeric(), nullable=True),
    sa.Column('taxa_de_custodia', sa.Numeric(), nullable=True),
    sa.Column('impostos', sa.Numeric(), nullable=True),
    sa.Column('pis_cofins', sa.Numeric(), nullable=True),
    sa.Column('taxa_de_transferencia_de_ativos', sa.Numeric(), nullable=True),
    sa.Column('execucao_casa', sa.Numeric(), nullable=True),
    sa.Column('filename', sa.Text(), nullable=False),
    sa.Column('processado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['investidor_id'], ['investidores.id'], name=op.f('fk_notas_investidor_id_investidores'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('investidor_id', 'corretora_id', 'doc_type', 'nota_id', name=op.f('pk_notas'))
    )
    op.create_table('b3_movimentacoes',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('investidor_id', sa.BigInteger(), nullable=False),
    sa.Column('arquivo_id', sa.BigInteger(), nullable=False),
    sa.Column('sentido', sa.Text(), nullable=False),
    sa.Column('data', sa.Date(), nullable=False),
    sa.Column('movimentacao', sa.Text(), nullable=False),
    sa.Column('produto_raw', sa.Text(), nullable=False),
    sa.Column('ativo_id', sa.BigInteger(), nullable=True),
    sa.Column('instituicao', sa.Text(), nullable=True),
    sa.Column('quantidade', sa.Numeric(), nullable=True),
    sa.Column('preco_unitario', sa.Numeric(), nullable=True),
    sa.Column('valor', sa.Numeric(), nullable=True),
    sa.ForeignKeyConstraint(['arquivo_id'], ['b3_arquivos_processados.id'], name=op.f('fk_b3_movimentacoes_arquivo_id_b3_arquivos_processados'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ativo_id'], ['ativos.id'], name=op.f('fk_b3_movimentacoes_ativo_id_ativos')),
    sa.ForeignKeyConstraint(['investidor_id'], ['investidores.id'], name=op.f('fk_b3_movimentacoes_investidor_id_investidores'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_b3_movimentacoes'))
    )
    op.create_index('ix_b3_movimentacoes_investidor_ativo', 'b3_movimentacoes', ['investidor_id', 'ativo_id', 'movimentacao'], unique=False)
    op.create_index('ix_b3_movimentacoes_investidor_data', 'b3_movimentacoes', ['investidor_id', 'data'], unique=False)
    op.create_table('negociacoes',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('investidor_id', sa.BigInteger(), nullable=False),
    sa.Column('corretora_id', sa.Text(), nullable=False),
    sa.Column('doc_type', sa.Text(), nullable=False),
    sa.Column('nota_id', sa.Text(), nullable=False),
    sa.Column('linha_na_nota', sa.Integer(), server_default='0', nullable=False),
    sa.Column('ativo_id', sa.BigInteger(), nullable=False),
    sa.Column('data', sa.Date(), nullable=False),
    sa.Column('sentido', sa.Text(), nullable=False),
    sa.Column('tipo', sa.Text(), nullable=False),
    sa.Column('debito_credito', sa.Text(), nullable=True),
    sa.Column('quantidade', sa.Numeric(), nullable=True),
    sa.Column('preco_unitario', sa.Numeric(), nullable=True),
    sa.Column('valor_bruto', sa.Numeric(), nullable=True),
    sa.Column('taxas_proporcionais', sa.Numeric(), server_default='0', nullable=False),
    sa.Column('valor_liquido', sa.Numeric(), nullable=True),
    sa.Column('mercado', sa.Text(), nullable=True),
    sa.Column('tipo_de_mercado', sa.Text(), nullable=True),
    sa.Column('prazo', sa.Text(), nullable=True),
    sa.Column('observacao', sa.Text(), nullable=True),
    sa.Column('indexador', sa.Text(), nullable=True),
    sa.Column('taxa_cupom_percentual', sa.Numeric(), nullable=True),
    sa.Column('percentual_do_indexador', sa.Numeric(), nullable=True),
    sa.Column('emissao', sa.Date(), nullable=True),
    sa.Column('vencimento', sa.Date(), nullable=True),
    sa.Column('custodia', sa.Text(), nullable=True),
    sa.Column('tipo_emitente', sa.Text(), nullable=True),
    sa.Column('conta_bancaria', sa.Text(), nullable=True),
    sa.Column('rendimentos', sa.Text(), nullable=True),
    sa.Column('imposto_de_renda_federal', sa.Numeric(), nullable=True),
    sa.Column('iof', sa.Numeric(), nullable=True),
    sa.Column('especificacao_observacao', sa.Text(), nullable=True),
    sa.Column('tx_bvmf', sa.Numeric(), nullable=True),
    sa.Column('tx_agente_custodia', sa.Numeric(), nullable=True),
    sa.CheckConstraint("sentido IN ('entrada', 'saida')", name=op.f('ck_negociacoes_sentido_valido')),
    sa.CheckConstraint('quantidade IS NULL OR quantidade >= 0', name=op.f('ck_negociacoes_quantidade_nao_negativa')),
    sa.ForeignKeyConstraint(['ativo_id'], ['ativos.id'], name=op.f('fk_negociacoes_ativo_id_ativos')),
    sa.ForeignKeyConstraint(['investidor_id', 'corretora_id', 'doc_type', 'nota_id'], ['notas.investidor_id', 'notas.corretora_id', 'notas.doc_type', 'notas.nota_id'], name='fk_negociacoes_nota', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_negociacoes')),
    sa.UniqueConstraint('investidor_id', 'corretora_id', 'doc_type', 'nota_id', 'linha_na_nota', name='uq_negociacoes_linha')
    )
    op.create_index('ix_negociacoes_investidor_ativo_data', 'negociacoes', ['investidor_id', 'ativo_id', 'data'], unique=False)
    op.create_table('bonificacoes',
    sa.Column('b3_movimentacao_id', sa.BigInteger(), nullable=False),
    sa.Column('custo_por_cota', sa.Numeric(), nullable=True),
    sa.CheckConstraint('custo_por_cota IS NULL OR custo_por_cota >= 0', name=op.f('ck_bonificacoes_custo_nao_negativo')),
    sa.ForeignKeyConstraint(['b3_movimentacao_id'], ['b3_movimentacoes.id'], name=op.f('fk_bonificacoes_b3_movimentacao_id_b3_movimentacoes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('b3_movimentacao_id', name=op.f('pk_bonificacoes'))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('bonificacoes')
    op.drop_index('ix_negociacoes_investidor_ativo_data', table_name='negociacoes')
    op.drop_table('negociacoes')
    op.drop_index('ix_b3_movimentacoes_investidor_data', table_name='b3_movimentacoes')
    op.drop_index('ix_b3_movimentacoes_investidor_ativo', table_name='b3_movimentacoes')
    op.drop_table('b3_movimentacoes')
    op.drop_table('notas')
    op.drop_table('b3_arquivos_processados')
    op.drop_index(op.f('ix_ticker_aliases_ativo_id'), table_name='ticker_aliases')
    op.drop_table('ticker_aliases')
    op.drop_table('investidores')
    op.drop_index('uq_usuarios_email_lower', table_name='usuarios')
    op.drop_table('usuarios')
    op.drop_table('ativos')
