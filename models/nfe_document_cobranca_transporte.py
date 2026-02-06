# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Modelos persistentes para Cobrança, Pagamento e Transporte da NF-e.

Os modelos da especificação (nfe.40.cobr, nfe.40.pag, nfe.40.transp) são
AbstractModel e não possuem tabela/id. Estes modelos (nfe.document.*) são
Model normais com tabela, usados na interface e no builder para gerar o XML.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.l10n_br_base.tools import check_cnpj_cpf

SELECTION_UF=[
            ("AC", "AC"), ("AL", "AL"), ("AM", "AM"), ("AP", "AP"), ("BA", "BA"),
            ("CE", "CE"), ("DF", "DF"), ("ES", "ES"), ("GO", "GO"), ("MA", "MA"),
            ("MG", "MG"), ("MS", "MS"), ("MT", "MT"), ("PA", "PA"), ("PB", "PB"),
            ("PE", "PE"), ("PI", "PI"), ("PR", "PR"), ("RJ", "RJ"), ("RN", "RN"),
            ("RO", "RO"), ("RR", "RR"), ("RS", "RS"), ("SC", "SC"), ("SE", "SE"),
            ("SP", "SP"), ("TO", "TO"),
        ]
class NfeDocumentCobrFat(models.Model):
    """Dados da fatura (grupo fat da cobrança NF-e)."""

    _name = "nfe.document.cobr.fat"
    _description = "NF-e Cobrança - Fatura"

    nfe40_nFat = fields.Char(string="Número da fatura")
    nfe40_vOrig = fields.Monetary(
        string="Valor original",
        currency_field="currency_id",
    )
    nfe40_vDesc = fields.Monetary(
        string="Desconto",
        currency_field="currency_id",
    )
    nfe40_vLiq = fields.Monetary(
        string="Valor líquido",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.ref("base.BRL", raise_if_not_found=False),
    )


class NfeDocumentCobrDup(models.Model):
    """Duplicata (grupo dup da cobrança NF-e)."""

    _name = "nfe.document.cobr.dup"
    _description = "NF-e Cobrança - Duplicata"

    cobr_id = fields.Many2one(
        comodel_name="nfe.document.cobr",
        ondelete="cascade",
        required=True,
    )
    nfe40_nDup = fields.Char(
        string="Número da parcela",
        required=True,
        help="Obrigatório com 3 algarismos sequenciais. Ex.: 001, 002, 003 (regra SEFAZ 852).",
    )
    nfe40_dVenc = fields.Date(
        string="Data de vencimento",
        required=True,
        help="Obrigatória e deve ser >= Data de Emissão da NF-e; parcelas em ordem crescente (900, 850).",
    )
    nfe40_vDup = fields.Monetary(
        string="Valor",
        required=True,
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.ref("base.BRL", raise_if_not_found=False),
    )


class NfeDocumentCobr(models.Model):
    """Dados de cobrança da NF-e (fatura e duplicatas)."""

    _name = "nfe.document.cobr"
    _description = "NF-e Cobrança"

    fat_id = fields.Many2one(
        comodel_name="nfe.document.cobr.fat",
        string="Fatura",
        ondelete="set null",
    )
    dup_ids = fields.One2many(
        comodel_name="nfe.document.cobr.dup",
        inverse_name="cobr_id",
        string="Duplicatas",
    )


# Códigos oficiais tPag (Meio de pagamento) - schema NF-e, 2 dígitos
SELECTION_TPAG = [
    ("01", "01 - Dinheiro"),
    ("02", "02 - Cheque"),
    ("03", "03 - Cartão de Crédito"),
    ("04", "04 - Cartão de Débito"),
    ("05", "05 - Crédito Loja"),
    ("10", "10 - Vale Alimentação"),
    ("11", "11 - Vale Refeição"),
    ("12", "12 - Vale Presente"),
    ("13", "13 - Vale Combustível"),
    ("15", "15 - Boleto Bancário"),
    ("16", "16 - Depósito Bancário"),
    ("17", "17 - PIX"),
    ("18", "18 - Transferência / Carteira Digital"),
    ("19", "19 - Fidelidade / Cashback / Crédito Virtual"),
    ("90", "90 - Sem pagamento"),
    ("99", "99 - Outros"),
]


