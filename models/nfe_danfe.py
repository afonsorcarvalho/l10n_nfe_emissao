# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Montagem do DANFE (Documento Auxiliar da NF-e).

Centraliza a preparação dos dados e estrutura para geração do PDF
seguindo o layout padrão nacional (Manual DANFE - MOC PR).
"""

import base64
import logging
from datetime import datetime

from odoo import models

_logger = logging.getLogger(__name__)

# Modais de frete conforme NT 2016/002
MOD_FRETE_LABELS = {
    "0": "0 - Remetente (CIF)",
    "1": "1 - Destinatário (FOB)",
    "2": "2 - Terceiros",
    "3": "3 - Próprio por conta do Remetente",
    "4": "4 - Próprio por conta do Destinatário",
    "9": "9 - Sem frete",
}


class NFeDANFE(models.AbstractModel):
    """
    Helper para montagem do DANFE a partir do documento fiscal NF-e.

    Fornece métodos para formatação de dados conforme layout nacional.
    """

    _name = "l10n_nfe_emissao.danfe"
    _description = "Helper DANFE NF-e"

    def _format_chave_blocos(self, chave):
        """
        Formata a chave de acesso em blocos de 4 dígitos (padrão DANFE).

        :param chave: str 44 dígitos
        :return: str "XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX"
        """
        if not chave or len(chave) != 44:
            return chave or ""
        digits = "".join(filter(str.isdigit, str(chave)))[:44]
        return " ".join(digits[i : i + 4] for i in range(0, 44, 4))

    def _format_datetime_danfe(self, dt):
        """Formata data/hora para exibição no DANFE (DD/MM/AAAA HH:MM)."""
        if not dt:
            return ""
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return dt[:19] if len(dt) >= 19 else dt
        return dt.strftime("%d/%m/%Y %H:%M") if hasattr(dt, "strftime") else str(dt)

    def _format_date_danfe(self, dt):
        """Formata data para exibição no DANFE (DD/MM/AAAA)."""
        if not dt:
            return ""
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt[:10])
            except (ValueError, TypeError):
                return dt[:10] if len(dt) >= 10 else dt
        return dt.strftime("%d/%m/%Y") if hasattr(dt, "strftime") else str(dt)

    def _format_money(self, value, decimals=2):
        """Formata valor monetário para exibição."""
        if value is None:
            return "0,00"
        try:
            v = float(value)
            return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return str(value)

    def _get_mod_frete_label(self, mod_frete):
        """Retorna descrição da modalidade de frete."""
        key = str(mod_frete or "9").strip()
        return MOD_FRETE_LABELS.get(key, key)

    def get_danfe_data(self, document):
        """
        Prepara dicionário com dados para o template QWeb do DANFE.

        :param document: l10n_br_fiscal.document (NF-e autorizada)
        :return: dict com chave, emitente, destinatário, itens, totais, etc.
        """
        document.ensure_one()
        company = document.company_id
        partner = document.partner_id

        # Chave formatada em blocos
        chave = document.nfe_key or ""
        chave_formatada = self._format_chave_blocos(chave)

        # Tipo operação
        tp_nf = getattr(document, "fiscal_operation_type", None) or "saida"
        tipo_operacao = "SAÍDA" if str(tp_nf).lower() in ("saida", "1", "output") else "ENTRADA"

        # Série e número
        serie = (document.document_serie_id.code or "1").strip()
        numero = str(document.document_number or "")

        # Ambiente - marca "SEM VALOR FISCAL" em homologação ou quando não autorizada
        tp_amb = getattr(company, "nfe_environment", None) or getattr(company, "dfe_environment", None) or "2"
        is_homologacao = str(tp_amb) == "2"
        state = getattr(document, "state_edoc", None)
        is_autorizada = state == "autorizada"
        is_cancelada = state == "cancelada"
        # Sem valor fiscal: homologação OU não autorizada (em digitação, rejeitada, etc.)
        sem_valor_fiscal = is_homologacao or not is_autorizada

        # Itens para tabela
        itens = []
        for idx, line in enumerate(document.fiscal_line_ids, start=1):
            ncm = (line.ncm_id.code or "00000000")[:8] if line.ncm_id else "00000000"
            cfop = line.cfop_id.code if line.cfop_id else ""
            uom = line.uom_id.name[:6] if line.uom_id else "UN"
            cst = (line.icms_cst_id.code or "") if line.icms_cst_id else ""
            itens.append({
                "n": idx,
                "codigo": line.product_id.default_code or str(line.product_id.id),
                "descricao": (line.name or line.product_id.name or "")[:60],
                "ncm": ncm,
                "cst": cst,
                "cfop": cfop,
                "uom": uom,
                "qtd": self._format_money(line.quantity),
                "v_unit": self._format_money(line.price_unit),
                "v_total": self._format_money(line.price_gross),
                "v_bc_icms": self._format_money(getattr(line, "icms_base", 0)),
                "v_icms": self._format_money(getattr(line, "icms_value", 0)),
                "p_icms": self._format_money(getattr(line, "icms_percent", 0)),
            })

        # Totalizadores
        v_prod = float(getattr(document, "amount_price_gross", 0) or 0)
        v_frete = float(getattr(document, "amount_freight_value", 0) or 0)
        v_seg = float(getattr(document, "amount_insurance_value", 0) or 0)
        v_desc = float(getattr(document, "amount_discount_value", 0) or 0)
        v_nf = float(getattr(document, "fiscal_amount_total", 0) or 0)
        v_bc_icms = float(getattr(document, "amount_icms_base", 0) or 0)
        v_icms = float(getattr(document, "amount_icms_value", 0) or 0)

        return {
            "doc": document,
            "company": company,
            "partner": partner,
            "company_inscr_est": getattr(company, "inscr_est", None) or "",
            "partner_inscr_est": getattr(partner, "inscr_est", None) or "",
            "chave": chave,
            "chave_formatada": chave_formatada,
            "nfe_protocol": document.nfe_protocol or "",
            "tipo_operacao": tipo_operacao,
            "natureza_op": document.fiscal_operation_id.name or "Venda",
            "serie": serie,
            "numero": numero,
            "data_emissao": self._format_datetime_danfe(document.document_date),
            "data_saida": self._format_datetime_danfe(
                getattr(document, "document_date", document.document_date)
            ),
            "is_homologacao": is_homologacao,
            "sem_valor_fiscal": sem_valor_fiscal,
            "is_cancelada": is_cancelada,
            "itens": itens,
            "v_prod": self._format_money(v_prod),
            "v_frete": self._format_money(v_frete),
            "v_seg": self._format_money(v_seg),
            "v_desc": self._format_money(v_desc),
            "v_nf": self._format_money(v_nf),
            "v_bc_icms": self._format_money(v_bc_icms),
            "v_icms": self._format_money(v_icms),
            "mod_frete": self._format_money(
                getattr(document, "freight_modality", 9) or 9
            ),
            "mod_frete_label": self._get_mod_frete_label(
                getattr(document, "freight_modality", 9)
            ),
        }
