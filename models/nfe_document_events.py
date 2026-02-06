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
from . import nfe_xml_utils

_logger = logging.getLogger(__name__)


# Resposta SEFAZ de cancelamento:
#   Sucesso: infEvento com cStat=135, xMotivo, dhRegEvento, nProt.
#   Rejeição: infEvento com cStat != 135 (ex.: 218 = NF-e já cancelada), xMotivo; sem nProt/dhRegEvento.
#   Ex. rejeição: <retEvento><infEvento><cStat>218</cStat><xMotivo>Rejeição: NF-e já está cancelada...</xMotivo></infEvento></retEvento>


def _parse_ret_evento_xml(xml_str):
    """
    Extrai da resposta SEFAZ (retEnvEvento/retEvento/infEvento) os campos:
    protocol_number (nProt), protocol_date (dhRegEvento), cstat (cStat), xmotivo (xMotivo).
    Retorna dict com protocol_number, protocol_date, cstat, xmotivo (None quando ausente).
    Resposta de sucesso: cStat=135; pedido (request) tem detEvento e não tem dhRegEvento — ignorado.
    """
    if not xml_str:
        return {"protocol_number": None, "protocol_date": None, "cstat": None, "xmotivo": None}
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
            for elem in root.iter():
                if elem.tag is not None and (elem.tag == "infEvento" or (isinstance(elem.tag, str) and elem.tag.endswith("}infEvento"))):
                    inf = elem
                    break
        if inf is None:
            return {"protocol_number": None, "protocol_date": None, "cstat": None, "xmotivo": None}
        # Resposta SEFAZ tem dhRegEvento e cStat; pedido tem detEvento (nProt dentro é da autorização)
        dh_elem = inf.find(f"{{{ns}}}dhRegEvento") or inf.find("dhRegEvento")
        if dh_elem is None and hasattr(inf, "iter"):
            for c in inf.iter():
                if c.tag is not None and (c.tag == "dhRegEvento" or (isinstance(c.tag, str) and c.tag.endswith("}dhRegEvento"))):
                    dh_elem = c
                    break
        det_evento = inf.find(f"{{{ns}}}detEvento") or inf.find("detEvento")
        if det_evento is None and hasattr(inf, "iter"):
            for c in inf.iter():
                if c.tag is not None and (c.tag == "detEvento" or (isinstance(c.tag, str) and c.tag.endswith("}detEvento"))):
                    det_evento = c
                    break
        if det_evento is not None and dh_elem is None:
            return {"protocol_number": None, "protocol_date": None, "cstat": None, "xmotivo": None}
        n_prot = None
        dh = None
        cstat = None
        xmotivo = None
        n_elem = inf.find(f"{{{ns}}}nProt") or inf.find("nProt")
        if n_elem is None and hasattr(inf, "iter"):
            for c in inf.iter():
                if c.tag is not None and (c.tag == "nProt" or (isinstance(c.tag, str) and c.tag.endswith("}nProt"))):
                    n_elem = c
                    break
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
        cstat_elem = inf.find(f"{{{ns}}}cStat") or inf.find("cStat")
        if cstat_elem is None and hasattr(inf, "iter"):
            for c in inf.iter():
                if c.tag is not None and (c.tag == "cStat" or (isinstance(c.tag, str) and c.tag.endswith("}cStat"))):
                    cstat_elem = c
                    break
        if cstat_elem is not None and cstat_elem.text:
            cstat = cstat_elem.text.strip()
        xmotivo_elem = inf.find(f"{{{ns}}}xMotivo") or inf.find("xMotivo")
        if xmotivo_elem is None and hasattr(inf, "iter"):
            for c in inf.iter():
                if c.tag is not None and (c.tag == "xMotivo" or (isinstance(c.tag, str) and c.tag.endswith("}xMotivo"))):
                    xmotivo_elem = c
                    break
        if xmotivo_elem is not None and xmotivo_elem.text:
            xmotivo = xmotivo_elem.text.strip()
        return {"protocol_number": n_prot, "protocol_date": dh, "cstat": cstat, "xmotivo": xmotivo}
    except Exception:
        return {"protocol_number": None, "protocol_date": None, "cstat": None, "xmotivo": None}