class NfeDocumentPagDetpag(models.Model):
    """Detalhamento da forma de pagamento (grupo detPag)."""

    _name = "nfe.document.pag.detpag"
    _description = "NF-e Pagamento - Detalhe"

    pag_id = fields.Many2one(
        comodel_name="nfe.document.pag",
        ondelete="cascade",
        required=True,
    )
    nfe40_indPag = fields.Selection(
        selection=[("0", "À Vista"), ("1", "À Prazo")],
        string="Indicador",
    )
    nfe40_tPag = fields.Selection(
        SELECTION_TPAG,
        string="Meio de pagamento (tPag)",
        required=True,
    )
    nfe40_xPag = fields.Char(string="Descrição")
    nfe40_vPag = fields.Monetary(
        string="Valor",
        required=True,
        currency_field="currency_id",
    )
    nfe40_dPag = fields.Date(
        string="Data pagamento",
        help="Opcional. Se informada, deve ser <= Data de Emissão da NF-e (rejeição 657 quando inválida).",
    )
    nfe40_CNPJPag = fields.Char(string="CNPJ transacional")
    nfe40_UFPag = fields.Selection(SELECTION_UF, string="UF")
   
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.ref("base.BRL", raise_if_not_found=False),
    )


class NfeDocumentPag(models.Model):
    """Dados de pagamento da NF-e."""

    _name = "nfe.document.pag"
    _description = "NF-e Pagamento"

    detpag_ids = fields.One2many(
        comodel_name="nfe.document.pag.detpag",
        inverse_name="pag_id",
        string="Formas de pagamento",
    )
    nfe40_vTroco = fields.Monetary(
        string="Troco",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.ref("base.BRL", raise_if_not_found=False),
    )


