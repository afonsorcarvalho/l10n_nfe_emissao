# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Integração fatura (account.move) com emissão de NF-e.

- Campo fiscal_document_id: documento fiscal referente à fatura (NF-e gerada a partir dela).
- Botão "Emitir NF-e": cria o documento fiscal a partir da fatura (se não existir),
  confirma e envia à SEFAZ.

Requer que a empresa tenha "Emissor NF-e" ativo e que produtos/parceiro tenham
dados fiscais quando necessário (NCM, etc.).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.l10n_br_fiscal.constants.fiscal import (
    DOCUMENT_ISSUER_COMPANY,
    EDOC_PURPOSE_NORMAL,
    SITUACAO_EDOC_AUTORIZADA,
    SITUACAO_EDOC_A_ENVIAR,
    SITUACAO_EDOC_EM_DIGITACAO,
)

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    fiscal_document_id = fields.Many2one(
        comodel_name="l10n_br_fiscal.document",
        string="Documento fiscal referente",
        copy=False,
        index=True,
        help="NF-e ou documento fiscal gerado a partir desta fatura (botão Emitir NF-e).",
    )

    def _get_nfe_document_type(self):
        """Retorna o tipo de documento NF-e (código 55)."""
        return self.env["l10n_br_fiscal.document.type"].search(
            [("code", "=", "55")], limit=1
        )

    def _get_nfe_fiscal_operation(self, move_type):
        """Retorna uma operação fiscal de saída aprovada (venda ou devolução)."""
        domain = [
            ("state", "=", "approved"),
            ("fiscal_operation_type", "=", "out"),
        ]
        return self.env["l10n_br_fiscal.operation"].search(domain, limit=1)

    def _get_nfe_document_serie(self, company, document_type, fiscal_operation):
        """Retorna a série NF-e conforme ambiente da empresa (homologação/produção)."""
        env = getattr(company, "nfe_environment", None) or "2"
        if env == "1" and company.nfe_serie_producao_id:
            return company.nfe_serie_producao_id
        if company.nfe_serie_homologacao_id:
            return company.nfe_serie_homologacao_id
        return document_type.get_document_serie(company, fiscal_operation)

    def _create_fiscal_document_from_invoice(self):
        """
        Cria um documento fiscal (NF-e) a partir dos dados da fatura.
        Preenche cabeçalho e linhas; atribui a self.fiscal_document_id e document.move_id.
        """
        self.ensure_one()
        if self.fiscal_document_id:
            return self.fiscal_document_id

        if self.move_type not in ("out_invoice", "out_refund"):
            raise UserError(
                _("Emitir NF-e está disponível apenas para faturas de saída (venda ou devolução).")
            )

        company = self.company_id
        if not getattr(company, "nfe_emitter_active", True):
            raise UserError(
                _("Emissor NF-e não está ativo para a empresa '%s'. Ative em Configurações > Fiscal.")
                % company.name
            )

        document_type = self._get_nfe_document_type()
        if not document_type:
            raise UserError(
                _("Tipo de documento NF-e (55) não encontrado. Verifique o módulo l10n_br_fiscal.")
            )

        fiscal_operation = self._get_nfe_fiscal_operation(self.move_type)
        if not fiscal_operation:
            raise UserError(
                _("Nenhuma operação fiscal de saída aprovada encontrada. Configure em Fiscal > Operações.")
            )

        document_serie = self._get_nfe_document_serie(
            company, document_type, fiscal_operation
        )
        if not document_serie:
            raise UserError(
                _("Nenhuma série NF-e configurada para a empresa. Configure em Empresa > Aba NF-e.")
            )

        # Linhas de produto/serviço (exclui seção, anotação e totais)
        line_vals_list = []
        for line in self.invoice_line_ids:
            if line.display_type in ("line_section", "line_note"):
                continue
            if not line.product_id:
                continue
            line_vals_list.append(
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "quantity": line.quantity,
                        "price_unit": line.price_unit,
                        "fiscal_operation_id": fiscal_operation.id,
                        "fiscal_operation_line_id": fiscal_operation.line_ids[:1].id
                        if fiscal_operation.line_ids
                        else False,
                    },
                )
            )

        if not line_vals_list:
            raise UserError(
                _("A fatura não possui linhas com produto. Adicione itens à fatura para emitir a NF-e.")
            )

        from datetime import datetime, time as dt_time

        inv_date = self.invoice_date or fields.Date.context_today(self)
        document_date = datetime.combine(inv_date, dt_time(12, 0, 0))

        doc_vals = {
            "company_id": company.id,
            "partner_id": self.partner_id.id,
            "document_type_id": document_type.id,
            "fiscal_operation_id": fiscal_operation.id,
            "fiscal_operation_type": "out",
            "document_serie_id": document_serie.id,
            "document_date": document_date,
            "issuer": DOCUMENT_ISSUER_COMPANY,
            "edoc_purpose": EDOC_PURPOSE_NORMAL,
            "move_id": self.id,
            "fiscal_line_ids": line_vals_list,
        }

        doc = self.env["l10n_br_fiscal.document"].create(doc_vals)
        self.fiscal_document_id = doc.id
        _logger.info(
            "Documento fiscal %s criado a partir da fatura %s (move_id=%s).",
            doc.display_name,
            self.name,
            self.id,
        )
        return doc

    def action_emit_nfe_from_invoice(self):
        """
        Abre o fluxo de emissão NF-e para esta fatura.
        Se já existir documento fiscal referente, usa-o; senão cria a partir da fatura.
        Em seguida confirma (action_confirmar_nfe) e emite (action_emit_nfe).
        """
        self.ensure_one()
        if self.move_type not in ("out_invoice", "out_refund"):
            raise UserError(
                _("Emitir NF-e está disponível apenas para faturas de saída.")
            )

        doc = self.fiscal_document_id
        if not doc:
            doc = self._create_fiscal_document_from_invoice()

        if doc.state_edoc == SITUACAO_EDOC_AUTORIZADA:
            return {
                "type": "ir.actions.act_window",
                "res_model": "l10n_br_fiscal.document",
                "res_id": doc.id,
                "view_mode": "form",
                "target": "current",
            }

        if doc.state_edoc in (SITUACAO_EDOC_EM_DIGITACAO, SITUACAO_EDOC_A_ENVIAR):
            if doc.state_edoc == SITUACAO_EDOC_EM_DIGITACAO and not doc.nfe_key:
                doc.action_confirmar_nfe()
            doc.action_emit_nfe()

        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n_br_fiscal.document",
            "res_id": doc.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_fiscal_nfe(self):
        """
        Abre o documento fiscal NF-e referente a esta fatura.
        Visível apenas quando fiscal_document_id está preenchido (NF-e já criada/emitida).
        """
        self.ensure_one()
        if not self.fiscal_document_id:
            raise UserError(
                _("Não há documento fiscal NF-e vinculado a esta fatura.")
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n_br_fiscal.document",
            "res_id": self.fiscal_document_id.id,
            "view_mode": "form",
            "target": "current",
        }