def _is_request_infevento(obj):
    """
    Detecta se o objeto é o infEvento do PEDIDO (evento enviado), não da RESPOSTA SEFAZ.
    No pedido: tpEvento, detEvento (com nProt da autorização), chNFe; não tem cStat/dhRegEvento.
    Na resposta: cStat, xMotivo, nProt (do evento), dhRegEvento.
    """
    if obj is None:
        return False
    has_cstat = getattr(obj, "cStat", None) is not None or getattr(obj, "nfe40_cStat", None) is not None
    has_dh_reg = getattr(obj, "dhRegEvento", None) is not None or getattr(obj, "nfe40_dhRegEvento", None) is not None
    if has_cstat or has_dh_reg:
        return False
    has_tp_evento = getattr(obj, "tpEvento", None) is not None or getattr(obj, "nfe40_tpEvento", None) is not None
    has_det = getattr(obj, "detEvento", None) is not None or getattr(obj, "nfe40_detEvento", None) is not None
    return bool(has_tp_evento or has_det)


def _get_response_from_processor(processor):
    """
    Tenta obter a resposta SEFAZ (retEvento) do processador após cancela_documento.
    A nfelib pode devolver o infEvento do pedido e guardar a resposta em outro atributo.
    """
    if processor is None:
        return None
    for attr in ("resposta", "retorno", "_resposta", "resposta_evento", "retEvento", "ret_env_evento"):
        val = getattr(processor, attr, None)
        if val is not None:
            return val
    # Transmissão pode guardar último envelope/resposta
    trans = getattr(processor, "_transmissao", None)
    if trans is not None:
        for attr in ("resposta", "ultima_resposta", "last_response", "resposta_xml", "response_body"):
            val = getattr(trans, attr, None)
            if val is not None and (isinstance(val, (str, bytes)) or hasattr(val, "resposta")):
                return val
    return None