class NfeDocumentTransporta(models.Model):
    """
    Dados do transportador.

    Pode ser vinculado a um res.partner (Transportadora): ao selecionar o parceiro,
    os dados são preenchidos automaticamente. CNPJ/CPF validados via erpbrasil.
    """

    _name = "nfe.document.transporta"
    _description = "NF-e Transportadora"
    # Nome exibido do registro = nome do transportador (parceiro) ou razão social preenchida
    _rec_name = "partner_id"

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Transportadora (parceiro)",
        ondelete="set null",
        help="Selecione um contato cadastrado para preencher os dados automaticamente.",
    )
    nfe40_CNPJ = fields.Char(string="CNPJ")
    nfe40_CPF = fields.Char(string="CPF")
    nfe40_xNome = fields.Char(string="Razão Social/Nome")
    nfe40_IE = fields.Char(string="Inscrição Estadual")
    nfe40_xEnder = fields.Char(string="Endereço")
    nfe40_xMun = fields.Char(string="Município")
    nfe40_UF = fields.Selection(
        SELECTION_UF,
        string="UF",
    )

    @api.depends("partner_id", "partner_id.name", "nfe40_xNome")
    def _compute_display_name(self):
        """Nome exibido = transportador (partner_id.name) ou razão social (nfe40_xNome)."""
        for rec in self:
            if rec.partner_id:
                rec.display_name = rec.partner_id.name
            else:
                rec.display_name = rec.nfe40_xNome or f"Transportadora #{rec.id}"

    @api.onchange("partner_id")
    def _onchange_partner_id_transporta(self):
        """Preenche dados da transportadora a partir do parceiro selecionado."""
        if not self.partner_id:
            return
        p = self.partner_id
        # Nome
        self.nfe40_xNome = p.legal_name or p.name or ""
        # Inscrição Estadual (compat l10n_br: inscr_est ou l10n_br_ie_code)
        self.nfe40_IE = getattr(p, "inscr_est", None) or getattr(p, "l10n_br_ie_code", None) or ""
        # Município
        self.nfe40_xMun = p.city_id.name if getattr(p, "city_id", None) and p.city_id else ""
        # UF
        if getattr(p, "state_id", None) and p.state_id:
            self.nfe40_UF = p.state_id.code
        else:
            self.nfe40_UF = False
        # Endereço completo (rua, número, complemento, bairro, cidade, UF, CEP)
        ender_parts = []
        if getattr(p, "street", None) and p.street:
            ender_parts.append(p.street)
        if getattr(p, "street_number", None) and p.street_number:
            ender_parts.append(str(p.street_number))
        if getattr(p, "street2", None) and p.street2:
            ender_parts.append(p.street2)
        if getattr(p, "district", None) and p.district:
            ender_parts.append(p.district)
        if ender_parts:
            self.nfe40_xEnder = ", ".join(ender_parts)
        else:
            self.nfe40_xEnder = p.street or ""
        # CNPJ ou CPF conforme tipo do parceiro
        cnpj_cpf = getattr(p, "cnpj_cpf", None) or getattr(p, "vat", None) or ""
        if cnpj_cpf:
            digits = "".join(c for c in str(cnpj_cpf) if c.isdigit())
            if len(digits) == 14:
                self.nfe40_CNPJ = digits
                self.nfe40_CPF = False
            elif len(digits) == 11:
                self.nfe40_CPF = digits
                self.nfe40_CNPJ = False
            else:
                self.nfe40_CNPJ = False
                self.nfe40_CPF = False
        else:
            self.nfe40_CNPJ = False
            self.nfe40_CPF = False

    @api.constrains("nfe40_CNPJ", "nfe40_CPF")
    def _check_cnpj_cpf_transporta(self):
        """Exige um único identificador (CNPJ ou CPF) e valida formato e dígitos."""
        country_br = self.env.ref("base.br", raise_if_not_found=False)
        if not country_br:
            return
        for rec in self:
            cnpj = (rec.nfe40_CNPJ or "").strip()
            cpf = (rec.nfe40_CPF or "").strip()
            if cnpj and cpf:
                raise ValidationError(
                    "Informe apenas CNPJ ou apenas CPF do transportador, não ambos."
                )
            if not cnpj and not cpf:
                continue
            value = cnpj if cnpj else cpf
            check_cnpj_cpf(self.env, value, country_br)


class NfeDocumentVeiculo(models.Model):
    """Dados do veículo (trator/reboque)."""

    _name = "nfe.document.veiculo"
    _description = "NF-e Veículo"

    nfe40_placa = fields.Char(string="Placa")
    nfe40_UF = fields.Selection(
        SELECTION_UF,
        string="UF",
    )
    nfe40_RNTC = fields.Char(string="RNTC (ANTT)")


class NfeDocumentRettransp(models.Model):
    """Retenção ICMS do transporte."""

    _name = "nfe.document.rettransp"
    _description = "NF-e Retenção ICMS Transporte"

    nfe40_vServ = fields.Monetary(
        string="Valor do serviço",
        currency_field="currency_id",
    )
    nfe40_vBCRet = fields.Monetary(
        string="BC retenção",
        currency_field="currency_id",
    )
    nfe40_pICMSRet = fields.Float(string="Alíquota %", digits=(3, 2))
    nfe40_vICMSRet = fields.Monetary(
        string="Valor retido",
        currency_field="currency_id",
    )
    nfe40_CFOP = fields.Char(string="CFOP")
    nfe40_cMunFG = fields.Char(string="Código município")
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.ref("base.BRL", raise_if_not_found=False),
    )


