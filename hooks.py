# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Hooks para migração do módulo l10n_nfe_emissao.

pre_init_hook: garante que as colunas de NF-e em res_company existem
antes do carregamento dos modelos, evitando UndefinedColumn durante upgrade.
- nfe_environment
- nfe_serie_homologacao_id, nfe_serie_producao_id (Many2one = INTEGER)
"""

import logging

_logger = logging.getLogger(__name__)


def _column_exists(cr, table, column):
    """Retorna True se a coluna existir na tabela."""
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return cr.fetchone() is not None


def pre_init_hook(env):
    """
    Adiciona colunas de NF-e em res_company se não existirem.

    Necessário para evitar psycopg2.errors.UndefinedColumn durante upgrade,
    quando o ORM pode consultar res.company antes da migração automática.
    """
    cr = env.cr
    if not _column_exists(cr, "res_company", "nfe_environment"):
        _logger.info("Adicionando coluna res_company.nfe_environment")
        cr.execute(
            """
            ALTER TABLE res_company
            ADD COLUMN nfe_environment VARCHAR
            """
        )
        cr.execute(
            """
            UPDATE res_company
            SET nfe_environment = '2'
            WHERE nfe_environment IS NULL
            """
        )
    if not _column_exists(cr, "res_company", "nfe_serie_homologacao_id"):
        _logger.info("Adicionando coluna res_company.nfe_serie_homologacao_id")
        cr.execute(
            """
            ALTER TABLE res_company
            ADD COLUMN nfe_serie_homologacao_id INTEGER
            """
        )
    if not _column_exists(cr, "res_company", "nfe_serie_producao_id"):
        _logger.info("Adicionando coluna res_company.nfe_serie_producao_id")
        cr.execute(
            """
            ALTER TABLE res_company
            ADD COLUMN nfe_serie_producao_id INTEGER
            """
        )
