# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Estende res.config.settings com o campo Emissor NF-e (por empresa).

Expõe company_id.nfe_emitter_active na tela Configurações Gerais -> Fiscal,
no bloco Documentos fiscais, no lugar da opção NF-e/NFC-e do l10n_br_fiscal.
"""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nfe_emitter_active = fields.Boolean(
        string="Emissor NF-e",
        related="company_id.nfe_emitter_active",
        readonly=False,
        help="Ativa o uso do módulo de emissão de NF-e (criação e envio de NF-e a partir de faturas).",
    )