def _extract_event_response_xml_and_protocol(result, processor=None):
    """
    Extrai XML de resposta (retEvento) e dados de protocolo do retorno do
    cancela_documento/carta_correcao da nfelib. O adaptador pode devolver
    o infEvento do PEDIDO (request) em vez da RESPOSTA; nesse caso tenta
    obter a resposta real do processor. Retorna (resp_xml_str, parsed_dict).
    """
    resp_xml = None
    parsed = {"protocol_number": None, "protocol_date": None, "cstat": None, "xmotivo": None}
    if result is None and processor is None:
        return None, parsed
    # Se o result for o infEvento do pedido (request), tentar resposta no processor
    resp = getattr(result, "resposta", result)
    if resp is not None and _is_request_infevento(resp):
        real = _get_response_from_processor(processor)
        if real is not None:
            result = real
            resp = getattr(real, "resposta", real)
    if result is None:
        return None, parsed
    if resp is None:
        resp = result
    # 0) Resposta já é string/bytes (XML bruto do processor._transmissao)
    if isinstance(resp, (str, bytes)):
        xml_str = resp.decode("utf-8") if isinstance(resp, bytes) else resp
        if xml_str.strip():
            parsed = _parse_ret_evento_xml(xml_str)
            return xml_str, parsed
    # 1) XML: tentar atributo raw do resultado (alguns adaptadores guardam o XML)
    for attr in ("resposta_xml", "xml", "raw", "_xml", "retorno_xml"):
        raw = getattr(result, attr, None)
        if raw and isinstance(raw, (str, bytes)):
            resp_xml = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            parsed = _parse_ret_evento_xml(resp_xml)
            if resp_xml.strip():
                return resp_xml, parsed
    # 2) Resposta como elemento etree (lxml)
    try:
        from lxml import etree as et
        if hasattr(resp, "tag") and callable(getattr(resp, "get", None)):
            # Pode ser retEnvEvento; pegar primeiro retEvento se existir
            ns = "http://www.portalfiscal.inf.br/nfe"
            ret_evento = resp.find(f".//{{{ns}}}retEvento") or resp.find(".//retEvento")
            if ret_evento is None and hasattr(resp, "iter"):
                for e in resp.iter():
                    if e.tag is not None and (e.tag == "retEvento" or (isinstance(e.tag, str) and "retEvento" in e.tag)):
                        ret_evento = e
                        break
            elem_to_serialize = ret_evento if ret_evento is not None else resp
            resp_xml = et.tostring(elem_to_serialize, encoding="unicode", method="xml")
            parsed = _parse_ret_evento_xml(resp_xml)
            return resp_xml, parsed
    except Exception:
        pass
    # 3) Resposta com método to_xml()
    if hasattr(resp, "to_xml"):
        try:
            resp_xml = resp.to_xml()
            if isinstance(resp_xml, bytes):
                resp_xml = resp_xml.decode("utf-8")
            if resp_xml:
                parsed = _parse_ret_evento_xml(resp_xml)
                return resp_xml, parsed
        except Exception:
            pass
    # 3b) Resposta com método export() (ex.: alguns bindings nfelib)
    if hasattr(resp, "export") and callable(getattr(resp, "export")):
        try:
            from io import StringIO
            buf = StringIO()
            resp.export(buf, 0)
            resp_xml = buf.getvalue()
            if resp_xml and resp_xml.strip():
                parsed = _parse_ret_evento_xml(resp_xml)
                return resp_xml, parsed
        except Exception:
            pass
    # 4) Lista retEvento (ex.: retEnvEvento.retEvento)
    ret_evento_list = getattr(resp, "retEvento", None)
    if ret_evento_list is None:
        ret_evento_list = getattr(resp, "nfe40_retEvento", None)
    if ret_evento_list is not None and not isinstance(ret_evento_list, list):
        ret_evento_list = [ret_evento_list] if ret_evento_list else []
    if ret_evento_list:
        first = ret_evento_list[0]
        if hasattr(first, "to_xml"):
            try:
                resp_xml = first.to_xml()
                if isinstance(resp_xml, bytes):
                    resp_xml = resp_xml.decode("utf-8")
                if resp_xml:
                    parsed = _parse_ret_evento_xml(resp_xml)
                    return resp_xml, parsed
            except Exception:
                pass
        if hasattr(first, "tag"):
            try:
                from lxml import etree as et
                resp_xml = et.tostring(first, encoding="unicode", method="xml")
                parsed = _parse_ret_evento_xml(resp_xml)
                return resp_xml, parsed
            except Exception:
                pass
        # Extrair protocolo do objeto binding (nfe40_nProt / infEvento)
        inf = getattr(first, "infEvento", None) or getattr(first, "nfe40_infEvento", None)
        if inf is not None:
            n_prot = getattr(inf, "nProt", None) or getattr(inf, "nfe40_nProt", None)
            dh = getattr(inf, "dhRegEvento", None) or getattr(inf, "nfe40_dhRegEvento", None)
            if n_prot is not None:
                parsed["protocol_number"] = str(n_prot).strip()
            if dh is not None:
                parsed["protocol_date"] = dh
        return resp_xml or None, parsed
    # 5) infEvento direto na resposta (binding) — só usar se for RESPOSTA (cStat/dhRegEvento), não pedido
    inf = getattr(resp, "infEvento", None) or getattr(resp, "nfe40_infEvento", None)
    if inf is None:
        inf = resp
    if inf is not None and not _is_request_infevento(inf):
        n_prot = getattr(inf, "nProt", None) or getattr(inf, "nfe40_nProt", None)
        dh = getattr(inf, "dhRegEvento", None) or getattr(inf, "nfe40_dhRegEvento", None)
        if n_prot is not None:
            parsed["protocol_number"] = str(n_prot).strip()
        if dh is not None:
            parsed["protocol_date"] = dh
    return resp_xml, parsed


