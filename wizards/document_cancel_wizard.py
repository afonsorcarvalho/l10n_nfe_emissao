# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Wizard de cancelamento de documento fiscal.

Herdado para NF-e: chama _document_cancel_nfe no documento (contorna MRO que
chamava só o workflow) e retorna notificação + reload do formulário.
"""

import logging

from odoo import _
from odoo import models

from odoo.addons.l10n_br_fiscal.constants.fiscal import SITUACAO_EDOC_AUTORIZADA

from odoo.addons.l10n_nfe_emissao.models.nfe_sefaz_chatter import action_reload_form

_logger = logging.getLogger(__name__)


class DocumentCancelWizard(models.TransientModel):
    _inherit = "l10n_br_fiscal.document.cancel.wizard"

    def doit(self):
        """Executa cancelamento; NF-e 55 usa _document_cancel_nfe para SEFAZ/eventos/chatter."""
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
            if is_nfe_55 and hasattr(doc, "_document_cancel_nfe"):
                _logger.info(
                    "[NFe cancel] wizard chamando _document_cancel_nfe document_id=%s",
                    doc.id,
                )
                doc._document_cancel_nfe(wizard.justification)
            else:
                wizard.do_cancel()
        doc = self.document_id
        if doc and doc.document_type_id and doc.document_type_id.code == "55":
            # Notificação e depois reload: atualiza o formulário com eventos/CANCELLATION/chatter
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Cancelamento NF-e"),
                    "message": _("Cancelamento registrado na SEFAZ. O formulário será atualizado."),
                    "type": "success",
                    "sticky": False,
                    "next": action_reload_form(doc),
                },
            }
        return self._close()
