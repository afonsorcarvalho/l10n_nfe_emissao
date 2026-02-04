# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Extensão do DFe para processar NF-e recebidas na distribuição.

Implementa _process_distribution para armazenar procNFe como anexos
e criar registros l10n_br_fiscal.document a partir dos XMLs recebidos.
"""

import base64
import logging
import re

from odoo import _, models

from odoo.addons.l10n_br_fiscal_dfe.tools import utils as dfe_utils

_logger = logging.getLogger(__name__)


class DFeNFe(models.Model):
    """Estende l10n_br_fiscal.dfe para processar NF-e da distribuição."""

    _inherit = "l10n_br_fiscal.dfe"

    def _process_distribution(self, result):
        """
        Processa documentos retornados na consulta de distribuição DFe.

        Para cada docZip com schema procNFe:
        - Descomprime o XML
        - Armazena como ir.attachment
        - Cria l10n_br_fiscal.document e vincula ao DFe

        Resumos (resNFe) são ignorados neste módulo.
        """
        resposta = result.resposta
        lote = getattr(resposta, "loteDistDFeInt", None)
        if not lote:
            return

        doc_zip_list = getattr(lote, "docZip", None)
        if not doc_zip_list:
            return
        if not isinstance(doc_zip_list, list):
            doc_zip_list = [doc_zip_list]

        for dfe in self:
            company = dfe.company_id
            for doc in doc_zip_list:
                try:
                    dfe._process_doc_zip(doc, company)
                except Exception as e:
                    _logger.exception(
                        "Erro ao processar docZip NSU=%s: %s",
                        getattr(doc, "NSU", "?"),
                        str(e),
                    )
                    dfe.message_post(
                        body=_(
                            "Erro ao processar documento NSU %(nsu)s: %(error)s",
                            nsu=getattr(doc, "NSU", "?"),
                            error=str(e),
                        )
                    )

    def _process_doc_zip(self, doc, company):
        """
        Processa um docZip da distribuição: procNFe ou resNFe.

        :param doc: elemento docZip (com schema, NSU, valueOf_)
        :param company: res.company da consulta
        """
        schema = (getattr(doc, "schema", None) or "").lower()
        nsu = getattr(doc, "NSU", "0")

        # Conteúdo base64+gzip
        content = getattr(doc, "valueOf_", None) or getattr(doc, "value", None)
        if not content:
            return

        # Apenas procNFe (NFe completa com protocolo) - resNFe é resumo
        if "procnfe" not in schema:
            _logger.debug("Ignorando schema %s (NSU=%s)", schema, nsu)
            return

        xml_bytes = self._decompress_doc_zip(content)
        if not xml_bytes:
            return

        # Chave NF-e (44 dígitos) - extrair do XML ou infNFe Id
        chave = self._extract_nfe_key_from_xml(xml_bytes)
        if not chave:
            chave = f"NSU-{nsu}"

        # Evitar duplicatas
        existing = self.env["l10n_br_fiscal.document"].search(
            [("document_key", "=", chave), ("company_id", "=", company.id)],
            limit=1,
        )
        if existing:
            _logger.info("NF-e %s já existe (NSU=%s)", chave, nsu)
            if not existing.dfe_id:
                existing.dfe_id = self.id
            return

        # Criar documento fiscal (entrada - empresa é destinatária)
        doc_type = self.env["l10n_br_fiscal.document.type"].search(
            [("code", "=", "55")], limit=1
        )
        if not doc_type:
            _logger.warning("Tipo de documento 55 (NF-e) não encontrado")
            return

        doc_vals = self._build_document_vals_from_xml(
            xml_bytes, chave, company, doc_type, nsu
        )
        if not doc_vals:
            return

        document = self.env["l10n_br_fiscal.document"].create(doc_vals)

        # Armazenar XML como anexo vinculado ao documento
        # Nome do arquivo: NFe-[numero]-autorizado.xml (procNFe é o XML autorizado)
        numero = document.document_number or document.id
        xml_b64 = base64.b64encode(xml_bytes)
        self.env["ir.attachment"].create(
            {
                "name": f"NFe-{numero}-autorizado.xml",
                "datas": xml_b64,
                "res_model": "l10n_br_fiscal.document",
                "res_id": document.id,
                "mimetype": "application/xml",
                "description": _("NF-e recebida via distribuição DFe (NSU %s)") % nsu,
            }
        )

        document.message_post(
            body=_("NF-e importada via Consultar Notas Recebidas (NSU %s)") % nsu,
        )

    def _decompress_doc_zip(self, content):
        """Descomprime conteúdo base64+gzip do docZip."""
        try:
            gz_file = dfe_utils.parse_gzip_xml(content)
            return gz_file.read()
        except Exception as e:
            _logger.warning("Falha ao descomprimir docZip: %s", e)
            return None

    def _extract_nfe_key_from_xml(self, xml_bytes):
        """Extrai chave NF-e (44 dígitos) do XML procNFe."""
        try:
            xml_str = xml_bytes.decode("utf-8", errors="ignore")
            # infNFe Id="NFe35210875335849000115550010000016871192213331"
            match = re.search(r'Id="NFe(\d{44})"', xml_str)
            if match:
                return match.group(1)
            match = re.search(r"<chNFe>(\d{44})</chNFe>", xml_str)
            if match:
                return match.group(1)
        except Exception as e:
            _logger.debug("Erro ao extrair chave do XML: %s", e)
        return None

    def _build_document_vals_from_xml(self, xml_bytes, chave, company, doc_type, nsu):
        """
        Monta valores para criar l10n_br_fiscal.document a partir do XML procNFe.

        Extração mínima: chave, empresa, parceiro emissor (se possível).
        """
        try:
            xml_str = xml_bytes.decode("utf-8", errors="ignore")
            # CNPJ emitente - dest é a empresa na nota recebida
            cnpj_emit = None
            emit_match = re.search(r"<CNPJ>(\d{14})</CNPJ>", xml_str)
            if emit_match:
                cnpj_emit = emit_match.group(1)
            if not cnpj_emit:
                cpf_match = re.search(r"<CPF>(\d{11})</CPF>", xml_str)
                if cpf_match:
                    cnpj_emit = cpf_match.group(1)

            # Buscar parceiro pelo CNPJ/CPF
            partner_id = False
            if cnpj_emit:
                cnpj_clean = re.sub(r"\D", "", cnpj_emit)
                partner = self.env["res.partner"].search(
                    [("cnpj_cpf_stripped", "=", cnpj_clean)],
                    limit=1,
                )
                if partner:
                    partner_id = partner.id

            numero = self._extract_numero_from_key(chave)

            return {
                "company_id": company.id,
                "dfe_id": self.id,
                "document_type_id": doc_type.id,
                "document_key": chave,
                "document_number": numero,
                "fiscal_operation_type": "in",  # Entrada
                "issuer": "partner",  # Parceiro emite, empresa recebe
                "partner_id": partner_id or company.partner_id.id,
                "state_edoc": "autorizada",  # Já autorizada pela SEFAZ
            }
        except Exception as e:
            _logger.warning("Erro ao montar vals do documento: %s", e)
            return None

    def _extract_numero_from_key(self, chave):
        """Número: posições 25-33 (9 dígitos)."""
        if len(chave) >= 34:
            return chave[25:34].lstrip("0") or "1"
        return chave[-9:].lstrip("0") or "1"

