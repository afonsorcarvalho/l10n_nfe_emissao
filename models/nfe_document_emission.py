# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Mixin de emissão NF-e: preparar estrutura, assinar, transmitir e processar retorno.

Responsabilidade única: fluxo de emissão (prepare, sign, send, parse protocol).
Depende de nfe.document.builders; usa nfe_xml_utils e nfe_sefaz_chatter.
"""

import base64
import logging
import random
import re

from nfelib.nfe.bindings.v4_0.nfe_v4_00 import Nfe
from requests import Session

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_br_fiscal.constants.fiscal import (
    EVENT_ENV_HML,
    EVENT_ENV_PROD,
    SITUACAO_EDOC_A_ENVIAR,
    SITUACAO_EDOC_AUTORIZADA,
    SITUACAO_EDOC_EM_DIGITACAO,
    SITUACAO_EDOC_REJEITADA,
)

from . import nfe_sefaz_chatter
from . import nfe_xml_utils

try:
    from erpbrasil.transmissao import TransmissaoSOAP
    from nfelib.nfe.ws.edoc_legacy import NFeAdapter
except ImportError:
    TransmissaoSOAP = None  # type: ignore[misc, assignment]
    NFeAdapter = None  # type: ignore[misc, assignment]

_logger = logging.getLogger(__name__)


class NFeDocumentEmission(models.AbstractModel):
    """
    Fluxo de emissão: _prepare_nfe_emission, assinatura, envio, parse do protocolo, action_emit_nfe.
    """

    _name = "nfe.document.emission"
    _description = "NF-e document emission flow"
    _inherit = ["nfe.document.builders"]
    _abstract = True

    def _gerar_chave_nfe(self):
        """
        Gera a chave de acesso da NF-e (44 dígitos) com código numérico aleatório.
        
        Retorna dict com: chave_nfe (44 dígitos), c_nf (8 dígitos), dv (1 dígito).
        Usada na confirmação do documento para persistir a chave antes da transmissão.
        """
        self.ensure_one()
        codigo_uf = self._get_codigo_uf(self.company_id.state_id)
        cnpj_raw = self.company_id.cnpj_cpf or ""
        cnpj = "".join(filter(str.isdigit, cnpj_raw)).zfill(14)
        if len(cnpj) != 14:
            raise ValidationError(
                _("CNPJ da empresa inválido: %s. Deve ter 14 dígitos.") % cnpj_raw
            )

        data_emissao = self.document_date or fields.Datetime.now()
        data_br = self._utc_to_brazil(data_emissao)
        aamm = data_br.strftime("%y%m")
        serie_raw = str(self.document_serie_id.code or "1")
        serie = "".join(filter(str.isdigit, serie_raw)).zfill(3)
        numero_raw = str(self.document_number or "1")
        numero = "".join(filter(str.isdigit, numero_raw)).zfill(9)
        tipo_emissao = "1"
        codigo_numerico = str(random.randint(0, 99999999)).zfill(8)
        chave_parcial = f"{codigo_uf}{aamm}{cnpj}55{serie}{numero}{tipo_emissao}{codigo_numerico}"

        if len(chave_parcial) != 43 or not chave_parcial.isdigit():
            raise ValidationError(
                _("Erro ao montar chave de acesso. Chave parcial: %s (len=%d). "
                  "Verifique CNPJ, série e número do documento.")
                % (chave_parcial, len(chave_parcial))
            )

        dv = self._calcular_dv_nfe(chave_parcial)
        chave_nfe = chave_parcial + str(dv)
        
        return {
            "chave_nfe": chave_nfe,
            "c_nf": codigo_numerico,
            "dv": str(dv),
        }

    def _prepare_nfe_emission(self):
        """
        Monta a estrutura completa da NF-e para emissão (nfelib.Nfe leiaute 4.0).
        Chama _validate_nfe_emission (definido no modelo principal).
        
        Se o documento já possui nfe_key (confirmado anteriormente), usa essa chave
        em vez de gerar nova. Caso contrário, gera nova chave (comportamento original).
        """
        self.ensure_one()
        self._validate_nfe_emission()

        # Se já existe chave NF-e (documento confirmado ou reenvio), usa ela
        if self.nfe_key and len(self.nfe_key) == 44:
            chave_nfe = self.nfe_key
            # Extrai componentes da chave: posições 35-43 = c_nf (codigo_numerico), 43-44 = dv
            codigo_numerico = chave_nfe[35:43]
            dv = chave_nfe[43:44]
            nfe_id = f"NFe{chave_nfe}"
            _logger.info(
                "Usando chave NF-e existente (documento confirmado): %s", chave_nfe
            )
        else:
            # Comportamento original: gera nova chave com código numérico aleatório
            chave_info = self._gerar_chave_nfe()
            chave_nfe = chave_info["chave_nfe"]
            codigo_numerico = chave_info["c_nf"]
            dv = chave_info["dv"]
            nfe_id = f"NFe{chave_nfe}"

        # Constrói InfNfe com todos os grupos
        inf_nfe_kwargs = {
            "versao": "4.00",
            "Id": nfe_id,
            "ide": self._build_nfe_ide(c_dv=dv, c_nf=codigo_numerico),
            "emit": self._build_nfe_emit(),
            "dest": self._build_nfe_dest(),
            "det": self._build_nfe_items(),
            "total": self._build_nfe_total(),
            "transp": self._build_nfe_transp(),
            "pag": self._build_nfe_pag(),
        }
        
        # Adiciona cobrança se houver dados preenchidos
        cobr = self._build_nfe_cobr()
        if cobr is not None:
            inf_nfe_kwargs["cobr"] = cobr
        
        inf_nfe = Nfe.InfNfe(**inf_nfe_kwargs)
        return Nfe(infNFe=inf_nfe)

    def _calcular_dv_nfe(self, chave_parcial):
        """Calcula dígito verificador da chave de acesso NF-e (43 dígitos -> 0-9)."""
        pesos = [4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8,
                 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        soma = sum(int(chave_parcial[i]) * pesos[i] for i in range(43))
        resto = soma % 11
        return 0 if resto in (0, 1) else 11 - resto

    def action_confirmar_nfe(self):
        """
        Confirma o documento NF-e: gera e persiste a chave de acesso (se ainda não existir)
        e altera o estado para 'a_enviar' (Aguardando envio / Confirmada).

        Se o documento já possui chave NF-e (ex.: reenvio após rejeição), a chave não
        é gerada novamente; apenas o estado é alterado para permitir novo envio.
        """
        self.ensure_one()

        # Validações
        if self.document_type_id.code != "55":
            raise UserError(_("Esta ação é válida apenas para documentos tipo 55 (NF-e)."))

        if self.state_edoc != SITUACAO_EDOC_EM_DIGITACAO:
            raise UserError(
                _("Apenas documentos em 'Em digitação' podem ser confirmados. "
                  "Estado atual: %s") % (self.state_edoc or "indefinido")
            )

        # Valida dados para emissão (série, número, empresa, parceiro, itens, certificado)
        self._validate_nfe_emission()

        chave_nfe = self.nfe_key
        if not chave_nfe:
            # Gera e persiste a chave de acesso (apenas quando ainda não existe)
            chave_info = self._gerar_chave_nfe()
            chave_nfe = chave_info["chave_nfe"]
            vals = {"nfe_key": chave_nfe}
            if hasattr(self, "document_key"):
                vals["document_key"] = chave_nfe
            self.write(vals)
            _logger.info("Documento NF-e %s confirmado. Chave gerada: %s", self.id, chave_nfe)
        else:
            _logger.info(
                "Documento NF-e %s confirmado usando chave existente: %s",
                self.id,
                chave_nfe,
            )

        # Altera estado para 'a_enviar' (Aguardando envio / Confirmada)
        self.write({"state_edoc": SITUACAO_EDOC_A_ENVIAR})

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Documento confirmado"),
                "message": _(
                    "O documento está pronto para emissão.\n\n"
                    "Chave: %s\n\n"
                    "Use o botão 'Emitir NF-e' para transmitir à SEFAZ."
                ) % (self.nfe_key or chave_nfe),
                "type": "success",
                "sticky": False,
                "next": nfe_sefaz_chatter.action_reload_form(self),
            },
        }

    # NOTA: Método comentado - usamos o botão "Voltar p/ Em Digitação" do l10n_br_fiscal
    # A proteção da chave NF-e está no método write() de nfe_document.py
    # def action_corrigir_reenvio_nfe(self):
    #     """
    #     Permite editar documento NF-e rejeitado pela SEFAZ para corrigir inconsistências
    #     e reenviar com a mesma chave.
    #     
    #     O documento volta para o estado 'em_digitacao', mas a chave NF-e permanece
    #     inalterada. Após corrigir os dados, o usuário pode usar 'Emitir NF-e' para
    #     reenviar à SEFAZ com a mesma chave de acesso.
    #     
    #     NOTA: Não usado - substituído pelo botão "Voltar p/ Em Digitação" padrão.
    #     """
    #     self.ensure_one()
    #     
    #     # Validações
    #     if self.document_type_id.code != "55":
    #         raise UserError(_("Esta ação é válida apenas para documentos tipo 55 (NF-e)."))
    #     
    #     if self.state_edoc != SITUACAO_EDOC_REJEITADA:
    #         raise UserError(
    #             _("Esta ação é válida apenas para documentos rejeitados. "
    #               "Estado atual: %s") % (self.state_edoc or "indefinido")
    #         )
    #     
    #     if not self.nfe_key:
    #         raise UserError(
    #             _("Este documento não possui chave NF-e. "
    #               "Não é possível reenviar sem chave.")
    #         )
    #     
    #     # Volta para 'em_digitacao' (chave permanece inalterada)
    #     self.write({"state_edoc": SITUACAO_EDOC_EM_DIGITACAO})
    #     
    #     _logger.info(
    #         "Documento NF-e %s (chave %s) voltou para edição após rejeição.",
    #         self.id, self.nfe_key
    #     )
    #     
    #     return {
    #         "type": "ir.actions.client",
    #         "tag": "display_notification",
    #         "params": {
    #             "title": _("Documento disponível para edição"),
    #             "message": _(
    #                 "O documento voltou para o estado 'Em digitação'.\n\n"
    #                 "Corrija os dados conforme o motivo da rejeição e use o botão "
    #                 "'Emitir NF-e' para reenviar à SEFAZ.\n\n"
    #                 "A chave NF-e (%s) não será alterada."
    #             ) % self.nfe_key,
    #             "type": "info",
    #             "sticky": False,
    #             "next": nfe_sefaz_chatter.action_reload_form(self),
    #         },
    #     }

    def _get_nfe_processor(self):
        """Cria processador NFe para transmissão à SEFAZ (NFeAdapter + TransmissaoSOAP)."""
        if NFeAdapter is None or TransmissaoSOAP is None:
            raise UserError(
                _("Bibliotecas erpbrasil.transmissao e nfelib não disponíveis. "
                  "Reinstale as dependências Python (requirements.txt) e reinicie o Odoo.")
            )
        certificado = self.company_id._get_br_ecertificate()
        session = Session()
        session.verify = False
        codigo_uf = self._get_codigo_uf(self.company_id.state_id)
        ambiente = getattr(self.company_id, "nfe_environment", None) or "2"
        return NFeAdapter(
            transmissao=TransmissaoSOAP(certificado, session),
            uf=int(codigo_uf),
            versao="4.00",
            ambiente=ambiente,
            mod="55",
            envio_sincrono=True,
        )

    def _get_next_nfe_id_lote(self):
        """Retorna o próximo idLote único (ir.sequence) para envio à SEFAZ."""
        self.ensure_one()
        lote_id = self.env["ir.sequence"].next_by_code("l10n_nfe_emissao.nfe_lote_id")
        if not lote_id:
            raise UserError(
                _(
                    "Sequência 'NF-e idLote' não encontrada. "
                    "Atualize o módulo l10n_nfe_emissao para criar a sequência."
                )
            )
        return lote_id

    def _parse_protocolo_nfe(self, proc):
        """Extrai chave, protocolo, cStat, xMotivo, autorizada, nRec e dhRecbto do retorno do envio."""
        if not proc or not getattr(proc, "resposta", None):
            return {
                "autorizada": False,
                "cStat": None,
                "xMotivo": _("Sem resposta da SEFAZ"),
                "nRec": None,
                "dhRecbto": None,
            }
        from lxml import etree

        resp = proc.resposta
        dh_recbto = None
        ns_uri = "http://www.portalfiscal.inf.br/nfe"
        if isinstance(resp, etree._Element):
            c_stat = getattr(resp, "cStat", None)
            x_motivo = getattr(resp, "xMotivo", "")
            prot_nfe_elem = resp.protNFe if hasattr(resp, "protNFe") else None
            chave = None
            protocolo = None
            if prot_nfe_elem is not None:
                ns = {"nfe": ns_uri}
                inf_prot = prot_nfe_elem.find(".//nfe:infProt", ns)
                if inf_prot is None:
                    inf_prot = prot_nfe_elem.find(f".//{{{ns_uri}}}infProt")
                if inf_prot is not None:
                    ch_nfe_elem = inf_prot.find(f".//{{{ns_uri}}}chNFe")
                    n_prot_elem = inf_prot.find(f".//{{{ns_uri}}}nProt")
                    c_stat_prot_elem = inf_prot.find(f".//{{{ns_uri}}}cStat")
                    x_motivo_prot_elem = inf_prot.find(f".//{{{ns_uri}}}xMotivo")
                    dh_elem = inf_prot.find(f".//{{{ns_uri}}}dhRecbto")
                    chave = ch_nfe_elem.text if ch_nfe_elem is not None else None
                    protocolo = n_prot_elem.text if n_prot_elem is not None else None
                    c_stat = c_stat_prot_elem.text if c_stat_prot_elem is not None else c_stat
                    x_motivo = x_motivo_prot_elem.text if x_motivo_prot_elem is not None else x_motivo
                    dh_recbto = dh_elem.text if dh_elem is not None and dh_elem.text else None
        else:
            c_stat = getattr(resp, "cStat", None)
            x_motivo = getattr(resp, "xMotivo", "")
            chave = None
            protocolo = None
            prot_nfe = getattr(resp, "protNFe", None)
            if prot_nfe is not None:
                if not isinstance(prot_nfe, list):
                    prot_nfe = [prot_nfe]
                if len(prot_nfe) > 0:
                    first_prot = prot_nfe[0]
                    if hasattr(first_prot, "find") and callable(first_prot.find):
                        inf_prot = first_prot.find(f".//{{{ns_uri}}}infProt")
                        if inf_prot is not None:
                            ch_elem = inf_prot.find(f".//{{{ns_uri}}}chNFe")
                            n_elem = inf_prot.find(f".//{{{ns_uri}}}nProt")
                            c_elem = inf_prot.find(f".//{{{ns_uri}}}cStat")
                            x_elem = inf_prot.find(f".//{{{ns_uri}}}xMotivo")
                            dh_elem = inf_prot.find(f".//{{{ns_uri}}}dhRecbto")
                            chave = ch_elem.text if ch_elem is not None else None
                            protocolo = n_elem.text if n_elem is not None else None
                            if c_elem is not None and c_elem.text:
                                c_stat = c_elem.text
                            if x_elem is not None and x_elem.text:
                                x_motivo = x_elem.text
                            dh_recbto = dh_elem.text if dh_elem is not None and dh_elem.text else None
                    else:
                        inf_prot = getattr(first_prot, "infProt", None)
                        if inf_prot is not None:
                            chave = getattr(inf_prot, "chNFe", None)
                            protocolo = getattr(inf_prot, "nProt", None)
                            c_stat = getattr(inf_prot, "cStat", c_stat)
                            x_motivo = getattr(inf_prot, "xMotivo", x_motivo)
                            dh_recbto = getattr(inf_prot, "dhRecbto", None)

        autorizada = str(c_stat) == "100" and bool(chave)
        n_rec = getattr(resp, "nRec", None) if resp is not None else None
        return {
            "chave": chave,
            "protocolo": protocolo,
            "cStat": c_stat,
            "xMotivo": x_motivo or "",
            "autorizada": autorizada,
            "nRec": n_rec,
            "dhRecbto": dh_recbto,
        }

    def action_emit_nfe(self):
        """
        Emite NF-e: gera XML (usando chave existente se já confirmado), assina e transmite à SEFAZ.
        
        Fluxo: _prepare_nfe_emission -> assinar -> enviar_lote -> parse -> atualizar documento.
        
        Permite emissão quando:
        - state_edoc == 'a_enviar' (documento confirmado, aguardando envio), ou
        - state_edoc == 'em_digitacao' E nfe_key preenchida (reenvio após rejeição).
        """
        self.ensure_one()
        
        # Validação de tipo de documento
        if self.document_type_id.code != "55":
            raise UserError(_("Esta ação é válida apenas para documentos tipo 55 (NF-e)."))
        
        # Validação de estado: permite a_enviar ou (em_digitacao com chave = reenvio)
        if self.state_edoc == SITUACAO_EDOC_A_ENVIAR:
            # Documento confirmado, aguardando envio (fluxo normal)
            pass
        elif self.state_edoc == SITUACAO_EDOC_EM_DIGITACAO and self.nfe_key:
            # Documento em edição após rejeição, com chave já definida (reenvio)
            _logger.info(
                "Reenvio de NF-e %s após rejeição. Chave: %s",
                self.id, self.nfe_key
            )
        else:
            raise UserError(
                _(
                    "Para emitir a NF-e, o documento deve estar confirmado.\n\n"
                    "Estado atual: %s\n\n"
                    "Use o botão 'Confirmar' para gerar a chave NF-e e confirmar o documento "
                    "antes de emitir."
                ) % (self.state_edoc or "indefinido")
            )
        
        try:
            nfe = self._prepare_nfe_emission()
            xml_nfe = nfe.to_xml(indent="  ")
            xml_bytes = xml_nfe if isinstance(xml_nfe, bytes) else xml_nfe.encode("utf-8")
            self.nfe_xml = base64.b64encode(xml_bytes)
            self.env.cr.commit()

            processor = self._get_nfe_processor()
            _logger.info("Assinando XML da NF-e...")
            xml_nfe_str = nfe.to_xml()
            if isinstance(xml_nfe_str, bytes):
                xml_nfe_str = xml_nfe_str.decode("utf-8")
            xml_compacto_antes = xml_nfe_str.replace("\n", "").replace("\r", "").replace("\t", "")
            xml_compacto_antes = re.sub(r">\s+<", "><", xml_compacto_antes)
            xml_bytes = xml_compacto_antes.encode("utf-8")

            from erpbrasil.assinatura.assinatura import Assinatura
            from lxml import etree

            xml_etree = etree.fromstring(xml_bytes)
            assinador = Assinatura(processor._transmissao.certificado)
            xml_assinado = assinador.assina_xml2(xml_etree, nfe.infNFe.Id)
            if isinstance(xml_assinado, bytes):
                xml_assinado = xml_assinado.decode("utf-8")
            else:
                xml_assinado = str(xml_assinado)

            if "<Signature" not in xml_assinado and "<ds:Signature" not in xml_assinado:
                raise UserError(_("Erro: assinatura digital não foi gerada. Verifique o certificado A1."))

            xml_para_envio = xml_assinado.strip()
            lote_id = self._get_next_nfe_id_lote()
            envelope_xml = (
                f'<enviNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
                f'<idLote>{lote_id}</idLote>'
                f'<indSinc>1</indSinc>'
                f'{xml_para_envio}'
                f'</enviNFe>'
            )
            codigo_uf = self._get_codigo_uf(self.company_id.state_id)
            ambiente = getattr(self.company_id, "nfe_environment", None) or "2"
            transmissor = self.env["l10n_nfe_emissao.transmissao"]
            envio_info = transmissor.enviar_lote(
                xml_envelope=envelope_xml,
                lote_id=lote_id,
                processor=processor,
                codigo_uf=codigo_uf,
                ambiente=ambiente,
                modelo="55",
            )
            retorno_raw = envio_info["response"]
            if envio_info.get("contingencia"):
                _logger.warning(
                    "Transmissão NF-e realizada em contingência via %s (%s).",
                    envio_info.get("descricao") or "servidor",
                    envio_info.get("url"),
                )
            else:
                _logger.info("Transmissão NF-e realizada via %s.", envio_info.get("url"))

            soap_tree = etree.fromstring(retorno_raw.content)
            ns = {"soap": "http://schemas.xmlsoap.org/soap/envelope/"}
            body = soap_tree.find(".//soap:Body", ns)
            if body is None:
                body = soap_tree.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Body")
            if body is None or len(body) == 0:
                raise UserError(_("Resposta SOAP inválida: Body vazio."))

            ret_envi_nfe_elem = body[0]
            ns_nfe = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
            ret_env_nfe = ret_envi_nfe_elem.find(".//nfe:retEnviNFe", ns_nfe)
            if ret_env_nfe is None:
                ret_env_nfe = ret_envi_nfe_elem.find(".//{http://www.portalfiscal.inf.br/nfe}retEnviNFe")
            if ret_env_nfe is None:
                ret_env_nfe = ret_envi_nfe_elem

            ret_envi_nfe_xml = etree.tostring(ret_env_nfe, encoding="unicode")
            ns_nfe_uri = "http://www.portalfiscal.inf.br/nfe"
            c_stat = ret_env_nfe.find(f".//{{{ns_nfe_uri}}}cStat")
            x_motivo = ret_env_nfe.find(f".//{{{ns_nfe_uri}}}xMotivo")
            prot_nfe_elem = ret_env_nfe.find(f".//{{{ns_nfe_uri}}}protNFe")
            inf_rec = ret_env_nfe.find(f".//{{{ns_nfe_uri}}}infRec")
            n_rec_elem = inf_rec.find(f".//{{{ns_nfe_uri}}}nRec") if inf_rec is not None else None
            n_rec_val = n_rec_elem.text if n_rec_elem is not None and n_rec_elem.text else None
            c_stat_val = c_stat.text if c_stat is not None else None
            x_motivo_val = x_motivo.text if x_motivo is not None else ""

            body_extra = nfe_xml_utils.format_ret_envi_nfe_details(ret_env_nfe)
            nfe_sefaz_chatter.post_sefaz_event(
                self,
                event_type="Autorização NF-e",
                cstat=c_stat_val,
                xmotivo=x_motivo_val,
                xml_content=ret_envi_nfe_xml,
                body_extra=body_extra or None,
            )

            class RespObj:
                def __init__(self, cStat, xMotivo, prot_nfe, nRec=None):
                    self.cStat = cStat
                    self.xMotivo = xMotivo
                    self.protNFe = prot_nfe if prot_nfe is not None else []
                    self.nRec = nRec

            class ProcResult:
                def __init__(self, resposta):
                    self.resposta = resposta

            proc_result = ProcResult(RespObj(c_stat_val, x_motivo_val, prot_nfe_elem, n_rec_val))
            dados = self._parse_protocolo_nfe(proc_result)

            # Data/hora do recebimento pela SEFAZ (dhRecbto) em UTC naive para uso no evento e nas datas do documento
            protocol_date = None
            if dados.get("dhRecbto"):
                try:
                    from dateutil.parser import parse as dateutil_parse
                    from datetime import timezone
                    protocol_dt = dateutil_parse(dados["dhRecbto"])
                    if protocol_dt.tzinfo:
                        protocol_dt = protocol_dt.astimezone(timezone.utc).replace(tzinfo=None)
                    protocol_date = protocol_dt
                except Exception:
                    pass

            # Chave NF-e: só preenche se ainda não existir (confirmação já grava a chave)
            if dados.get("chave"):
                if not self.nfe_key:
                    # Primeira emissão (sem confirmação prévia): grava chave do retorno
                    self.nfe_key = dados["chave"]
                    if hasattr(self, "document_key"):
                        self.document_key = dados["chave"]
                    _logger.info("Chave NF-e gravada do retorno SEFAZ: %s", dados["chave"])
                elif self.nfe_key != dados["chave"]:
                    # Chave já existe mas difere do retorno: alerta (não deveria acontecer)
                    _logger.warning(
                        "Chave NF-e no documento (%s) difere da retornada pela SEFAZ (%s). "
                        "Mantendo chave do documento.",
                        self.nfe_key, dados["chave"]
                    )
                else:
                    # Chave já existe e é igual ao retorno: OK, não sobrescreve
                    _logger.debug("Chave NF-e já gravada, não sobrescrita: %s", self.nfe_key)
            if dados.get("protocolo"):
                self.nfe_protocol = dados["protocolo"]
            if dados.get("nRec"):
                self.nfe_batch_recibo = dados["nRec"]
            # Status Description (aba EDI): status_code + status_name preenchem o campo computado
            if hasattr(self, "status_code"):
                self.status_code = str(dados.get("cStat") or "")
            if hasattr(self, "status_name"):
                self.status_name = (dados.get("xMotivo") or "")[:500]

            # Preenche aba EDI: evento de Autorização de Uso (l10n_br_fiscal.event type "0")
            if self.document_type_id and self.document_type_id.code == "55":
                env_edi = EVENT_ENV_PROD if (getattr(self.company_id, "nfe_environment", None) or "2") == "1" else EVENT_ENV_HML
                event_model = self.env["l10n_br_fiscal.event"]
                auth_event = event_model.create_event_save_xml(
                    company_id=self.company_id,
                    environment=env_edi,
                    event_type="0",
                    xml_file=xml_para_envio,
                    document_id=self,
                )
                auth_event.set_done(
                    status_code=str(dados.get("cStat") or ""),
                    response=dados.get("xMotivo") or "",
                    protocol_date=protocol_date,
                    protocol_number=dados.get("protocolo") or "",
                    file_response_xml=ret_envi_nfe_xml,
                )
                # Message (mensagem SEFAZ na lista de eventos), Origin, Sequence, Partner, Lot Receipt
                xmotivo_str = (dados.get("xMotivo") or "").strip()
                auth_event.write({
                    "message": xmotivo_str[:500] if xmotivo_str else "Response received",
                    "origin": self.display_name or self.name or "",
                    "sequence": "1",
                    "partner_id": self.partner_id.id if self.partner_id else False,
                    "lot_receipt_number": self.nfe_batch_recibo or "",
                })
                self.authorization_event_id = auth_event

            if dados.get("autorizada"):
                self.nfe_xml_signed = base64.b64encode(xml_para_envio.encode("utf-8"))
                if prot_nfe_elem is not None:
                    prot_str = etree.tostring(prot_nfe_elem, encoding="unicode", method="xml")
                    nfe_frag = nfe_xml_utils.strip_xml_declaration(xml_para_envio)
                    prot_frag = nfe_xml_utils.strip_xml_declaration(prot_str)
                    proc_nfe_xml = (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
                        f"{nfe_frag}\n{prot_frag}\n"
                        "</nfeProc>"
                    )
                    self.nfe_proc_xml = base64.b64encode(proc_nfe_xml.encode("utf-8"))
                # Document Report (PDF DANFE) na aba EDI: gera e grava em file_report_id
                self.make_pdf()
                # Atualiza Data do Documento e Data de Entrada/Saída quando vazios (data do protocolo SEFAZ)
                if protocol_date:
                    if not self.document_date:
                        self.document_date = protocol_date
                    if not self.date_in_out:
                        self.date_in_out = protocol_date

            if dados.get("autorizada"):
                self.state_edoc = SITUACAO_EDOC_AUTORIZADA
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("NF-e autorizada"),
                        "message": _("NF-e %s autorizada pela SEFAZ. Protocolo: %s") % (dados["chave"], dados["protocolo"]),
                        "type": "success",
                        "sticky": False,
                        "next": nfe_sefaz_chatter.action_reload_form(self),
                    },
                }
            else:
                c_stat_val = dados.get("cStat")
                n_rec = dados.get("nRec") or self.nfe_batch_recibo
                if str(c_stat_val) == "104":
                    msg_104 = _(
                        "104 - Lote já processado anteriormente.\n\n"
                        "O lote de notas fiscais enviado já foi processado pela SEFAZ. "
                        "O sistema já recebeu, analisou e respondeu ao envio — "
                        "não é necessário reenviá-lo."
                    )
                    if n_rec:
                        msg_104 += _("\n\nNº recibo do lote: %s (use na consulta de recibo se precisar do resultado).") % n_rec
                    return {
                        "type": "ir.actions.client",
                        "tag": "display_notification",
                        "params": {
                            "title": _("Lote já processado"),
                            "message": msg_104,
                            "type": "warning",
                            "sticky": True,
                            "next": nfe_sefaz_chatter.action_reload_form(self),
                        },
                    }
                self.state_edoc = SITUACAO_EDOC_REJEITADA
                # Formata mensagem de rejeição com código e motivo bem destacados
                msg_rejeicao = _(
                    "Código: %s\n"
                    "Motivo: %s\n\n"
                    "Consulte o motivo da rejeição no formulário e use o botão 'Editar' "
                    "para corrigir os dados antes de reenviar."
                ) % (c_stat_val or "?", dados.get("xMotivo", "Sem descrição"))
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("NF-e rejeitada pela SEFAZ"),
                        "message": msg_rejeicao,
                        "type": "danger",
                        "sticky": True,
                        "next": nfe_sefaz_chatter.action_reload_form(self),
                    },
                }

        except UserError:
            raise
        except Exception as e:
            _logger.error("Erro ao emitir NF-e: %s", str(e), exc_info=True)
            raise UserError(
                _(
                    "Erro ao emitir NF-e:\n\n%s\n\n"
                    "Verifique os logs para mais detalhes."
                ) % str(e)
            )
