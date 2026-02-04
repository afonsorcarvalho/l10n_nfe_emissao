# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Mixin de mapeamento: documento fiscal -> valores do schema NF-e.

Responsabilidade única: conversão de campos do l10n_br_fiscal (datas, CST,
tpNF, finNFe, UF, etc.) para os valores esperados pelo leiaute NFe 4.0.
"""

from datetime import datetime

import pytz

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class NFeDocumentMappers(models.AbstractModel):
    """
    Mapeadores documento fiscal -> schema NF-e (ide, emit, dest, etc.).
    """

    _name = "nfe.document.mappers"
    _description = "NF-e document mappers (fiscal -> NFe schema)"
    _abstract = True

    def _utc_to_brazil(self, dt):
        """
        Converte datetime UTC (Odoo) para America/Sao_Paulo (UTC-3).

        O Odoo armazena datetimes em UTC (naive). A SEFAZ exige horário Brasil
        em dhEmi/dhSaiEnt (evita rejeição 703).
        """
        if dt is None:
            dt = fields.Datetime.now()
        if isinstance(dt, str):
            dt = datetime.strptime(dt[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        tz_utc = pytz.utc
        tz_br = pytz.timezone("America/Sao_Paulo")
        if dt.tzinfo is None:
            dt_utc = tz_utc.localize(dt)
        else:
            dt_utc = dt.astimezone(tz_utc)
        return dt_utc.astimezone(tz_br)

    def _format_datetime_nfe(self, dt):
        """
        Formata datetime para TDateTimeUTC do schema NF-e.

        Converte UTC (Odoo) para America/Sao_Paulo antes de formatar.
        Padrão: YYYY-MM-DDTHH:MM:SS-03:00 (Brasil, UTC-3).
        """
        dt_br = self._utc_to_brazil(dt)
        return dt_br.strftime("%Y-%m-%dT%H:%M:%S") + "-03:00"

    def _format_date_nfe(self, dt):
        """
        Formata data para padrão NF-e (apenas data, sem hora).
        Padrão: YYYY-MM-DD. Usado em dPag e similares.
        """
        if dt is None:
            dt = datetime.now().date()
        if isinstance(dt, str):
            dt = datetime.strptime(dt[:10], "%Y-%m-%d").date()
        return dt.strftime("%Y-%m-%d")

    def _map_tpnf_fiscal_operation_type(self):
        """tpNF: 0=Entrada, 1=Saída (Enumeration no schema)."""
        t = self.fiscal_operation_type or "out"
        return "0" if t == "in" else "1"

    def _map_finnfe(self):
        """finNFe: 1=Normal, 2=Complementar, 3=Ajuste, 4=Devolução."""
        val = getattr(self, "edoc_purpose", None) or "1"
        return val if val in ("1", "2", "3", "4") else "1"

    def _map_indfinal(self):
        """indFinal: 0=Não, 1=Consumidor final (Enumeration)."""
        val = getattr(self, "ind_final", None) or "1"
        return "0" if val == "0" else "1"

    def _map_indpres(self):
        """indPres: 0-5, 9 (Enumeration). Usar ind_pres do documento."""
        val = getattr(self, "ind_pres", None) or "1"
        valid = ("0", "1", "2", "3", "4", "5", "9")
        return val if val in valid else "1"

    def _map_tpamb(self):
        """tpAmb: 1=Produção, 2=Homologação (Enumeration)."""
        val = getattr(self.company_id, "nfe_environment", None) or "2"
        return "1" if str(val) == "1" else "2"

    def _map_crt_emit(self):
        """CRT (Regime tributário): 1=Simples, 2=Simples Excesso, 3=Normal (Enumeration)."""
        val = getattr(self.company_id, "tax_framework", None) or "3"
        return val if str(val) in ("1", "2", "3") else "3"

    def _map_serie_nfe(self):
        """Série NF-e: 0-889 normal, 900-999 contingência. Apenas dígitos."""
        raw = str(self.document_serie_id.code or "1") if self.document_serie_id else "1"
        digits = "".join(filter(str.isdigit, raw))
        num = int(digits or "1")
        return str(min(max(num, 0), 999))

    def _map_uom_nfe(self, uom_name):
        """
        Mapeia nome da unidade de medida para uCom/uTrib (max 6 caracteres).
        """
        if not uom_name or not str(uom_name).strip():
            return "UN"
        name = str(uom_name).strip()
        mapa = {
            "unidades": "UN", "unit": "UN", "unités": "UN", "pieces": "UN",
            "pecas": "UN", "caixa": "CX", "caixas": "CX", "kg": "KG",
            "kilograma": "KG", "kilogramas": "KG", "litro": "LT", "litros": "LT",
            "l": "LT", "metro": "M", "metros": "M", "m": "M", "par": "PAR",
            "pares": "PAR", "pacote": "PC", "pacotes": "PC",
        }
        key = name.lower()
        return mapa.get(key, (name[:6] or "UN"))

    def _map_indiedest_dest(self):
        """indIEDest: 1=Contribuinte, 2=Isento, 9=Não contribuinte (Enumeration)."""
        partner = self.partner_id
        if getattr(partner, "inscr_est", None) and partner.inscr_est:
            return "1"
        ind = getattr(partner, "ind_ie_dest", None) or "9"
        return ind if ind in ("1", "2", "9") else "9"

    def _get_codigo_uf(self, state):
        """
        Retorna o código IBGE da UF (2 dígitos) para a NF-e.
        Usa state.ibge_code (l10n_br_base) quando existir; senão mapeamento sigla -> código.
        """
        if not state:
            raise ValidationError("Estado da empresa não definido.")
        if getattr(state, "ibge_code", None):
            return str(state.ibge_code).zfill(2)
        uf_para_ibge = {
            "AC": "12", "AL": "27", "AP": "16", "AM": "13", "BA": "29", "CE": "23",
            "DF": "53", "ES": "32", "GO": "52", "MA": "21", "MT": "51", "MS": "50",
            "MG": "31", "PA": "15", "PB": "25", "PR": "41", "PE": "26", "PI": "22",
            "RJ": "33", "RN": "24", "RS": "43", "RO": "11", "RR": "14", "SC": "42",
            "SP": "35", "SE": "28", "TO": "17",
        }
        code = (state.code or "").upper().strip()
        if code in uf_para_ibge:
            return uf_para_ibge[code]
        raise ValidationError(
            _("Estado %s sem código IBGE. Configure o estado na empresa ou use l10n_br_base.")
            % (state.name or code)
        )
