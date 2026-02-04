# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Mixin de construção da estrutura NF-e (ide, emit, dest, det, total, transp, pag).

Responsabilidade única: mapear l10n_br_fiscal.document para objetos nfelib
conforme leiaute NFe 4.0. Depende de nfe.document.mappers.
"""

import logging

from nfelib.nfe.bindings.v4_0.leiaute_nfe_v4_00 import Tendereco, Tnfe
from nfelib.nfe.bindings.v4_0.leiaute_nfe_v4_00 import TenderEmi
from nfelib.nfe.bindings.v4_0.leiaute_nfe_v4_00 import Icmssn102Csosn
from nfelib.nfe.bindings.v4_0.nfe_v4_00 import Nfe

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.l10n_br_fiscal.constants.fiscal import TAX_FRAMEWORK_SIMPLES_ALL
from . import nfe_xml_utils

_logger = logging.getLogger(__name__)


class NFeDocumentBuilders(models.AbstractModel):
    """
    Constrói grupos da NF-e: ide, emit, dest, det (impostos), total, transp, pag.
    """

    _name = "nfe.document.builders"
    _description = "NF-e document builders (fiscal -> nfelib)"
    _inherit = ["nfe.document.mappers"]
    _abstract = True

    # Literal obrigatório para xNome do destinatário em homologação (rejeição 598)
    NFE_HOMOLOG_XNOME_DEST = "NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"

    # CST PIS/COFINS por grupo no XML (doc TecnoSpeed)
    _PIS_COFINS_CST_ALIQ = ("01", "02")
    _PIS_COFINS_CST_QTDE = ("03",)
    _PIS_COFINS_CST_NT = ("04", "05", "06", "07", "08", "09")

    # CSOSN válidos para ICMSSN102 (Simples Nacional sem ST)
    _ICMSSN102_CSOSN = ("102", "103", "300", "400")

    def _build_nfe_ide(self, c_dv=None, c_nf=None):
        """
        Constrói tag <ide> (identificação) da NF-e.
        cDV obrigatório no schema; cNF = código numérico 8 dígitos (aleatório).
        """
        self.ensure_one()
        codigo_uf = self._get_codigo_uf(self.company_id.state_id)
        dh_emi = self.document_date or fields.Datetime.now()
        num_raw = self.document_number
        digits = "".join(filter(str.isdigit, str(num_raw or "1"))) or "1"
        if c_nf and len(c_nf) == 8 and c_nf.isdigit():
            cnf_val = c_nf
        else:
            cnf_val = digits[-8:].zfill(8)
        ind_pres_val = self._map_indpres()
        ide_kw = dict(
            cUF=codigo_uf,
            cNF=cnf_val,
            natOp=nfe_xml_utils.escape_xml_text(self.fiscal_operation_id.name or "Venda"),
            mod="55",
            serie=self._map_serie_nfe(),
            nNF=digits,
            dhEmi=self._format_datetime_nfe(dh_emi),
            dhSaiEnt=self._format_datetime_nfe(dh_emi),
            tpNF=self._map_tpnf_fiscal_operation_type(),
            idDest="1",
            cMunFG="".join(filter(str.isdigit, str(
                self.company_id.city_id.ibge_code or ""
            ))).zfill(7)[:7] or "0000000",
            tpImp="1",
            tpEmis="1",
            cDV=c_dv or "0",
            tpAmb=self._map_tpamb(),
            finNFe=self._map_finnfe(),
            indFinal=self._map_indfinal(),
            indPres=ind_pres_val,
            procEmi="0",
            verProc="l10n_nfe_emissao 1.0",
        )
        if ind_pres_val in ("2", "3", "4", "9"):
            ide_kw["indIntermed"] = "0"
        origin = self.return_origin_document_id
        if (
            origin
            and origin.nfe_key
            and len(origin.nfe_key) == 44
            and self._map_finnfe() == "4"
        ):
            Nfref_cls = Tnfe.InfNfe.Ide.Nfref
            ide_kw["NFref"] = [Nfref_cls(refNFe=origin.nfe_key)]
        return Nfe.InfNfe.Ide(**ide_kw)

    def _build_nfe_emit(self):
        """Constrói tag <emit> (emitente) da NF-e."""
        self.ensure_one()
        company = self.company_id
        cnpj = "".join(filter(str.isdigit, company.cnpj_cpf or ""))
        fone = "".join(filter(str.isdigit, company.phone or "")) or None
        emit = Nfe.InfNfe.Emit(
            CNPJ=cnpj,
            xNome=nfe_xml_utils.escape_xml_text(company.legal_name or company.name),
            xFant=nfe_xml_utils.escape_xml_text(company.name),
            enderEmit=TenderEmi(
                xLgr=nfe_xml_utils.escape_xml_text(company.street or ""),
                nro=getattr(company, "street_number", None) or "SN",
                xCpl=company.street2 or None,
                xBairro=getattr(company, "district", None) or "Centro",
                cMun=company.city_id.ibge_code if company.city_id else "",
                xMun=company.city_id.name if company.city_id else "",
                UF=company.state_id.code if company.state_id else "",
                CEP="".join(filter(str.isdigit, company.zip or "")).zfill(8)[:8],
                cPais="1058",
                xPais="Brasil",
                fone=fone,
            ),
            IE=getattr(company, "inscr_est", None) or "",
            CRT=self._map_crt_emit(),
        )
        return emit

    def _build_nfe_dest(self):
        """Constrói tag <dest> (destinatário). Em homologação xNome = literal SEFAZ (rejeição 598)."""
        self.ensure_one()
        partner = self.partner_id
        cnpj_cpf = partner.cnpj_cpf.replace(".", "").replace("/", "").replace("-", "") if partner.cnpj_cpf else ""
        fone_dest = "".join(filter(str.isdigit, partner.phone or "")) or None
        if self._map_tpamb() == "2":
            x_nome_dest = self.NFE_HOMOLOG_XNOME_DEST
        else:
            x_nome_dest = partner.legal_name or partner.name
        dest_kwargs = {
            "xNome": nfe_xml_utils.escape_xml_text(x_nome_dest),
            "enderDest": Tendereco(
                xLgr=nfe_xml_utils.escape_xml_text(partner.street or ""),
                nro=getattr(partner, "street_number", None) or "SN",
                xCpl=partner.street2 or None,
                xBairro=getattr(partner, "district", None) or "Centro",
                cMun=partner.city_id.ibge_code if partner.city_id else "",
                xMun=partner.city_id.name if partner.city_id else "",
                UF=partner.state_id.code if partner.state_id else "",
                CEP="".join(filter(str.isdigit, partner.zip or "")).zfill(8)[:8],
                cPais="1058",
                xPais="Brasil",
                fone=fone_dest,
            ),
            "indIEDest": self._map_indiedest_dest(),
            "email": partner.email or None,
        }
        if partner.company_type == "company":
            dest_kwargs["CNPJ"] = cnpj_cpf
            dest_kwargs["IE"] = partner.inscr_est or None
        else:
            dest_kwargs["CPF"] = cnpj_cpf
        return Nfe.InfNfe.Dest(**dest_kwargs)

    def _build_pis_cofins_for_line(self, line):
        """
        Constrói objetos PIS e COFINS a partir da configuração da linha fiscal.
        CST: 01-02 Aliq; 03 Qtde; 04-09 NT; demais Outr.
        """
        pis_cst = (line.pis_cst_id.code or "07").strip() if line.pis_cst_id else "07"
        cofins_cst = (line.cofins_cst_id.code or "07").strip() if line.cofins_cst_id else "07"
        v_bc_pis = float(getattr(line, "pis_base", 0) or 0)
        p_pis = float(getattr(line, "pis_percent", 0) or 0)
        v_pis = float(getattr(line, "pis_value", 0) or 0)
        v_bc_cofins = float(getattr(line, "cofins_base", 0) or 0)
        p_cofins = float(getattr(line, "cofins_percent", 0) or 0)
        v_cofins = float(getattr(line, "cofins_value", 0) or 0)
        pis_obj = None
        cofins_obj = None
        imposto_cls = Nfe.InfNfe.Det.Imposto
        pis_cls = getattr(imposto_cls, "Pis", None) or getattr(imposto_cls, "pis", None)
        cofins_cls = getattr(imposto_cls, "Cofins", None) or getattr(imposto_cls, "cofins", None)

        if pis_cls is not None:
            if pis_cst in self._PIS_COFINS_CST_ALIQ:
                pis_aliq_cls = (
                    getattr(pis_cls, "PISAliq", None) or getattr(pis_cls, "PisAliq", None)
                    or getattr(pis_cls, "Pisaliq", None) or getattr(pis_cls, "pis_aliq", None)
                )
                if pis_aliq_cls is not None:
                    try:
                        pis_aliq = pis_aliq_cls(CST=pis_cst, vBC=f"{v_bc_pis:.2f}", pPIS=f"{p_pis:.2f}", vPIS=f"{v_pis:.2f}")
                        pis_obj = pis_cls(PISAliq=pis_aliq)
                    except Exception as e:
                        _logger.warning("PIS Aliq (CST %s) não construído: %s", pis_cst, e, exc_info=True)
                        try:
                            pis_aliq = pis_aliq_cls(CST=pis_cst, vBC=f"{v_bc_pis:.2f}", pPIS=f"{p_pis:.2f}", vPIS=f"{v_pis:.2f}")
                            pis_obj = pis_cls(PISAliq=pis_aliq)
                        except Exception as e2:
                            _logger.warning("PIS Aliq fallback (CST %s) não construído: %s", pis_cst, e2, exc_info=True)
            elif pis_cst in self._PIS_COFINS_CST_QTDE:
                pis_qtde_cls = (
                    getattr(pis_cls, "PISQtde", None) or getattr(pis_cls, "PisQtde", None)
                    or getattr(pis_cls, "Pisqtde", None) or getattr(pis_cls, "pis_qtde", None)
                )
                if pis_qtde_cls is not None:
                    try:
                        pis_qtde = pis_qtde_cls(CST=pis_cst, qBCProd=f"{v_bc_pis:.4f}", vAliqProd=f"{p_pis:.4f}", vPIS=f"{v_pis:.2f}")
                        pis_obj = pis_cls(PISQtde=pis_qtde)
                    except Exception as e:
                        _logger.warning("PIS Qtde (CST %s) não construído: %s", pis_cst, e, exc_info=True)
                        try:
                            pis_qtde = pis_qtde_cls(CST=pis_cst, qBCProd=f"{v_bc_pis:.4f}", vAliqProd=f"{p_pis:.4f}", vPIS=f"{v_pis:.2f}")
                            pis_obj = pis_cls(PISQtde=pis_qtde)
                        except Exception as e2:
                            _logger.warning("PIS Qtde fallback (CST %s) não construído: %s", pis_cst, e2, exc_info=True)
            elif pis_cst in self._PIS_COFINS_CST_NT:
                pis_nt_cls = (
                    getattr(pis_cls, "PISNT", None) or getattr(pis_cls, "PisNt", None)
                    or getattr(pis_cls, "Pisnt", None) or getattr(pis_cls, "pis_nt", None)
                )
                if pis_nt_cls is not None:
                    try:
                        pis_nt = pis_nt_cls(CST=pis_cst)
                        pis_obj = pis_cls(PISNT=pis_nt)
                    except Exception as e:
                        _logger.warning("PIS NT (CST %s) não construído: %s", pis_cst, e, exc_info=True)
                        try:
                            pis_nt = pis_nt_cls(CST=pis_cst)
                            pis_obj = pis_cls(PISNT=pis_nt)
                        except Exception as e2:
                            _logger.warning("PIS NT fallback (CST %s) não construído: %s", pis_cst, e2, exc_info=True)
            else:
                pis_outr_cls = (
                    getattr(pis_cls, "PISOutr", None) or getattr(pis_cls, "PisOutr", None)
                    or getattr(pis_cls, "Pisoutr", None) or getattr(pis_cls, "pis_outr", None)
                )
                if pis_outr_cls is not None:
                    try:
                        pis_outr = pis_outr_cls(CST=pis_cst, vBC=f"{v_bc_pis:.2f}", pPIS=f"{p_pis:.2f}", vPIS=f"{v_pis:.2f}")
                        pis_obj = pis_cls(PISOutr=pis_outr)
                    except Exception as e:
                        _logger.warning("PIS Outr (CST %s) não construído: %s", pis_cst, e, exc_info=True)
                        try:
                            pis_outr = pis_outr_cls(CST=pis_cst, vBC=f"{v_bc_pis:.2f}", pPIS=f"{p_pis:.2f}", vPIS=f"{v_pis:.2f}")
                            pis_obj = pis_cls(PISOutr=pis_outr)
                        except Exception as e2:
                            _logger.warning("PIS Outr fallback (CST %s) não construído: %s", pis_cst, e2, exc_info=True)
            if pis_obj is None and pis_cst in self._PIS_COFINS_CST_NT:
                pis_nt_cls = getattr(pis_cls, "PISNT", None) or getattr(pis_cls, "PisNt", None)
                if pis_nt_cls:
                    try:
                        pis_nt = pis_nt_cls(CST=pis_cst)
                        pis_obj = pis_cls(PISNT=pis_nt)
                    except Exception:
                        pass
        else:
            _logger.warning("Classe PIS não encontrada no binding nfelib.")

        if cofins_cls is not None:
            if cofins_cst in self._PIS_COFINS_CST_ALIQ:
                cofins_aliq_cls = (
                    getattr(cofins_cls, "COFINSAliq", None) or getattr(cofins_cls, "CofinsAliq", None)
                    or getattr(cofins_cls, "Cofinsaliq", None) or getattr(cofins_cls, "cofins_aliq", None)
                )
                if cofins_aliq_cls is not None:
                    try:
                        cofins_aliq = cofins_aliq_cls(CST=cofins_cst, vBC=f"{v_bc_cofins:.2f}", pCOFINS=f"{p_cofins:.2f}", vCOFINS=f"{v_cofins:.2f}")
                        cofins_obj = cofins_cls(COFINSAliq=cofins_aliq)
                    except Exception as e:
                        _logger.warning("COFINS Aliq (CST %s) não construído: %s", cofins_cst, e, exc_info=True)
                        try:
                            cofins_aliq = cofins_aliq_cls(CST=cofins_cst, vBC=f"{v_bc_cofins:.2f}", pCOFINS=f"{p_cofins:.2f}", vCOFINS=f"{v_cofins:.2f}")
                            cofins_obj = cofins_cls(COFINSAliq=cofins_aliq)
                        except Exception as e2:
                            _logger.warning("COFINS Aliq fallback (CST %s) não construído: %s", cofins_cst, e2, exc_info=True)
            elif cofins_cst in self._PIS_COFINS_CST_QTDE:
                cofins_qtde_cls = (
                    getattr(cofins_cls, "COFINSQtde", None) or getattr(cofins_cls, "CofinsQtde", None)
                    or getattr(cofins_cls, "Cofinsqtde", None) or getattr(cofins_cls, "cofins_qtde", None)
                )
                if cofins_qtde_cls is not None:
                    try:
                        cofins_qtde = cofins_qtde_cls(CST=cofins_cst, qBCProd=f"{v_bc_cofins:.4f}", vAliqProd=f"{p_cofins:.4f}", vCOFINS=f"{v_cofins:.2f}")
                        cofins_obj = cofins_cls(COFINSQtde=cofins_qtde)
                    except Exception as e:
                        _logger.warning("COFINS Qtde (CST %s) não construído: %s", cofins_cst, e, exc_info=True)
                        try:
                            cofins_qtde = cofins_qtde_cls(CST=cofins_cst, qBCProd=f"{v_bc_cofins:.4f}", vAliqProd=f"{p_cofins:.4f}", vCOFINS=f"{v_cofins:.2f}")
                            cofins_obj = cofins_cls(COFINSQtde=cofins_qtde)
                        except Exception as e2:
                            _logger.warning("COFINS Qtde fallback (CST %s) não construído: %s", cofins_cst, e2, exc_info=True)
            elif cofins_cst in self._PIS_COFINS_CST_NT:
                cofins_nt_cls = (
                    getattr(cofins_cls, "COFINSNT", None) or getattr(cofins_cls, "CofinsNT", None)
                    or getattr(cofins_cls, "Cofinsnt", None) or getattr(cofins_cls, "cofins_nt", None)
                )
                if cofins_nt_cls is not None:
                    try:
                        cofins_nt = cofins_nt_cls(CST=cofins_cst)
                        cofins_obj = cofins_cls(COFINSNT=cofins_nt)
                    except Exception as e:
                        _logger.warning("COFINS NT (CST %s) não construído: %s", cofins_cst, e, exc_info=True)
                        try:
                            cofins_nt = cofins_nt_cls(CST=cofins_cst)
                            cofins_obj = cofins_cls(COFINSNT=cofins_nt)
                        except Exception as e2:
                            _logger.warning("COFINS NT fallback (CST %s) não construído: %s", cofins_cst, e2, exc_info=True)
            else:
                cofins_outr_cls = (
                    getattr(cofins_cls, "CofinsOutr", None) or getattr(cofins_cls, "Cofinsoutr", None)
                    or getattr(cofins_cls, "cofins_outr", None)
                )
                if cofins_outr_cls is not None:
                    try:
                        cofins_outr = cofins_outr_cls(CST=cofins_cst, vBC=f"{v_bc_cofins:.2f}", pCOFINS=f"{p_cofins:.2f}", vCOFINS=f"{v_cofins:.2f}")
                        cofins_obj = cofins_cls(COFINSOutr=cofins_outr)
                    except Exception as e:
                        _logger.warning("COFINS Outr (CST %s) não construído: %s", cofins_cst, e, exc_info=True)
                        try:
                            cofins_outr = cofins_outr_cls(CST=cofins_cst, vBC=f"{v_bc_cofins:.2f}", pCOFINS=f"{p_cofins:.2f}", vCOFINS=f"{v_cofins:.2f}")
                            cofins_obj = cofins_cls(COFINSOutr=cofins_outr)
                        except Exception as e2:
                            _logger.warning("COFINS Outr fallback (CST %s) não construído: %s", cofins_cst, e2, exc_info=True)
            if cofins_obj is None and cofins_cst in self._PIS_COFINS_CST_NT:
                cofins_nt_cls = getattr(cofins_cls, "COFINSNT", None) or getattr(cofins_cls, "CofinsNT", None) or getattr(cofins_cls, "Cofinsnt", None)
                if cofins_nt_cls:
                    try:
                        cofins_nt = cofins_nt_cls(CST=cofins_cst)
                        cofins_obj = cofins_cls(COFINSNT=cofins_nt)
                    except Exception:
                        pass
        else:
            _logger.warning("Classe COFINS não encontrada no binding nfelib.")
        return pis_obj, cofins_obj

    def _build_icms_for_line(self, line):
        """Constrói grupo ICMS do item para Simples Nacional (ICMSSN102)."""
        self.ensure_one()
        tax_framework = getattr(self.company_id, "tax_framework", None) or "0"
        if tax_framework not in TAX_FRAMEWORK_SIMPLES_ALL:
            return None
        Icms_cls = Tnfe.InfNfe.Det.Imposto.Icms
        Icmssn102_cls = Icms_cls.Icmssn102
        csosn_code = (line.icms_cst_id.code or "102").strip() if line.icms_cst_id else "102"
        if csosn_code not in self._ICMSSN102_CSOSN:
            csosn_code = "102"
        orig = str(getattr(line, "icms_origin", "0") or "0").strip()
        try:
            csosn_enum = getattr(Icmssn102Csosn, f"VALUE_{csosn_code}", Icmssn102Csosn.VALUE_102)
        except AttributeError:
            csosn_enum = Icmssn102Csosn.VALUE_102
        icmssn102 = Icmssn102_cls(orig=orig, CSOSN=csosn_enum)
        return Icms_cls(ICMSSN102=icmssn102)

    def _set_imposto_pis_cofins(self, imposto, pis_obj, cofins_obj):
        """Atribui PIS e COFINS no objeto Imposto respeitando nomes do binding."""
        if pis_obj is not None:
            if hasattr(imposto, "PIS"):
                setattr(imposto, "PIS", pis_obj)
            elif hasattr(imposto, "pis"):
                setattr(imposto, "pis", pis_obj)
        if cofins_obj is not None:
            if hasattr(imposto, "COFINS"):
                setattr(imposto, "COFINS", cofins_obj)
            elif hasattr(imposto, "cofins"):
                setattr(imposto, "cofins", cofins_obj)

    def _build_nfe_items(self):
        """Constrói tags <det> (itens) da NF-e."""
        self.ensure_one()
        det_list = []
        for idx, line in enumerate(self.fiscal_line_ids, start=1):
            prod = Nfe.InfNfe.Det.Prod(
                cProd=line.product_id.default_code or str(line.product_id.id),
                cEAN="SEM GTIN" if not line.product_id.barcode else line.product_id.barcode,
                xProd=nfe_xml_utils.escape_xml_text(line.name or line.product_id.name),
                NCM="".join(filter(str.isdigit, (line.ncm_id.code or "00000000"))).zfill(8)[:8] or "00000000",
                CFOP=line.cfop_id.code if line.cfop_id else "5102",
                uCom=self._map_uom_nfe(line.uom_id.name),
                qCom=str(line.quantity),
                vUnCom=f"{line.price_unit:.10f}",
                vProd=f"{line.price_gross:.2f}",
                cEANTrib="SEM GTIN" if not line.product_id.barcode else line.product_id.barcode,
                uTrib=self._map_uom_nfe(line.uom_id.name),
                qTrib=str(line.quantity),
                vUnTrib=f"{line.price_unit:.10f}",
                indTot="1",
            )
            icms_obj = self._build_icms_for_line(line)
            imposto_kw = {"vTotTrib": f"{line.estimate_tax:.2f}" if line.estimate_tax else "0.00"}
            if icms_obj is not None:
                imposto_kw["ICMS"] = icms_obj
            imposto = Nfe.InfNfe.Det.Imposto(**imposto_kw)
            pis_obj, cofins_obj = self._build_pis_cofins_for_line(line)
            if pis_obj is None:
                raise ValidationError(
                    _(
                        "Linha %s sem PIS definido. Configure PIS na aba de impostos do item. "
                        "CST=%s, base=%s, aliquota=%s, valor=%s."
                    )
                    % (idx, line.pis_cst_id.code if line.pis_cst_id else "N/A", line.pis_base, line.pis_percent, line.pis_value)
                )
            if cofins_obj is None:
                raise ValidationError(
                    _(
                        "Linha %s sem COFINS definido. Configure COFINS na aba de impostos do item. "
                        "CST=%s, base=%s, aliquota=%s, valor=%s."
                    )
                    % (idx, line.cofins_cst_id.code if line.cofins_cst_id else "N/A", line.cofins_base, line.cofins_percent, line.cofins_value)
                )
            self._set_imposto_pis_cofins(imposto, pis_obj, cofins_obj)
            det = Nfe.InfNfe.Det(nItem=str(idx), prod=prod, imposto=imposto)
            det_list.append(det)
        return det_list

    def _build_nfe_total(self):
        """Constrói tag <total> (totalizadores) da NF-e."""
        self.ensure_one()
        def _val(name, default=0.0):
            return float(getattr(self, name, default) or default)
        total = Nfe.InfNfe.Total(
            ICMSTot=Nfe.InfNfe.Total.Icmstot(
                vBC=f"{_val('amount_icms_base'):.2f}",
                vICMS=f"{_val('amount_icms_value'):.2f}",
                vICMSDeson="0.00", vFCP="0.00", vBCST="0.00", vST="0.00",
                vFCPST="0.00", vFCPSTRet="0.00",
                vProd=f"{_val('amount_price_gross'):.2f}",
                vFrete=f"{_val('amount_freight_value'):.2f}",
                vSeg=f"{_val('amount_insurance_value'):.2f}",
                vDesc=f"{_val('amount_discount_value'):.2f}",
                vII="0.00", vIPI=f"{_val('amount_ipi_value'):.2f}", vIPIDevol="0.00",
                vPIS=f"{_val('amount_pis_value'):.2f}",
                vCOFINS=f"{_val('amount_cofins_value'):.2f}",
                vOutro=f"{_val('amount_other_value'):.2f}",
                vNF=f"{_val('fiscal_amount_total'):.2f}",
                vTotTrib=f"{_val('amount_estimate_tax'):.2f}",
            )
        )
        return total

    def _build_nfe_transp(self):
        """Constrói tag <transp>. modFrete 9 = Sem ocorrência de transporte."""
        return Nfe.InfNfe.Transp(modFrete="9")

    def _build_nfe_pag(self):
        """Constrói tag <pag>. detPag obrigatório: tPag 99=Outros, vPag=total."""
        total_nf = float(getattr(self, "fiscal_amount_total", 0) or 0)
        det_pag = Nfe.InfNfe.Pag.DetPag(
            tPag="99", xPag="Pagamento à vista", vPag=f"{total_nf:.2f}",
        )
        return Nfe.InfNfe.Pag(detPag=[det_pag])
