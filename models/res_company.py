# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Estende res.company com configurações de NF-e.

- nfe_environment: ambiente SEFAZ (Produção ou Homologação). tpAmb 1 = Produção, 2 = Homologação.
- nfe_serie_homologacao_id / nfe_serie_producao_id: séries a usar conforme o ambiente,
  permitindo série distinta para homologação e produção (leiaute NF-e: 0-999).
"""

from odoo import fields, models

from ..constants.nfe import NFE_ENVIRONMENT_DEFAULT, NFE_ENVIRONMENTS


class ResCompany(models.Model):
    _inherit = "res.company"

    nfe_environment = fields.Selection(
        selection=NFE_ENVIRONMENTS,
        string="Ambiente NF-e",
        default=NFE_ENVIRONMENT_DEFAULT,
        help="Identificação do Ambiente SEFAZ: 1 - Produção, 2 - Homologação",
    )

    # Série NF-e por ambiente (opcional). Se definida, documentos NF-e usarão esta série
    # conforme nfe_environment; caso contrário, segue a série padrão do tipo/operação.
    nfe_serie_homologacao_id = fields.Many2one(
        comodel_name="l10n_br_fiscal.document.serie",
        string="Série NF-e (Homologação)",
        help="Série a utilizar para NF-e quando o ambiente for Homologação. Deixe vazio para usar a série padrão do documento.",
        domain="[('document_type_id.code', '=', '55')]",
    )
    nfe_serie_producao_id = fields.Many2one(
        comodel_name="l10n_br_fiscal.document.serie",
        string="Série NF-e (Produção)",
        help="Série a utilizar para NF-e quando o ambiente for Produção. Deixe vazio para usar a série padrão do documento.",
        domain="[('document_type_id.code', '=', '55')]",
    )

    # Ativa o uso do módulo de emissão de NF-e (checkbox em Configurações Gerais -> Fiscal).
    nfe_emitter_active = fields.Boolean(
        string="Emissor NF-e",
        default=True,
        help="Quando ativo, permite emitir NF-e a partir de faturas e usar as funcionalidades do módulo de emissão.",
    )