class NfeDocumentTranspVolLacres(models.Model):
    """Lacre do volume."""

    _name = "nfe.document.transp.vol.lacres"
    _description = "NF-e Volume - Lacre"

    vol_id = fields.Many2one(
        comodel_name="nfe.document.transp.vol",
        ondelete="cascade",
        required=True,
    )
    nfe40_nLacre = fields.Char(string="Número do lacre", required=True)


class NfeDocumentTranspVol(models.Model):
    """Volume transportado."""

    _name = "nfe.document.transp.vol"
    _description = "NF-e Transporte - Volume"

    transp_id = fields.Many2one(
        comodel_name="nfe.document.transp",
        ondelete="cascade",
        required=True,
    )
    nfe40_qVol = fields.Char(string="Quantidade")
    nfe40_esp = fields.Char(string="Espécie")
    nfe40_marca = fields.Char(string="Marca")
    nfe40_nVol = fields.Char(string="Numeração")
    nfe40_pesoL = fields.Float(string="Peso líquido (kg)", digits=(12, 3))
    nfe40_pesoB = fields.Float(string="Peso bruto (kg)", digits=(12, 3))
    lacres_ids = fields.One2many(
        comodel_name="nfe.document.transp.vol.lacres",
        inverse_name="vol_id",
        string="Lacres",
    )

    def _update_document_total_weight(self):
        """Atualiza total_weight do documento fiscal que usa este(s) transporte(s)."""
        transps = self.mapped("transp_id")
        if not transps:
            return
        Doc = self.env["l10n_br_fiscal.document"]
        for transp in transps:
            docs = Doc.search([("nfe40_transp_id", "=", transp.id)])
            for doc in docs:
                if doc.document_type_id and doc.document_type_id.code == "55":
                    total = sum(
                        (v or 0.0) for v in transp.vol_ids.mapped("nfe40_pesoB")
                    )
                    doc.total_weight = total

    @api.model_create_multi
    def create(self, vals_list):
        vols = super().create(vals_list)
        vols._update_document_total_weight()
        return vols

    def write(self, vals):
        res = super().write(vals)
        if "nfe40_pesoB" in vals or "transp_id" in vals:
            self._update_document_total_weight()
        return res

    def unlink(self):
        transps = self.mapped("transp_id")
        res = super().unlink()
        for transp in transps:
            docs = self.env["l10n_br_fiscal.document"].search(
                [("nfe40_transp_id", "=", transp.id)]
            )
            for doc in docs:
                if doc.document_type_id and doc.document_type_id.code == "55":
                    total = sum(
                        (v or 0.0) for v in transp.vol_ids.mapped("nfe40_pesoB")
                    )
                    doc.total_weight = total
        return res


class NfeDocumentTransp(models.Model):
    """Dados de transporte da NF-e."""

    _name = "nfe.document.transp"
    _description = "NF-e Transporte"

    nfe40_modFrete = fields.Selection(
        selection=[
            ("0", "0 - CIF (Remetente)"),
            ("1", "1 - FOB (Destinatário)"),
            ("2", "2 - Terceiros"),
            ("3", "3 - Próprio Remetente"),
            ("4", "4 - Próprio Destinatário"),
            ("9", "9 - Sem transporte"),
        ],
        string="Modalidade do frete",
        default="9",
        required=True,
    )
    transporta_id = fields.Many2one(
        comodel_name="nfe.document.transporta",
        string="Transportadora",
        ondelete="set null",
    )
    veicTransp_id = fields.Many2one(
        comodel_name="nfe.document.veiculo",
        string="Veículo",
        ondelete="set null",
    )
    vol_ids = fields.One2many(
        comodel_name="nfe.document.transp.vol",
        inverse_name="transp_id",
        string="Volumes",
    )
    retTransp_id = fields.Many2one(
        comodel_name="nfe.document.rettransp",
        string="Retenção ICMS",
        ondelete="set null",
    )
