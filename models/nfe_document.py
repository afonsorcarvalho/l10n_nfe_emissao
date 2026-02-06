# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Modelo de documento NF-e para emissão de Nota Fiscal Eletrônica.

A lógica de emissão, mapeamento, construção da NFe, PDF e eventos está nos mixins
e nos módulos de utilidade (nfe_xml_utils, nfe_sefaz_chatter, nfe_pdf_helpers).
Este arquivo declara os campos e os métodos mínimos do documento (create, validação, consulta).
"""

import base64
import logging
import re

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.l10n_br_fiscal.constants.fiscal import (
    DOCUMENT_ISSUER_COMPANY,
    EVENT_ENV_HML,
    EVENT_ENV_PROD,
    SITUACAO_EDOC_A_ENVIAR,
    SITUACAO_EDOC_AUTORIZADA,
    SITUACAO_EDOC_CANCELADA,
    SITUACAO_EDOC_EM_DIGITACAO,
)

from . import nfe_sefaz_chatter
from . import nfe_xml_utils

_logger = logging.getLogger(__name__)


class FiscalDocument(models.Model):
    """
    Estende documento fiscal com funcionalidades de emissão de NF-e.

    Herda mixins: mappers, builders, emission, pdf, events.
    Mapeia l10n_br_fiscal.document para XML NF-e (nfelib), assina
    digitalmente e transmite à SEFAZ via web services.
    """

    _name = "l10n_br_fiscal.document"
    # Apenas mixins "folha": emission já traz builders e mappers na cadeia.
    # Incluir mappers/builders explícitos causaria MRO inconsistente.
    _inherit = [
        "l10n_br_fiscal.document",
        "nfe.document.emission",
        "nfe.document.pdf",
        "nfe.document.events",
    ]

    # --- Campos de rastreamento da NF-e ---
    nfe_key = fields.Char(
        string="Chave NF-e",
        help=(
            "Chave de acesso da NF-e (44 dígitos). "
            "Gerada na confirmação do documento e imutável após definida."
        ),
        readonly=True,
        copy=False,
    )
    nfe_protocol = fields.Char(
        string="Protocolo",
        help="Número do protocolo de autorização da SEFAZ",
        readonly=True,
        copy=False,
    )
    nfe_xml = fields.Binary(
        string="XML NF-e",
        help="XML da NF-e gerado para envio",
        readonly=True,
        copy=False,
    )
    nfe_xml_signed = fields.Binary(
        string="XML Assinado",
        help="XML da NF-e assinado digitalmente",
        readonly=True,
        copy=False,
    )
    nfe_batch_recibo = fields.Char(
        string="Nº Recibo do Lote",
        help="Número do recibo (nRec) retornado pela SEFAZ ao processar o lote.",
        readonly=True,
        copy=False,
    )
    nfe_proc_xml = fields.Binary(
        string="XML procNFe",
        help="XML procNFe (NFe + protNFe) para geração do DANFE via BrazilFiscalReport.",
        readonly=True,
        copy=False,
    )
    nfe_xml_download_filename = fields.Char(
        string="Nome XML gerado para download",
        compute="_compute_nfe_xml_download_filename",
        help="Nome padrão ao baixar XML gerado: NFe-[Número]-gerado.xml",
    )
    nfe_xml_signed_download_filename = fields.Char(
        string="Nome XML assinado para download",
        compute="_compute_nfe_xml_download_filename",
        help="Nome padrão ao baixar XML assinado: NFe-[Número]-assinado.xml",
    )
    nfe_proc_xml_download_filename = fields.Char(
        string="Nome XML autorizado para download",
        compute="_compute_nfe_xml_download_filename",
        help="Nome padrão ao baixar XML autorizado (procNFe): NFe-[Número]-autorizado.xml",
    )
    danfe_download_filename = fields.Char(
        string="Nome DANFE para download",
        compute="_compute_danfe_download_filename",
        help="Nome do arquivo PDF do DANFE para exibir na ficha (download forçado).",
    )
    return_origin_document_id = fields.Many2one(
        comodel_name="l10n_br_fiscal.document",
        string="Documento de Devolução - Origem",
        copy=False,
        help="NF-e original referenciada em documento de devolução (finNFe=4).",
    )

    # Vínculo com fatura (account.move) quando o documento foi criado a partir dela.
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Fatura",
        index=True,
        copy=False,
        help="Fatura que originou este documento fiscal (botão Emitir NF-e na fatura).",
    )

    # --- Cobrança, pagamento e transporte (modelos persistentes com id para a interface) ---
    nfe40_cobr_id = fields.Many2one(
        comodel_name="nfe.document.cobr",
        string="Dados de Cobrança",
        help="Dados da cobrança da NF-e (fatura e duplicatas). Grupo <cobr> da NFe.",
        copy=False,
        readonly=False,
    )
    # Duplicatas da cobrança (para exibir em treeview na aba Financeiro)
    cobr_dup_ids = fields.One2many(
        related="nfe40_cobr_id.dup_ids",
        string="Duplicatas",
        readonly=False,
    )
    nfe40_pag_id = fields.Many2one(
        comodel_name="nfe.document.pag",
        string="Dados de Pagamento",
        help="Dados de pagamento da NF-e (formas de pagamento). Grupo <pag> da NFe.",
        copy=False,
        readonly=False,
    )
    # Campos relacionados para edição inline na aba Financeiro (formas de pagamento e troco)
    pag_detpag_ids = fields.One2many(
        related="nfe40_pag_id.detpag_ids",
        string="Formas de pagamento (inline)",
        readonly=False,
    )
    pag_v_troco = fields.Monetary(
        related="nfe40_pag_id.nfe40_vTroco",
        string="Troco (inline)",
        currency_field="currency_id",
        readonly=False,
    )
    nfe40_transp_id = fields.Many2one(
        comodel_name="nfe.document.transp",
        string="Dados de Transporte",
        help="Dados de transporte da NF-e (transportadora, veículo, volumes). Grupo <transp> da NFe.",
        copy=False,
        readonly=False,
    )

    # --- Campos relacionados para edição inline na aba Entrega (evitar abrir formulário separado) ---
    transp_mod_freite = fields.Selection(
        related="nfe40_transp_id.nfe40_modFrete",
        string="Modalidade do frete",
        readonly=False,
    )
    transp_transporta_id = fields.Many2one(
        related="nfe40_transp_id.transporta_id",
        string="Transportadora",
        readonly=False,
    )
    transp_veiculo_id = fields.Many2one(
        related="nfe40_transp_id.veicTransp_id",
        string="Veículo",
        readonly=False,
    )
    transp_vol_ids = fields.One2many(
        related="nfe40_transp_id.vol_ids",
        string="Volumes",
        readonly=False,
    )
    transp_ret_transp_id = fields.Many2one(
        related="nfe40_transp_id.retTransp_id",
        string="Retenção ICMS",
        readonly=False,
    )

    @api.depends("document_number")
    def _compute_nfe_xml_download_filename(self):
        """Calcula nomes para download dos XMLs: NFe-[Numero]-gerado/assinado/autorizado.xml"""
        for rec in self:
            numero = rec.document_number or rec.id
            rec.nfe_xml_download_filename = f"NFe-{numero}-gerado.xml"
            rec.nfe_xml_signed_download_filename = f"NFe-{numero}-assinado.xml"
            rec.nfe_proc_xml_download_filename = f"NFe-{numero}-autorizado.xml"

    @api.depends("file_report_id", "file_report_id.name")
    def _compute_danfe_download_filename(self):
        """Preenche o nome do arquivo do DANFE quando file_report_id existe."""
        for rec in self:
            rec.danfe_download_filename = (rec.file_report_id.name or "") if rec.file_report_id else ""

    def view_pdf(self):
        """
        Visualizar PDF (DANFE). Definido no modelo principal para garantir prioridade
        sobre o EDI e permitir logs de debug. DANFE disponível sempre que houver
        XML de envio; não autorizada exibe "SEM VALOR FISCAL", cancelada exibe "CANCELADA".
        """
        self.ensure_one()
        _logger.debug(
            "[DANFE] view_pdf doc_id=%s document_type_id=%s code=%s nfe_key=%s state_edoc=%s "
            "nfe_proc_xml=%s nfe_xml_signed=%s nfe_xml=%s",
            self.id,
            self.document_type_id.id if self.document_type_id else None,
            self.document_type_id.code if self.document_type_id else None,
            (self.nfe_key or "")[:20] + "..." if self.nfe_key and len(self.nfe_key) > 20 else (self.nfe_key or "(vazio)"),
            self.state_edoc,
            "sim" if getattr(self, "nfe_proc_xml", None) else "nao",
            "sim" if getattr(self, "nfe_xml_signed", None) else "nao",
            "sim" if getattr(self, "nfe_xml", None) else "nao",
        )
        code = str(getattr(self.document_type_id, "code", None) or "").strip() if self.document_type_id else ""
        if self.document_type_id and code == "55":
            # Chama explicitamente o make_pdf do mixin nfe.document.pdf (via MRO); o EDI
            # sobrescreve make_pdf com pass, então self.make_pdf() pode não executar nossa lógica.
            for cls in type(self).__mro__:
                if getattr(cls, "_name", None) == "nfe.document.pdf" and hasattr(cls, "make_pdf"):
                    cls.make_pdf(self)
                    break
            if self.file_report_id:
                _logger.debug("[DANFE] view_pdf abrindo file_report_id=%s", self.file_report_id.id)
                return self._target_new_tab(self.file_report_id)
            _logger.warning(
                "[DANFE] view_pdf doc_id=%s make_pdf não gerou file_report_id (state=%s)",
                self.id, self.state_edoc,
            )
            if not getattr(self, "nfe_proc_xml", None) and not getattr(self, "nfe_xml_signed", None) and not getattr(self, "nfe_xml", None):
                raise UserError(
                    _("Gere o XML da NF-e (botão 'Emitir NF-e') para visualizar o DANFE.")
                )
            raise UserError(
                _("Não foi possível gerar o DANFE. Verifique os logs do servidor (doc_id=%s).")
                % self.id
            )
        return super().view_pdf()

    @api.model_create_multi
    def create(self, vals_list):
        """
        Ao criar documento NF-e (55), preenche document_number com o próximo
        da sequência da série e cria registro de transporte para edição inline na aba Entrega.
        """
        docs = super().create(vals_list)
        Transp = self.env["nfe.document.transp"]
        for doc in docs:
            if (
                doc.document_type_id
                and doc.document_type_id.code == "55"
                and doc.issuer == DOCUMENT_ISSUER_COMPANY
                and doc.document_serie_id
                and not doc.document_number
                and not doc.nfe_key
            ):
                doc.document_number = doc.document_serie_id.next_seq_number()
            # Cria registro de transporte para tipo 55 se não foi passado (permite edição inline na aba Entrega)
            if (
                doc.document_type_id
                and doc.document_type_id.code == "55"
                and not doc.nfe40_transp_id
            ):
                transp = Transp.create({"nfe40_modFrete": "9"})
                doc.nfe40_transp_id = transp
            # Cria registro de pagamento para tipo 55 se não foi passado (permite edição inline na aba Financeiro)
            if (
                doc.document_type_id
                and doc.document_type_id.code == "55"
                and not doc.nfe40_pag_id
            ):
                doc.nfe40_pag_id = self.env["nfe.document.pag"].create({})
        return docs

    # Campos relacionados para edição inline dos dados de transporte na aba Entrega
    transp_mod_frete = fields.Selection(
        related="nfe40_transp_id.nfe40_modFrete",
        string="Modalidade do frete",
        readonly=False,
    )
    transp_transporta_id = fields.Many2one(
        related="nfe40_transp_id.transporta_id",
        comodel_name="nfe.document.transporta",
        string="Transportadora (inline)",
        readonly=False,
    )
    transp_veiculo_id = fields.Many2one(
        related="nfe40_transp_id.veicTransp_id",
        comodel_name="nfe.document.veiculo",
        string="Veículo (inline)",
        readonly=False,
    )
    transp_vol_ids = fields.One2many(
        related="nfe40_transp_id.vol_ids",
        string="Volumes (inline)",
        readonly=False,
    )
    transp_rettransp_id = fields.Many2one(
        related="nfe40_transp_id.retTransp_id",
        comodel_name="nfe.document.rettransp",
        string="Retenção transporte (inline)",
        readonly=False,
    )

    def write(self, vals):
        """
        Protege a chave NF-e (nfe_key) contra alterações indevidas.
        Garante registro de transporte para NF-e 55 ao gravar campos de transporte.
        """
        # Para NF-e 55: se está gravando algum campo de pagamento e ainda não tem registro, cria um por documento
        pag_related = {"pag_detpag_ids", "pag_v_troco"}
        if pag_related & set(vals) and not vals.get("nfe40_pag_id"):
            docs_need_pag = [
                doc
                for doc in self
                if doc.document_type_id
                and doc.document_type_id.code == "55"
                and not doc.nfe40_pag_id
            ]
            if docs_need_pag:
                Pag = self.env["nfe.document.pag"]
                for doc in docs_need_pag:
                    pag = Pag.create({})
                    doc.write({**vals, "nfe40_pag_id": pag.id})
                rest = self - docs_need_pag
                if rest:
                    rest.write(vals)
                return True
        # Para NF-e 55: se está gravando algum campo de transporte e ainda não tem registro, cria um por documento
        transp_related = {
            "transp_mod_freite",
            "transp_transporta_id",
            "transp_veiculo_id",
            "transp_vol_ids",
            "transp_ret_transp_id",
        }
        if transp_related & set(vals) and not vals.get("nfe40_transp_id"):
            docs_need_transp = [
                doc
                for doc in self
                if doc.document_type_id
                and doc.document_type_id.code == "55"
                and not doc.nfe40_transp_id
            ]
            if docs_need_transp:
                Transp = self.env["nfe.document.transp"]
                for doc in docs_need_transp:
                    transp = Transp.create({"nfe40_modFrete": "9"})
                    doc.write({**vals, "nfe40_transp_id": transp.id})
                rest = self - docs_need_transp
                if rest:
                    rest.write(vals)
                return True
        # Se a escrita não envolve nfe_key, segue normalmente
        if "nfe_key" not in vals:
            return super().write(vals)
        
        # Se está tentando escrever nfe_key, verifica se já existe
        for record in self:
            if record.nfe_key and vals.get("nfe_key") != record.nfe_key:
                # Já existe chave e está tentando alterar para valor diferente: bloqueia
                raise ValidationError(
                    _(
                        "A chave NF-e não pode ser alterada após definida.\n\n"
                        "Chave atual: %s\n"
                        "Tentativa de alteração para: %s\n\n"
                        "A chave é gerada na confirmação do documento e é imutável."
                    ) % (record.nfe_key, vals.get("nfe_key"))
                )
        
        return super().write(vals)

    def read(self, fields=None, load="_classic_read"):
        """
        Garante registros de pagamento e transporte para NF-e 55 em digitação ao carregar,
        para que os campos das abas Financeiro e Entrega sejam editáveis (related exige o Many2one).
        """
        need_pag = (
            fields is None
            or "nfe40_pag_id" in (fields or [])
            or "pag_detpag_ids" in (fields or [])
            or "pag_v_troco" in (fields or [])
        )
        if need_pag:
            docs_no_pag = self.filtered(lambda d: not d.nfe40_pag_id)
            if docs_no_pag:
                need_attrs = ["state_edoc", "document_type_id"]
                if fields and not all(f in fields for f in need_attrs):
                    docs_no_pag.read(need_attrs)
                to_create_pag = []
                for doc in docs_no_pag:
                    if getattr(doc, "state_edoc", None) != "em_digitacao":
                        continue
                    dt = doc.document_type_id
                    if not dt or getattr(dt, "code", None) != "55":
                        continue
                    to_create_pag.append((doc, self.env["nfe.document.pag"].create({})))
                for doc, pag in to_create_pag:
                    doc.write({"nfe40_pag_id": pag.id})
        need_transp = (
            fields is None
            or "nfe40_transp_id" in (fields or [])
            or any(
                f in (fields or [])
                for f in (
                    "transp_mod_freite",
                    "transp_transporta_id",
                    "transp_veiculo_id",
                    "transp_vol_ids",
                    "transp_ret_transp_id",
                )
            )
        )
        if need_transp:
            docs_no_transp = self.filtered(lambda d: not d.nfe40_transp_id)
            if docs_no_transp:
                need_attrs = ["state_edoc", "document_type_id"]
                if fields and not all(f in fields for f in need_attrs):
                    docs_no_transp.read(need_attrs)
                to_create = []
                for doc in docs_no_transp:
                    if getattr(doc, "state_edoc", None) != "em_digitacao":
                        continue
                    dt = doc.document_type_id
                    if not dt or getattr(dt, "code", None) != "55":
                        continue
                    transp = self.env["nfe.document.transp"].create(
                        {"nfe40_modFrete": "9"}
                    )
                    to_create.append((doc, transp))
                for doc, transp in to_create:
                    doc.write({"nfe40_transp_id": transp.id})
        return super().read(fields=fields, load=load)

    def _create_return(self):
        """Cria documento de devolução e vincula à NF-e de origem."""
        return_docs = super()._create_return()
        for doc, origin in zip(return_docs, self):
            if (
                doc.document_type_id
                and doc.document_type_id.code == "55"
                and origin.nfe_key
            ):
                doc.return_origin_document_id = origin
        return return_docs

    def _get_nfe_serie_from_company(self):
        """
        Retorna a série NF-e configurada na empresa conforme nfe_environment.
        Só aplicável a documento tipo 55 (NF-e). Se não houver série
        configurada para o ambiente, retorna recordset vazio.
        """
        self.ensure_one()
        if not self.company_id or not self.document_type_id or self.document_type_id.code != "55":
            return self.env["l10n_br_fiscal.document.serie"]
        env = getattr(self.company_id, "nfe_environment", None) or "2"
        if env == "1":
            return self.company_id.nfe_serie_producao_id or self.env["l10n_br_fiscal.document.serie"]
        return self.company_id.nfe_serie_homologacao_id or self.env["l10n_br_fiscal.document.serie"]

    @api.depends(
        "document_type_id",
        "issuer",
        "company_id",
        "company_id.nfe_environment",
        "company_id.nfe_serie_homologacao_id",
        "company_id.nfe_serie_producao_id",
    )
    def _compute_document_serie_id(self):
        """
        Para NF-e (55), usa série de homologação/produção da empresa quando
        configurada; caso contrário segue a lógica padrão (tipo/operação).
        """
        for doc in self:
            # Só aplica série da empresa quando ainda não há série (evita sobrescrever escolha do usuário)
            if (
                not doc.document_serie_id
                and doc.document_type_id
                and doc.document_type_id.code == "55"
                and doc.issuer == DOCUMENT_ISSUER_COMPANY
                and doc.company_id
            ):
                serie_company = doc._get_nfe_serie_from_company()
                if serie_company:
                    doc.document_serie_id = serie_company
                    continue
            if (
                not doc.document_serie_id
                and doc.document_type_id
                and doc.issuer == DOCUMENT_ISSUER_COMPANY
            ):
                doc.document_serie_id = doc.document_type_id.get_document_serie(
                    doc.company_id, doc.fiscal_operation_id
                )
            elif doc.document_serie_id is None:
                doc.document_serie_id = False

    @api.onchange("document_type_id")
    def _onchange_document_type_id_nfe_serie(self):
        """
        Ao selecionar tipo de documento NF-e (55), aplica a série de
        homologação ou produção da empresa, se configurada.
        """
        if (
            self.document_type_id
            and self.document_type_id.code == "55"
            and self.issuer == DOCUMENT_ISSUER_COMPANY
            and self.company_id
        ):
            serie_company = self._get_nfe_serie_from_company()
            if serie_company:
                self.document_serie_id = serie_company

    @api.onchange("document_serie_id")
    def _onchange_document_serie_id_nfe_number(self):
        """
        Ao selecionar a série em documento NF-e (55), sugere o próximo
        número da sequência se document_number estiver vazio.
        """
        if (
            self.document_type_id
            and self.document_type_id.code == "55"
            and self.issuer == DOCUMENT_ISSUER_COMPANY
            and self.document_serie_id
            and not self.document_number
            and not self.nfe_key
        ):
            self.document_number = self.document_serie_id.next_seq_number()

    def action_document_confirm(self):
        """
        Sobrescreve action_document_confirm: para documentos tipo 55 (NF-e),
        chama action_confirmar_nfe (gera chave e confirma documento).
        Para outros tipos, usa comportamento padrão do l10n_br_fiscal.
        """
        self.ensure_one()
        
        # Se for NF-e (tipo 55), usa nosso fluxo de confirmação (gera chave)
        if self.document_type_id and self.document_type_id.code == "55":
            return self.action_confirmar_nfe()
        
        # Outros tipos de documento: comportamento padrão
        return super().action_document_confirm()

    def action_document_send(self):
        """
        Sobrescreve action_document_send: para documentos tipo 55 (NF-e), 
        chama action_emit_nfe (emissão NF-e à SEFAZ).
        Para outros tipos, usa comportamento padrão do l10n_br_fiscal.
        """
        self.ensure_one()
        
        # Se for NF-e (tipo 55), usa nosso fluxo de emissão
        if self.document_type_id and self.document_type_id.code == "55":
            return self.action_emit_nfe()
        
        # Outros tipos de documento: comportamento padrão
        return super().action_document_send()

    def _validate_nfe_emission(self):
        """
        Valida se o documento está pronto para emissão de NF-e.
        :raises ValidationError: se dados obrigatórios estão faltando
        """
        self.ensure_one()
        if not self.company_id:
            raise ValidationError("Empresa não definida no documento")
        if not self.company_id.cnpj_cpf:
            raise ValidationError("CNPJ da empresa não configurado")
        if not self.partner_id:
            raise ValidationError("Destinatário não definido")
        if not self.fiscal_line_ids:
            raise ValidationError("Documento sem itens")
        certificate_helper = self.env["l10n_nfe_emissao.certificate.helper"]
        try:
            certificate_helper.get_certificate_data(self.company_id.id)
        except UserError as e:
            raise ValidationError(str(e))
        _logger.info("Documento %s validado para emissão de NF-e", self.display_name)

    def action_consultar_nfe(self):
        """
        Consulta NF-e na SEFAZ e atualiza procNFe/XML assinado quando ausentes.
        Usa consulta_documento (nfeConsultaNF); fallback distribuição DFe por chave.
        """
        for doc in self:
            if (
                not doc.nfe_key
                or not doc.document_type_id
                or doc.document_type_id.code != "55"
            ):
                continue
            try:
                processor = doc._get_nfe_processor()
                result = processor.consulta_documento(chave=doc.nfe_key)
                resp = getattr(result, "resposta", result)
                if resp is None:
                    raise UserError(_("Resposta vazia da consulta à SEFAZ."))

                c_stat = getattr(resp, "cStat", None) or (
                    getattr(getattr(resp, "retorno", None), "cStat", None)
                )
                if hasattr(resp, "cStat") and resp.cStat is not None:
                    c_stat = str(resp.cStat)
                if c_stat is None and hasattr(resp, "find") and callable(resp.find):
                    ns = "http://www.portalfiscal.inf.br/nfe"
                    ce = resp.find(f".//{{{ns}}}cStat")
                    c_stat = ce.text if ce is not None else None

                x_motivo = getattr(resp, "xMotivo", "") or ""
                if str(c_stat) != "100":
                    raise UserError(
                        _("NF-e não autorizada na SEFAZ (cStat=%s): %s")
                        % (c_stat, x_motivo)
                    )

                resp_xml = None
                if hasattr(resp, "to_xml"):
                    try:
                        resp_xml = resp.to_xml()
                        if isinstance(resp_xml, bytes):
                            resp_xml = resp_xml.decode("utf-8")
                    except Exception:
                        pass
                elif hasattr(resp, "tag"):
                    try:
                        resp_xml = etree.tostring(resp, encoding="unicode", method="xml")
                    except Exception:
                        pass

                prot_nfe_elem = getattr(resp, "protNFe", None)
                if prot_nfe_elem is None and hasattr(resp, "find"):
                    ns = "http://www.portalfiscal.inf.br/nfe"
                    prot_nfe_elem = resp.find(f".//{{{ns}}}protNFe")

                prot_str = None
                if prot_nfe_elem is not None:
                    if hasattr(prot_nfe_elem, "tag"):
                        prot_str = etree.tostring(
                            prot_nfe_elem, encoding="unicode", method="xml"
                        )
                    elif hasattr(prot_nfe_elem, "to_xml"):
                        prot_str = prot_nfe_elem.to_xml()
                        if isinstance(prot_str, bytes):
                            prot_str = prot_str.decode("utf-8")
                    else:
                        prot_str = str(prot_nfe_elem)

                if prot_str and not doc.nfe_protocol and prot_nfe_elem is not None:
                    n_prot_val = None
                    if hasattr(prot_nfe_elem, "find"):
                        ns = "http://www.portalfiscal.inf.br/nfe"
                        inf_prot = prot_nfe_elem.find(f".//{{{ns}}}infProt")
                        if inf_prot is not None:
                            n_prot = inf_prot.find(f".//{{{ns}}}nProt")
                            n_prot_val = n_prot.text if n_prot is not None else None
                    elif hasattr(prot_nfe_elem, "infProt"):
                        n_prot_val = getattr(prot_nfe_elem.infProt, "nProt", None)
                    if n_prot_val:
                        doc.nfe_protocol = str(n_prot_val)

                proc_salvo = False
                xml_assinado = None
                if doc.nfe_xml_signed:
                    xml_assinado = base64.b64decode(doc.nfe_xml_signed).decode("utf-8")
                elif doc.nfe_xml and prot_str:
                    try:
                        xml_gerado = base64.b64decode(doc.nfe_xml).decode("utf-8")
                        xml_compacto = xml_gerado.replace("\n", "").replace("\r", "").replace("\t", "")
                        xml_compacto = re.sub(r">\s+<", "><", xml_compacto)
                        xml_bytes = xml_compacto.encode("utf-8")
                        from erpbrasil.assinatura.assinatura import Assinatura

                        xml_etree = etree.fromstring(xml_bytes)
                        inf_id = f"NFe{doc.nfe_key}"
                        assinador = Assinatura(processor._transmissao.certificado)
                        xml_assinado = assinador.assina_xml2(xml_etree, inf_id)
                        if isinstance(xml_assinado, bytes):
                            xml_assinado = xml_assinado.decode("utf-8")
                        else:
                            xml_assinado = str(xml_assinado)
                        xml_assinado = xml_assinado.strip()
                        doc.nfe_xml_signed = base64.b64encode(xml_assinado.encode("utf-8"))
                    except Exception as reas:
                        _logger.warning("Não foi possível re-assinar nfe_xml: %s", reas)

                if xml_assinado and prot_str:
                    nfe_frag = nfe_xml_utils.strip_xml_declaration(xml_assinado)
                    prot_frag = nfe_xml_utils.strip_xml_declaration(prot_str)
                    proc_nfe_xml = (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
                        f"{nfe_frag}\n{prot_frag}\n</nfeProc>"
                    )
                    doc.nfe_proc_xml = base64.b64encode(proc_nfe_xml.encode("utf-8"))
                    proc_salvo = True
                elif not doc.nfe_proc_xml:
                    dfe = self.env["l10n_br_fiscal.dfe"].search(
                        [("company_id", "=", doc.company_id.id)], limit=1
                    )
                    if dfe and hasattr(dfe, "_download_document"):
                        doc_zip = dfe._download_document(doc.nfe_key)
                        if doc_zip:
                            from odoo.addons.l10n_br_fiscal_dfe.tools import utils as dfe_utils

                            schema = (getattr(doc_zip, "schema", None) or "").lower()
                            if "procnfe" in schema:
                                content = getattr(doc_zip, "valueOf_", None) or getattr(doc_zip, "value", None)
                                if content:
                                    xml_bytes = dfe_utils.parse_gzip_xml(content).read()
                                    doc.nfe_proc_xml = base64.b64encode(xml_bytes)
                                    proc_salvo = True

                # Status Description (aba EDI)
                if hasattr(doc, "status_code"):
                    doc.status_code = str(c_stat or "")
                if hasattr(doc, "status_name"):
                    doc.status_name = (x_motivo or "")[:500]

                extra = (
                    _(
                        "Consulta NF-e realizada. procNFe e/ou XML assinado "
                        "atualizados a partir da SEFAZ."
                    )
                    if proc_salvo
                    else _(
                        "procNFe não pôde ser recuperado: XML assinado não estava "
                        "salvo e distribuição DFe não retornou o documento."
                    )
                )
                nfe_sefaz_chatter.post_sefaz_event(
                    doc,
                    event_type="Consulta NF-e",
                    cstat=str(c_stat),
                    xmotivo=x_motivo,
                    xml_content=resp_xml,
                    body_extra=extra,
                )

                # Aba EDI: registra evento Consulta NFE (type "4") em event_ids
                if hasattr(doc, "authorization_event_id"):
                    env_edi = EVENT_ENV_PROD if (getattr(doc.company_id, "nfe_environment", None) or "2") == "1" else EVENT_ENV_HML
                    event_model = doc.env["l10n_br_fiscal.event"]
                    xml_para_evento = resp_xml or "<?xml version='1.0'?><retConsSitNFe/>"
                    consulta_event = event_model.create_event_save_xml(
                        company_id=doc.company_id,
                        environment=env_edi,
                        event_type="4",
                        xml_file=xml_para_evento,
                        document_id=doc,
                    )
                    consulta_event.set_done(
                        status_code=str(c_stat or ""),
                        response=(x_motivo or "")[:500],
                        protocol_date=None,
                        protocol_number=doc.nfe_protocol or "",
                        file_response_xml=resp_xml,
                    )
                    xmotivo_str = (x_motivo or "").strip()
                    consulta_event.write({
                        "message": xmotivo_str[:500] if xmotivo_str else "Response received",
                        "origin": doc.display_name or doc.name or "",
                        "sequence": "1",
                        "partner_id": doc.partner_id.id if doc.partner_id else False,
                    })

                if proc_salvo:
                    doc.make_pdf()
                    _logger.info("NF-e %s: procNFe atualizado via consulta.", doc.nfe_key)
                else:
                    _logger.warning(
                        "NF-e %s: consulta OK, mas procNFe não recuperado (nfe_xml_signed ausente).",
                        doc.nfe_key,
                    )

            except UserError:
                raise
            except Exception as e:
                _logger.exception("Erro ao consultar NF-e %s: %s", doc.nfe_key, e)
                raise UserError(
                    _("Erro ao consultar NF-e na SEFAZ:\n\n%s") % str(e)
                ) from e

        doc = self[:1]
        if doc.exists():
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Consulta NF-e"),
                    "message": _("Consulta realizada. Verifique as mensagens do documento."),
                    "type": "success",
                    "sticky": False,
                    "next": nfe_sefaz_chatter.action_reload_form(doc),
                },
            }
        return {"type": "ir.actions.act_window_close"}
