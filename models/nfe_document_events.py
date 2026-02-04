# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Mixin de eventos SEFAZ: cancelamento e Carta de Correção (CCe).

Responsabilidade única: envio de evento de cancelamento (110111) e CCe (110110).
Usa nfe_sefaz_chatter; _get_nfe_processor vem do mixin de emissão no modelo final.
Preenche a aba EDI com eventos l10n_br_fiscal.event (cancel_event_id, correction).
"""

import logging

from odoo import _
from odoo import models
from odoo.exceptions import UserError

from odoo.addons.l10n_br_fiscal.constants.fiscal import (
    EVENT_ENV_HML,
    EVENT_ENV_PROD,
    SITUACAO_EDOC_AUTORIZADA,
)

from . import nfe_sefaz_chatter

_logger = logging.getLogger(__name__)


def _parse_ret_evento_xml(xml_str):
    """
    Extrai nProt e dhRegEvento do XML retEvento/infEvento (resposta SEFAZ de evento).
    Retorna dict com protocol_number e protocol_date (datetime UTC naive ou None).
    """
    if not xml_str:
        return {"protocol_number": None, "protocol_date": None}
    try:
        from lxml import etree
        from dateutil.parser import parse as dateutil_parse
        from datetime import timezone
        root = etree.fromstring(
            xml_str.encode("utf-8") if isinstance(xml_str, str) else xml_str
        )
        ns = "http://www.portalfiscal.inf.br/nfe"
        inf = root.find(f".//{{{ns}}}infEvento")
        if inf is None:
            inf = root.find(".//infEvento")
        if inf is None:
            return {"protocol_number": None, "protocol_date": None}
        n_prot = None
        dh = None
        n_elem = inf.find(f"{{{ns}}}nProt") or inf.find("nProt")
        dh_elem = inf.find(f"{{{ns}}}dhRegEvento") or inf.find("dhRegEvento")
        if n_elem is not None and n_elem.text:
            n_prot = n_elem.text.strip()
        if dh_elem is not None and dh_elem.text:
            try:
                dt = dateutil_parse(dh_elem.text.strip())
                if dt.tzinfo:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                dh = dt
            except Exception:
                pass
        return {"protocol_number": n_prot, "protocol_date": dh}
    except Exception:
        return {"protocol_number": None, "protocol_date": None}


class NFeDocumentEvents(models.AbstractModel):
    """
    Eventos NF-e: cancelamento (_document_cancel) e Carta de Correção (_document_correction).

    Mixin abstrato sem _inherit: não pode herdar de modelo não abstrato (l10n_br_fiscal.document).
    Os métodos são injetados em FiscalDocument via _inherit no modelo principal.
    """

    _name = "nfe.document.events"
    _description = "NF-e document events (cancel, CCe)"
    _abstract = True

    def _document_cancel_nfe(self, justificative):
        """
        Cancelamento NF-e: envia evento à SEFAZ, registra em event_ids/chatter/CANCELLATION
        e em seguida aplica o workflow (cancel_reason, state_edoc).
        Chamado explicitamente pelo wizard para não depender do MRO de _document_cancel.
        """
        self.ensure_one()
        doc = self
        _logger.info(
            "[NFe cancel] _document_cancel_nfe chamado doc id=%s type_code=%s nfe_key=%s state_edoc=%s",
            doc.id,
            doc.document_type_id.code if doc.document_type_id else None,
            getattr(doc, "nfe_key", None),
            doc.state_edoc,
        )
        if not (
            doc.document_type_id
            and doc.document_type_id.code == "55"
            and doc.nfe_key
            and doc.state_edoc == SITUACAO_EDOC_AUTORIZADA
        ):
            _logger.info(
                "[NFe cancel] documento fora do bloco NF-e 55 autorizada; apenas workflow"
            )
            return super()._document_cancel(justificative)
        if not doc.nfe_protocol:
            raise UserError(
                _("NF-e sem protocolo de autorização. Não é possível cancelar.")
            )
        if not justificative or len(str(justificative).strip()) < 15:
            raise UserError(
                _("A justificativa de cancelamento deve ter no mínimo 15 caracteres.")
            )
        processor = doc._get_nfe_processor()
        _logger.info(
            "[NFe cancel] enviando cancelamento à SEFAZ chave=%s protocolo=%s",
            doc.nfe_key,
            doc.nfe_protocol,
        )
        result = processor.cancela_documento(
            chave=doc.nfe_key,
            protocolo_autorizacao=doc.nfe_protocol,
            justificativa=str(justificative).strip()[:255],
        )
        _logger.info(
            "[NFe cancel] resposta SEFAZ recebida result=%s type=%s",
            result is not None,
            type(result).__name__ if result is not None else None,
        )
        _logger.info("NF-e %s cancelada na SEFAZ.", doc.nfe_key)

        resp_xml = None
        cstat, xmotivo = None, None
        if result is not None:
            resp = getattr(result, "resposta", result)
            if resp is not None:
                cstat = getattr(resp, "cStat", None) or getattr(
                    getattr(resp, "infEvento", None), "cStat", None
                )
                xmotivo = getattr(resp, "xMotivo", None) or getattr(
                    getattr(resp, "infEvento", None), "xMotivo", None
                )
                _logger.info("[NFe cancel] cStat=%s xMotivo=%s", cstat, (str(xmotivo)[:80] + "..." if xmotivo and len(str(xmotivo)) > 80 else xmotivo))
                if hasattr(resp, "to_xml"):
                    try:
                        resp_xml = resp.to_xml()
                        if isinstance(resp_xml, bytes):
                            resp_xml = resp_xml.decode("utf-8")
                    except Exception as e:
                        _logger.debug("[NFe cancel] to_xml falhou: %s", e)
                elif hasattr(resp, "tag"):
                    from lxml import etree as et
                    try:
                        resp_xml = et.tostring(resp, encoding="unicode", method="xml")
                    except Exception as e:
                        _logger.debug("[NFe cancel] etree tostring falhou: %s", e)
        try:
            nfe_sefaz_chatter.post_sefaz_event(
                doc,
                event_type="Cancelamento NF-e",
                cstat=str(cstat) if cstat else "135",
                xmotivo=str(xmotivo) if xmotivo else _("Evento registrado e vinculado à NF-e"),
                xml_content=resp_xml,
            )
        except Exception as e:
            _logger.warning(
                "Falha ao postar cancelamento no chatter do documento %s: %s",
                doc.display_name,
                e,
                exc_info=True,
            )
        env_edi = EVENT_ENV_PROD if (getattr(doc.company_id, "nfe_environment", None) or "2") == "1" else EVENT_ENV_HML
        event_model = doc.env["l10n_br_fiscal.event"]
        xml_para_evento = resp_xml or "<?xml version='1.0'?><retEvento/>"
        _logger.info("[NFe cancel] criando l10n_br_fiscal.event type=2 document_id=%s", doc.id)
        cancel_event = event_model.create_event_save_xml(
            company_id=doc.company_id,
            environment=env_edi,
            event_type="2",
            xml_file=xml_para_evento,
            document_id=doc,
            justification=str(justificative).strip()[:255],
        )
        _logger.info("[NFe cancel] evento criado id=%s", cancel_event.id)
        parsed = _parse_ret_evento_xml(resp_xml) if resp_xml else {}
        cancel_event.set_done(
            status_code=str(cstat) if cstat else "",
            response=str(xmotivo) if xmotivo else "",
            protocol_date=parsed.get("protocol_date"),
            protocol_number=parsed.get("protocol_number") or "",
            file_response_xml=resp_xml,
        )
        xmotivo_str = (str(xmotivo) if xmotivo else "").strip()
        cancel_event.write({
            "message": xmotivo_str[:500] if xmotivo_str else "Response received",
            "origin": doc.display_name or doc.name or "",
            "sequence": "1",
            "partner_id": doc.partner_id.id if doc.partner_id else False,
        })
        if hasattr(doc, "cancel_event_id"):
            doc.cancel_event_id = cancel_event
            _logger.info("[NFe cancel] doc.cancel_event_id=%s CANCELLATION preenchido", cancel_event.id)
        # Aplica workflow (cancel_reason, state_edoc); super() não encontra _document_cancel no MRO do mixin
        from odoo.addons.l10n_br_fiscal_edi.models.document_workflow import DocumentWorkflow
        return DocumentWorkflow._document_cancel(self, justificative)

    def _document_cancel(self, justificative):
        """
        Envia evento de cancelamento à SEFAZ para NF-e (evento 110111).
        Usa NFeAdapter.cancela_documento.
        Nota: o wizard NF-e chama _document_cancel_nfe diretamente para garantir execução (MRO).
        """
        return self._document_cancel_nfe(justificative)

    def _document_correction(self, justificative):
        """
        Envia Carta de Correção Eletrônica (CCe) à SEFAZ (evento 110110).
        Máximo 20 CCe por NF-e. Regenera DANFE após envio.
        """
        for doc in self:
            if (
                doc.document_type_id
                and doc.document_type_id.code == "55"
                and doc.nfe_key
                and doc.state_edoc == SITUACAO_EDOC_AUTORIZADA
            ):
                if not justificative or len(str(justificative).strip()) < 15:
                    raise UserError(
                        _("A correção deve ter no mínimo 15 caracteres.")
                    )
                txt = str(justificative).strip()[:1000]
                sequencia = 1
                if hasattr(doc, "correction_event_ids") and doc.correction_event_ids:
                    sequencia = len(doc.correction_event_ids) + 1
                if sequencia > 20:
                    raise UserError(
                        _("Limite de 20 Cartas de Correção por NF-e já atingido.")
                    )
                processor = doc._get_nfe_processor()
                result = processor.carta_correcao(
                    chave=doc.nfe_key,
                    sequencia=str(sequencia),
                    justificativa=txt,
                )
                _logger.info("CCe nº %s enviada para NF-e %s.", sequencia, doc.nfe_key)

                resp_xml = None
                cstat, xmotivo = None, None
                if result is not None:
                    resp = getattr(result, "resposta", result)
                    if resp is not None:
                        cstat = getattr(resp, "cStat", None) or getattr(
                            getattr(resp, "infEvento", None), "cStat", None
                        )
                        xmotivo = getattr(resp, "xMotivo", None) or getattr(
                            getattr(resp, "infEvento", None), "xMotivo", None
                        )
                        if hasattr(resp, "to_xml"):
                            try:
                                resp_xml = resp.to_xml()
                                if isinstance(resp_xml, bytes):
                                    resp_xml = resp_xml.decode("utf-8")
                            except Exception:
                                pass
                        elif hasattr(resp, "tag"):
                            from lxml import etree as et
                            try:
                                resp_xml = et.tostring(resp, encoding="unicode", method="xml")
                            except Exception:
                                pass
                        elif hasattr(resp, "export") and callable(getattr(resp, "export")):
                            from io import StringIO
                            try:
                                buf = StringIO()
                                resp.export(buf, 0)
                                resp_xml = buf.getvalue()
                            except Exception:
                                pass
                nfe_sefaz_chatter.post_sefaz_event(
                    doc,
                    event_type="Carta de Correção",
                    cstat=str(cstat) if cstat else "135",
                    xmotivo=str(xmotivo) if xmotivo else _("Evento registrado"),
                    xml_content=resp_xml,
                )
                # Aba EDI: evento Carta de Correção (l10n_br_fiscal.event type "14"; aparece em correction_event_ids)
                if hasattr(doc, "authorization_event_id"):
                    env_edi = EVENT_ENV_PROD if (getattr(doc.company_id, "nfe_environment", None) or "2") == "1" else EVENT_ENV_HML
                    event_model = doc.env["l10n_br_fiscal.event"]
                    xml_para_evento = resp_xml or "<?xml version='1.0'?><retEvento/>"
                    cce_event = event_model.create_event_save_xml(
                        company_id=doc.company_id,
                        environment=env_edi,
                        event_type="14",
                        xml_file=xml_para_evento,
                        document_id=doc,
                        sequence=str(sequencia),
                        justification=txt[:255],
                    )
                    parsed = _parse_ret_evento_xml(resp_xml) if resp_xml else {}
                    cce_event.set_done(
                        status_code=str(cstat) if cstat else "",
                        response=str(xmotivo) if xmotivo else "",
                        protocol_date=parsed.get("protocol_date"),
                        protocol_number=parsed.get("protocol_number") or "",
                        file_response_xml=resp_xml,
                    )
                    xmotivo_str = (str(xmotivo) if xmotivo else "").strip()
                    cce_event.write({
                        "message": xmotivo_str[:500] if xmotivo_str else "Response received",
                        "origin": doc.display_name or doc.name or "",
                        "partner_id": doc.partner_id.id if doc.partner_id else False,
                    })
                doc.make_pdf()
        return super()._document_correction(justificative)
