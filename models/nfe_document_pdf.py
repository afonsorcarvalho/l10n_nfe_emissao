# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Mixin de geração e merge de DANFE/PDF (make_pdf, DACCe, banner cancelada).

Responsabilidade única: produção do PDF do DANFE e anexos (DACCe, aviso cancelada).
Usa nfe_xml_utils e nfe_pdf_helpers.
"""

import base64
import logging
import re

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.l10n_br_fiscal.constants.fiscal import (
    SITUACAO_EDOC_AUTORIZADA,
    SITUACAO_EDOC_CANCELADA,
)

from . import nfe_pdf_helpers
from . import nfe_xml_utils

_logger = logging.getLogger(__name__)


class NFeDocumentPdf(models.AbstractModel):
    """
    Geração de DANFE (make_pdf), merge DACCe e banner NF-e cancelada.

    Mixin abstrato sem _inherit: não pode herdar de modelo não abstrato (l10n_br_fiscal.document).
    Os métodos são injetados em FiscalDocument via _inherit no modelo principal.
    """

    _name = "nfe.document.pdf"
    _description = "NF-e document PDF (DANFE, DACCe, cancelada)"
    _abstract = True

    def _merge_dacce_into_danfe_pdf(self, danfe_pdf_bytes):
        """
        Anexa páginas DACCe (Carta de Correção) ao PDF do DANFE.
        Busca anexos XML de CCe; usa BrazilFiscalReport ou fallback FPDF.
        """
        self.ensure_one()
        if not danfe_pdf_bytes:
            return danfe_pdf_bytes
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("name", "ilike", "sefaz_carta"),
        ], order="id asc")
        if not attachments:
            return danfe_pdf_bytes
        try:
            from io import BytesIO
            try:
                from pypdf import PdfReader, PdfWriter
            except ImportError:
                from PyPDF2 import PdfReader, PdfWriter
            try:
                from brazilfiscalreport.nfe.dacce import DaCCe
            except ImportError:
                from brazilfiscalreport.dacce import DaCCe
            writer = PdfWriter()
            pdf_io = BytesIO(
                danfe_pdf_bytes if isinstance(danfe_pdf_bytes, bytes) else danfe_pdf_bytes
            )
            reader = PdfReader(pdf_io)
            for page in reader.pages:
                writer.add_page(page)
            for att in attachments:
                xml_raw = base64.b64decode(att.datas).decode("utf-8", errors="replace")
                proc_xml = nfe_xml_utils.wrap_inf_evento_as_proc_evento(xml_raw)
                if not proc_xml:
                    continue
                dacce_pdf = None
                try:
                    dacce = DaCCe(xml=proc_xml)
                    dacce_pdf = dacce.output()
                except Exception as e:
                    _logger.info(
                        "[DANFE] DaCCe falhou (%s), usando fallback: %s",
                        att.name, e,
                    )
                    x_correcao = nfe_xml_utils.extract_xcorrecao_from_infevento(xml_raw)
                    n_prot = nfe_xml_utils.extract_nprot_from_infevento(xml_raw)
                    dacce_pdf = nfe_pdf_helpers.build_cce_fallback_pdf(self, x_correcao, n_prot=n_prot)
                if dacce_pdf:
                    if isinstance(dacce_pdf, str):
                        dacce_pdf = dacce_pdf.encode("latin-1")
                    try:
                        dacce_reader = PdfReader(BytesIO(dacce_pdf))
                        for page in dacce_reader.pages:
                            writer.add_page(page)
                        _logger.info("[DANFE] DACCe anexado ao PDF (anexo=%s)", att.name)
                    except Exception as e:
                        _logger.warning("[DANFE] Erro ao merge pagina DACCe: %s", e)
            output = BytesIO()
            writer.write(output)
            return output.getvalue()
        except ImportError as e:
            _logger.warning("[DANFE] pypdf não disponível para merge DACCe: %s", e)
            return danfe_pdf_bytes
        except Exception as e:
            _logger.warning("[DANFE] Erro ao merge DACCe: %s", e, exc_info=True)
            return danfe_pdf_bytes

    def _prepend_cancelada_banner_to_pdf(self, danfe_pdf_bytes):
        """Insere como primeira página o aviso NF-e CANCELADA no PDF do DANFE."""
        self.ensure_one()
        if not danfe_pdf_bytes:
            return danfe_pdf_bytes
        banner_bytes = nfe_pdf_helpers.build_cancelada_banner_pdf(self)
        if not banner_bytes:
            return danfe_pdf_bytes
        try:
            from io import BytesIO
            try:
                from pypdf import PdfReader, PdfWriter
            except ImportError:
                from PyPDF2 import PdfReader, PdfWriter
            writer = PdfWriter()
            writer.add_page(PdfReader(BytesIO(banner_bytes)).pages[0])
            reader = PdfReader(BytesIO(
                danfe_pdf_bytes if isinstance(danfe_pdf_bytes, bytes) else danfe_pdf_bytes
            ))
            for page in reader.pages:
                writer.add_page(page)
            output = BytesIO()
            writer.write(output)
            return output.getvalue()
        except Exception as e:
            _logger.warning(
                "[DANFE] Erro ao inserir pagina NF-e CANCELADA: %s",
                e, exc_info=True,
            )
            return danfe_pdf_bytes

    def make_pdf(self):
        """
        Gera o DANFE (PDF) para NF-e sempre que houver XML de envio (nfe_proc_xml,
        nfe_xml_signed ou nfe_xml). Não autorizada: sem protocolo e marca "SEM VALOR FISCAL";
        cancelada: marca "CANCELADA". Homologação já exibe "SEM VALOR FISCAL".
        """
        # DANFE disponível sempre que houver XML de envio OU linhas (preview); qualquer estado.
        # code pode vir como int (55) ou str ("55") conforme o modelo fiscal
        def _is_nfe_55(d):
            if not d.document_type_id:
                return False
            code = getattr(d.document_type_id, "code", None)
            if code is None:
                return False
            if str(code).strip() != "55":
                return False
            return bool(
                getattr(d, "nfe_proc_xml", None)
                or getattr(d, "nfe_xml_signed", None)
                or getattr(d, "nfe_xml", None)
                or (d.fiscal_line_ids and len(d.fiscal_line_ids) > 0)
            )

        nfe_docs = self.filtered(_is_nfe_55)
        if not nfe_docs:
            _logger.warning(
                "[DANFE] make_pdf: nenhum doc passou no filtro NF-e 55 (ids=%s). "
                "Verificando: code=%s",
                self.ids,
                [str(getattr(d.document_type_id, "code", None)) for d in self],
            )
            return super().make_pdf()

        _logger.warning("[DANFE] make_pdf: processando %s doc(s) nfe_docs.ids=%s", len(nfe_docs), nfe_docs.ids)

        for doc in nfe_docs:
            pdf_content = None
            pdf_source = None
            try:
                _logger.warning(
                    "[DANFE] make_pdf loop doc=%s state_edoc=%s nfe_proc_xml=%s nfe_xml_signed=%s nfe_xml=%s",
                    doc.id,
                    getattr(doc, "state_edoc", None),
                    "sim" if getattr(doc, "nfe_proc_xml", None) else "nao",
                    "sim" if getattr(doc, "nfe_xml_signed", None) else "nao",
                    "sim" if getattr(doc, "nfe_xml", None) else "nao",
                )
            except Exception as e:
                _logger.warning("[DANFE] make_pdf log inicial falhou: %s", e)

            if doc.nfe_proc_xml:
                try:
                    _logger.info("[DANFE] entrando no fluxo BrazilFiscalReport")
                    try:
                        from brazilfiscalreport.nfe.danfe import Danfe
                    except ImportError:
                        from brazilfiscalreport.danfe import Danfe

                    xml_proc = base64.b64decode(doc.nfe_proc_xml).decode("utf-8")
                    xml_proc = re.sub(
                        r">\s*<\?xml[^?>]*\?>\s*", ">\n", xml_proc, flags=re.IGNORECASE
                    )
                    xml_proc = nfe_xml_utils.ensure_protocol_in_procnfe(
                        xml_proc, doc.nfe_protocol
                    )

                    config = None
                    try:
                        from brazilfiscalreport.danfe import (
                            DanfeConfig,
                            DecimalConfig,
                            FontSize,
                            FontType,
                            InvoiceDisplay,
                        )
                        # Helvetica: mesma fonte do banner NF-e cancelada (manual BrazilFiscalReport: font_type em DanfeConfig)
                        # FontType do library só tem TIMES e COURIER; FPDF2 aceita "Helvetica" via .value
                        font_type_helvetica = getattr(FontType, "HELVETICA", None)
                        if font_type_helvetica is None:
                            font_type_helvetica = type("_Helvetica", (), {"value": "Helvetica"})()

                        config = DanfeConfig(
                            decimal_config=DecimalConfig(
                                price_precision=2,
                                quantity_precision=2,
                            ),
                            invoice_display=InvoiceDisplay.FULL_DETAILS,
                            display_pis_cofins=True,
                            font_size=FontSize.SMALL,
                            font_type=font_type_helvetica,
                        )
                        if doc.company_id.logo:
                            from io import BytesIO
                            config.logo = BytesIO(base64.b64decode(doc.company_id.logo))
                    except ImportError:
                        pass

                    danfe = Danfe(xml=xml_proc, config=config) if config else Danfe(xml=xml_proc)
                    pdf_content = danfe.output()
                    if isinstance(pdf_content, str):
                        pdf_content = pdf_content.encode("latin-1")
                    pdf_source = "BrazilFiscalReport"
                except ImportError as e:
                    _logger.warning(
                        "brazilfiscalreport não disponível, usando QWeb: %s", e
                    )
                except Exception as e:
                    _logger.warning(
                        "Erro ao gerar DANFE com BrazilFiscalReport: %s. Fallback QWeb.",
                        e, exc_info=True,
                    )

            if not pdf_content:
                _logger.info(
                    "[DANFE] pdf_content vazio (sem nfe_proc_xml ou BrazilFiscalReport falhou), "
                    "usando fallback QWeb doc=%s state=%s",
                    doc.id, getattr(doc, "state_edoc", None),
                )
                report = self.env.ref(
                    "l10n_nfe_emissao.report_nfe_danfe",
                    raise_if_not_found=False,
                )
                if report:
                    pdf_content, _ = report._render_qweb_pdf(
                        report.report_name, res_ids=doc.ids
                    )
                if not pdf_content:
                    _logger.warning("[DANFE] QWeb também não gerou PDF para doc=%s", doc.id)
                    raise UserError(
                        _("Não foi possível gerar o DANFE. Verifique os logs.")
                    )
                pdf_source = "QWeb"

            if pdf_content:
                pdf_content = doc._merge_dacce_into_danfe_pdf(pdf_content)
            if pdf_content and doc.state_edoc == SITUACAO_EDOC_CANCELADA:
                pdf_content = doc._prepend_cancelada_banner_to_pdf(pdf_content)

            _logger.info(
                "[DANFE] salvando PDF doc=%s fonte=%s bytes=%s",
                doc.id, pdf_source or "?", len(pdf_content) if pdf_content else 0,
            )
            filename = f"DANFE-NFe-{doc.document_number or doc.id}.pdf"
            vals = {
                "name": filename,
                "res_model": doc._name,
                "res_id": doc.id,
                "datas": base64.b64encode(pdf_content),
                "mimetype": "application/pdf",
                "type": "binary",
            }
            try:
                old_attachment = doc.file_report_id
                new_attachment = self.env["ir.attachment"].create(vals)
                doc.file_report_id = new_attachment
                if old_attachment and old_attachment.exists():
                    old_attachment.unlink()
                _logger.info(
                    "[DANFE] make_pdf: file_report_id atualizado doc_id=%s attachment_id=%s",
                    doc.id,
                    doc.file_report_id.id,
                )
            except Exception as e:
                _logger.warning(
                    "[DANFE] make_pdf: falha ao criar/gravar file_report_id doc_id=%s: %s",
                    doc.id,
                    e,
                    exc_info=True,
                )
                raise
        # Chamada via MRO (view_pdf) pode deixar super() sem make_pdf; EDI só faz pass
        try:
            return super().make_pdf()
        except AttributeError:
            return None

    def view_pdf(self):
        """
        Abre o DANFE em nova aba (botão "Visualizar PDF" do header).
        Para NF-e 55: regenera PDF via make_pdf() e abre file_report_id
        (mesmo anexo exibido como "Document Report" na aba EDI).
        Evita delegar ao EDI (que levantaria "No PDF file generated!") quando
        o documento é NF-e mas o PDF ainda não pode ser gerado.
        """
        self.ensure_one()
        code = str(getattr(self.document_type_id, "code", None) or "").strip() if self.document_type_id else ""
        if self.document_type_id and code == "55":
            self.make_pdf()
            if self.file_report_id:
                return self._target_new_tab(self.file_report_id)
            if not self.nfe_key:
                raise UserError(
                    _("O DANFE só fica disponível após a NF-e ser enviada e autorizada. "
                      "Use o botão 'Emitir NF-e' e aguarde a autorização da SEFAZ.")
                )
            if self.state_edoc not in (SITUACAO_EDOC_AUTORIZADA, SITUACAO_EDOC_CANCELADA):
                raise UserError(
                    _("O DANFE só está disponível para NF-e autorizada ou cancelada. "
                      "Estado atual: %s.")
                    % (self.state_edoc or _("em digitação"))
                )
            raise UserError(
                _("Não foi possível gerar o DANFE. Verifique se a NF-e possui "
                  "XML autorizado (procNFe) e os logs do servidor.")
            )
        return super().view_pdf()

    def action_download_danfe(self):
        """Retorna ação para baixar o DANFE (PDF) com download forçado."""
        self.ensure_one()
        if not self.file_report_id:
            raise UserError(_("Não há DANFE (PDF) disponível para download."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.file_report_id.id}?download=1",
            "target": "self",
        }
