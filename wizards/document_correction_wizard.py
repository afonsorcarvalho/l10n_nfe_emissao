# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Wizard de Carta de Correção Eletrônica (CCe).

Herdado para NF-e: chama _document_correction_nfe no documento (contorna MRO, como no cancelamento)
e retorna notificação + reload do formulário para exibir evento em Events e chatter.
"""

from odoo import _
from odoo import models

from odoo.addons.l10n_br_fiscal.constants.fiscal import SITUACAO_EDOC_AUTORIZADA

from odoo.addons.l10n_nfe_emissao.models.nfe_sefaz_chatter import action_reload_form


class DocumentCorrectionWizard(models.TransientModel):
    _inherit = "l10n_br_fiscal.document.correction.wizard"

    def doit(self):
        """Executa correção; NF-e 55 usa _document_correction_nfe para SEFAZ/eventos/chatter (como cancelamento)."""
        for wizard in self:
            if not wizard.document_id:
                continue
            doc = wizard.document_id
            is_nfe_55 = (
                doc.document_type_id
                and doc.document_type_id.code == "55"
                and getattr(doc, "nfe_key", None)
                and doc.state_edoc == SITUACAO_EDOC_AUTORIZADA
            )
            if is_nfe_55 and hasattr(doc, "_document_correction_nfe"):
                doc._document_correction_nfe(wizard.justification)
            else:
                wizard.document_id._document_correction(wizard.justification)
        doc = self.document_id
        if doc and doc.document_type_id and doc.document_type_id.code == "55":
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Carta de Correção"),
                    "message": _("CCe registrada na SEFAZ. O formulário será atualizado."),
                    "type": "success",
                    "sticky": False,
                    "next": action_reload_form(doc),
                },
            }
        return self._close()