def _debug_log_cancel_request_response(chave, protocolo_autorizacao, justificativa, result, processor, resp_xml, parsed):
    """Debug: loga envio e resposta do cancelamento para apuração."""
    _logger.info(
        "[NFe cancel DEBUG] ENVIO: chave=%s protocolo_autorizacao=%s justificativa=%s",
        chave,
        protocolo_autorizacao,
        (justificativa[:60] + "..." if justificativa and len(justificativa) > 60 else justificativa),
    )
    _logger.info("[NFe cancel DEBUG] RESULT: type=%s", type(result).__name__ if result is not None else None)
    if result is not None:
        attrs = [a for a in dir(result) if not a.startswith("_")]
        _logger.info("[NFe cancel DEBUG] RESULT attrs: %s", attrs[:40])
        for attr in ("resposta", "cStat", "xMotivo", "infEvento", "retEvento", "nfe40_cStat", "nfe40_dhRegEvento", "nfe40_nProt"):
            if hasattr(result, attr):
                val = getattr(result, attr, None)
                _logger.info("[NFe cancel DEBUG] RESULT.%s = %s", attr, type(val).__name__ if val is not None else None)
    if processor is not None:
        for attr in ("resposta", "retorno", "_resposta", "retEvento"):
            if hasattr(processor, attr):
                val = getattr(processor, attr, None)
                _logger.info("[NFe cancel DEBUG] PROCESSOR.%s = %s", attr, type(val).__name__ if val is not None else None)
        trans = getattr(processor, "_transmissao", None)
        if trans is not None:
            for attr in ("resposta", "ultima_resposta", "resposta_xml", "response_body", "last_response"):
                if hasattr(trans, attr):
                    val = getattr(trans, attr, None)
                    if isinstance(val, (str, bytes)):
                        _logger.info("[NFe cancel DEBUG] PROCESSOR._transmissao.%s = len=%s preview=%s", attr, len(val), (val[:300] + "..." if len(val) > 300 else val))
                    else:
                        _logger.info("[NFe cancel DEBUG] PROCESSOR._transmissao.%s = %s", attr, type(val).__name__ if val is not None else None)
    if resp_xml:
        _logger.info("[NFe cancel DEBUG] resp_xml preview: %s", resp_xml[:500] + "..." if len(resp_xml) > 500 else resp_xml)
    _logger.info("[NFe cancel DEBUG] parsed protocol_number=%s protocol_date=%s", parsed.get("protocol_number"), parsed.get("protocol_date"))


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

        Nota (erpbrasil.edoc): cancela_documento() apenas monta o infEvento; é necessário
        chamar enviar_lote_evento([inf_evento]) para enviar à SEFAZ e obter a resposta (retEnvEvento).
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
        ambiente = getattr(doc.company_id, "nfe_environment", None) or "2"
        _logger.info(
            "[NFe cancel] enviando cancelamento à SEFAZ chave=%s protocolo=%s ambiente=%s (1=produção 2=homologação)",
            doc.nfe_key,
            doc.nfe_protocol,
            ambiente,
        )
        just_str = str(justificative).strip()[:255]
        # erpbrasil.edoc: cancela_documento() só monta o infEvento (pedido); quem envia é enviar_lote_evento().
        # Sem enviar_lote_evento a resposta da SEFAZ nunca é obtida.
        inf_evento = processor.cancela_documento(
            chave=doc.nfe_key,
            protocolo_autorizacao=doc.nfe_protocol,
            justificativa=just_str,
        )
        try:
            result = processor.enviar_lote_evento([inf_evento])
        except Exception as e:
            _logger.exception("[NFe cancel] Falha ao enviar evento à SEFAZ: %s", e)
            raise UserError(
                _("Falha ao enviar cancelamento à SEFAZ:\n\n%s") % str(e)
            ) from e
        _logger.info(
            "[NFe cancel] resposta SEFAZ recebida result=%s type=%s",
            result is not None,
            type(result).__name__ if result is not None else None,
        )
        _logger.info("NF-e %s cancelada na SEFAZ.", doc.nfe_key)

        # Extrai XML de resposta e protocolo (nProt/dhRegEvento); passa processor para obter resposta real se nfelib devolver só o pedido
        resp_xml, parsed = _extract_event_response_xml_and_protocol(result, processor=processor)
        _debug_log_cancel_request_response(
            doc.nfe_key,
            doc.nfe_protocol,
            just_str,
            result,
            processor,
            resp_xml,
            parsed,
        )
        # Se só temos o pedido (nfelib devolveu infEvento do request), não gravar como XML Response
        resp_obj = getattr(result, "resposta", result) if result is not None else None
        if resp_obj is not None and _is_request_infevento(resp_obj) and not parsed.get("protocol_number") and not parsed.get("protocol_date"):
            resp_xml = None
        # cStat/xMotivo do evento vêm de retEvento/infEvento (ex.: 135 = Evento vinculado), não da raiz retEnvEvento (128 = Lote processado)
        cstat, xmotivo = None, None
        if result is not None:
            resp = getattr(result, "resposta", result)
            if resp is not None:
                ret_list = getattr(resp, "retEvento", None)
                if ret_list is not None and not isinstance(ret_list, list):
                    ret_list = [ret_list] if ret_list else []
                if ret_list:
                    inf0 = getattr(ret_list[0], "infEvento", None) or getattr(ret_list[0], "nfe40_infEvento", None)
                    if inf0 is not None:
                        cstat = getattr(inf0, "cStat", None) or getattr(inf0, "nfe40_cStat", None)
                        xmotivo = getattr(inf0, "xMotivo", None) or getattr(inf0, "nfe40_xMotivo", None)
                if cstat is None:
                    inf_ev = getattr(resp, "infEvento", None) or getattr(resp, "nfe40_infEvento", None)
                    cstat = getattr(resp, "cStat", None) or getattr(inf_ev, "cStat", None) or getattr(inf_ev, "nfe40_cStat", None)
                    xmotivo = xmotivo or getattr(resp, "xMotivo", None) or getattr(inf_ev, "xMotivo", None) or getattr(inf_ev, "nfe40_xMotivo", None)
                _logger.info("[NFe cancel] cStat=%s xMotivo=%s", cstat, (str(xmotivo)[:80] + "..." if xmotivo and len(str(xmotivo)) > 80 else xmotivo))
        # idLote da resposta (retEnvEvento) -> Lot Receipt Number do evento
        id_lote = None
        if result is not None:
            resp = getattr(result, "resposta", result)
            if resp is not None:
                id_lote = getattr(resp, "idLote", None) or getattr(resp, "nfe40_idLote", None)
                if id_lote is not None:
                    id_lote = str(id_lote).strip()
        # Quando a resposta vem como XML (ex.: retEnvEvento), cStat/xMotivo podem estar em parsed
        if cstat is None and parsed.get("cstat"):
            cstat = parsed.get("cstat")
        if xmotivo is None and parsed.get("xmotivo"):
            xmotivo = parsed.get("xmotivo")
        # cStat 135 = evento registrado e vinculado à NF-e; sem essa confirmação não mudamos o status da nota
        if cstat is None or str(cstat) != "135":
            _logger.warning(
                "[NFe cancel] Resposta SEFAZ não recebida ou não sucesso (cStat=%s). Status da nota NÃO será alterado.",
                cstat,
            )
            if cstat is not None:
                # Rejeição da SEFAZ (ex.: 218 = NF-e já cancelada) — exibir mensagem retornada
                msg_sefaz = (xmotivo or "").strip() or _("Rejeição SEFAZ (cStat %s).") % cstat
                raise UserError(
                    _(
                        "A SEFAZ rejeitou o cancelamento. O status da nota NÃO foi alterado.\n\n"
                        "cStat: %s\n"
                        "Motivo: %s"
                    )
                    % (cstat, msg_sefaz)
                )
            # Resposta não recebida (biblioteca retornou só o pedido)
            raise UserError(
                _(
                    "O cancelamento foi enviado à SEFAZ, mas a resposta de confirmação não foi recebida "
                    "(a biblioteca retornou apenas o pedido enviado). O status da nota NÃO foi alterado. "
                    "Verifique se o cancelamento consta no Portal Nacional da NF-e; se sim, você pode "
                    "consultar a NF-e no documento para atualizar o status."
                )
            )
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
        # Garante gravação de XML Response e Protocol Number no evento de cancelamento
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
            "lot_receipt_number": id_lote or "",
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

    def _document_correction_nfe(self, justificative):
        """
        Carta de Correção NF-e: envia CCe à SEFAZ via lote de evento (como o cancelamento),
        registra em event_ids/chatter e aplica o workflow (correction_reason).
        Chamado pelo wizard para não depender do MRO.

        Fluxo alinhado ao cancelamento: carta_correcao() monta o infEvento;
        enviar_lote_evento([inf_evento]) envia e retorna retEnvEvento; só considera
        sucesso com cStat=135 no retEvento/infEvento.
        """
        self.ensure_one()
        doc = self
        if not (
            doc.document_type_id
            and doc.document_type_id.code == "55"
            and doc.nfe_key
            and doc.state_edoc == SITUACAO_EDOC_AUTORIZADA
        ):
            from odoo.addons.l10n_br_fiscal_edi.models.document_workflow import DocumentWorkflow
            return DocumentWorkflow._document_correction(self, justificative)
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
        # erpbrasil.edoc: carta_correcao() só monta o infEvento; enviar_lote_evento() envia e retorna a resposta (retEnvEvento).
        inf_evento = processor.carta_correcao(
            chave=doc.nfe_key,
            sequencia=str(sequencia),
            justificativa=txt,
        )
        try:
            result = processor.enviar_lote_evento([inf_evento])
        except Exception as e:
            _logger.exception("[CCe] Falha ao enviar evento à SEFAZ: %s", e)
            raise UserError(_("Falha ao enviar Carta de Correção à SEFAZ:\n\n%s") % str(e)) from e
        _logger.info(
            "[CCe] resposta SEFAZ recebida result=%s type=%s",
            result is not None,
            type(result).__name__ if result is not None else None,
        )

        # Extrai XML de resposta e protocolo do retorno (mesma lógica do cancelamento)
        resp_xml, parsed = _extract_event_response_xml_and_protocol(result, processor=processor)
        # Se a biblioteca retornou só o pedido (infEvento do request), não gravar como XML Response
        resp_obj = getattr(result, "resposta", result) if result is not None else None
        if resp_obj is not None and _is_request_infevento(resp_obj) and not parsed.get("protocol_number") and not parsed.get("protocol_date"):
            resp_xml = None
        # cStat/xMotivo do evento vêm de retEvento/infEvento (ex.: 135 = Evento vinculado), não da raiz retEnvEvento (128 = Lote processado)
        cstat, xmotivo = None, None
        if result is not None:
            resp = getattr(result, "resposta", result)
            if resp is not None:
                ret_list = getattr(resp, "retEvento", None)
                if ret_list is not None and not isinstance(ret_list, list):
                    ret_list = [ret_list] if ret_list else []
                if ret_list:
                    inf0 = getattr(ret_list[0], "infEvento", None) or getattr(ret_list[0], "nfe40_infEvento", None)
                    if inf0 is not None:
                        cstat = getattr(inf0, "cStat", None) or getattr(inf0, "nfe40_cStat", None)
                        xmotivo = getattr(inf0, "xMotivo", None) or getattr(inf0, "nfe40_xMotivo", None)
                if cstat is None:
                    inf_ev = getattr(resp, "infEvento", None) or getattr(resp, "nfe40_infEvento", None)
                    cstat = getattr(resp, "cStat", None) or getattr(inf_ev, "cStat", None) or getattr(inf_ev, "nfe40_cStat", None)
                    xmotivo = xmotivo or getattr(resp, "xMotivo", None) or getattr(inf_ev, "xMotivo", None) or getattr(inf_ev, "nfe40_xMotivo", None)
                _logger.info("[CCe] cStat=%s xMotivo=%s", cstat, (str(xmotivo)[:80] + "..." if xmotivo and len(str(xmotivo)) > 80 else xmotivo))
        # idLote da resposta (retEnvEvento) -> Lot Receipt Number do evento
        id_lote_cce = None
        if result is not None:
            resp = getattr(result, "resposta", result)
            if resp is not None:
                id_lote_cce = getattr(resp, "idLote", None) or getattr(resp, "nfe40_idLote", None)
                if id_lote_cce is not None:
                    id_lote_cce = str(id_lote_cce).strip()
                else:
                    id_lote_cce = ""
        # Quando a resposta vem como XML (ex.: retEnvEvento), cStat/xMotivo podem estar em parsed
        if cstat is None and parsed.get("cstat"):
            cstat = parsed.get("cstat")
        if xmotivo is None and parsed.get("xmotivo"):
            xmotivo = parsed.get("xmotivo")
        # cStat 135 = evento registrado e vinculado à NF-e; sem essa confirmação não registramos a CCe nem alteramos o documento
        if cstat is None or str(cstat) != "135":
            _logger.warning(
                "[CCe] Resposta SEFAZ não recebida ou não sucesso (cStat=%s). Carta de correção NÃO será registrada.",
                cstat,
            )
            if cstat is not None:
                msg_sefaz = (xmotivo or "").strip() or _("Rejeição SEFAZ (cStat %s).") % cstat
                raise UserError(
                    _(
                        "A SEFAZ rejeitou a Carta de Correção. A correção NÃO foi registrada.\n\n"
                        "cStat: %s\n"
                        "Motivo: %s"
                    )
                    % (cstat, msg_sefaz)
                )
            raise UserError(
                _(
                    "A Carta de Correção foi enviada à SEFAZ, mas a resposta de confirmação não foi recebida "
                    "(a biblioteca retornou apenas o pedido enviado). A correção NÃO foi registrada. "
                    "Verifique se a CCe consta no Portal Nacional da NF-e; se sim, você pode "
                    "consultar a NF-e no documento para atualizar."
                )
            )
        _logger.info("CCe nº %s registrada na SEFAZ para NF-e %s.", sequencia, doc.nfe_key)
        try:
            # Anexo no chatter com wrapper (retEvento + texto da correção) para o DACCe exibir "Texto da correção"
            xml_para_chatter = nfe_xml_utils.wrap_cce_response_with_text(resp_xml, txt)
            nfe_sefaz_chatter.post_sefaz_event(
                doc,
                event_type="Carta de Correção",
                cstat=str(cstat) if cstat else "135",
                xmotivo=str(xmotivo) if xmotivo else _("Evento registrado e vinculado à NF-e"),
                xml_content=xml_para_chatter,
            )
        except Exception as e:
            _logger.warning(
                "Falha ao postar CCe no chatter do documento %s: %s",
                doc.display_name,
                e,
                exc_info=True,
            )
        # Aba EDI: evento type "14" com document_id=doc para aparecer em event_ids e correction_event_ids
        env_edi = EVENT_ENV_PROD if (getattr(doc.company_id, "nfe_environment", None) or "2") == "1" else EVENT_ENV_HML
        event_model = doc.env["l10n_br_fiscal.event"]
        xml_para_evento = resp_xml or "<?xml version='1.0'?><retEvento/>"
        _logger.info("[CCe] criando l10n_br_fiscal.event type=14 document_id=%s sequence=%s", doc.id, sequencia)
        cce_event = event_model.create_event_save_xml(
            company_id=doc.company_id,
            environment=env_edi,
            event_type="14",
            xml_file=xml_para_evento,
            document_id=doc,
            sequence=str(sequencia),
            justification=txt[:255],
        )
        # Garante gravação de XML Response e Protocol Number no evento (como no cancelamento)
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
            "lot_receipt_number": id_lote_cce or "",
        })
        # Garantir que o anexo XML da CCe (chatter) esteja visível na busca do merge DACCe
        doc.env["ir.attachment"].flush_model()
        doc.make_pdf()
        # Forçar recarga do anexo do DANFE no cliente após atualização
        if doc.file_report_id:
            doc.invalidate_recordset(["file_report_id"])
        from odoo.addons.l10n_br_fiscal_edi.models.document_workflow import DocumentWorkflow
        return DocumentWorkflow._document_correction(self, justificative)

    def _document_correction(self, justificative):
        """
        Envia Carta de Correção Eletrônica (CCe) à SEFAZ (evento 110110).
        O wizard NF-e chama _document_correction_nfe diretamente para garantir execução (MRO).
        """
        return self._document_correction_nfe(justificative)
