# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Helper para obter certificado digital para assinatura de NF-e.

O certificado é associado à empresa em res.company (certificate_nfe_id ou
certificate_ecnpj_id). O modelo l10n_br_fiscal.certificate não possui company_id.
"""

import base64

from odoo import api, models
from odoo.exceptions import UserError


class NfeCertificateHelper(models.AbstractModel):
    """Helper para acesso ao certificado digital."""

    _name = "l10n_nfe_emissao.certificate.helper"
    _description = "NF-e Certificate Helper"

    @api.model
    def get_certificate_data(self, company_id):
        """
        Obtém dados do certificado digital (PKCS#12) da empresa.

        O certificado é vinculado à empresa em res.company (certificate_nfe_id
        ou certificate_ecnpj_id), não possui company_id no modelo certificate.

        :param company_id: ID da empresa
        :return: tuple (pkcs12_data, senha) ou raise se não encontrado
        """
        company = self.env["res.company"].browse(company_id)
        if not company.exists():
            raise UserError("Empresa não encontrada.")

        # Certificado NF-e tem prioridade; senão usa e-CNPJ
        certificate = company.sudo().certificate_nfe_id or company.sudo().certificate_ecnpj_id
        if not certificate:
            raise UserError(
                "Certificado digital A1 não configurado para esta empresa.\n\n"
                "Em Fiscal > Configuração > Certificados, crie um certificado tipo NF-e ou E-CNPJ "
                "e associe-o à empresa em Configurações > Empresas > sua empresa (aba Certificates)."
            )

        if not certificate.is_valid:
            raise UserError(
                "O certificado configurado está vencido ou inválido. "
                "Atualize o certificado em Fiscal > Configuração > Certificados."
            )

        if not certificate.file:
            raise UserError("Arquivo de certificado (.pfx) não anexado ao certificado.")

        # Campo Binary no Odoo retorna base64; erpbrasil.assinatura espera bytes do .pfx
        pfx_data = certificate.file
        if pfx_data:
            pfx_data = base64.b64decode(pfx_data)
        password = certificate.password or ""

        return pfx_data, password
