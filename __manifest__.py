# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

{
    "name": "Emissão de Nota Fiscal Eletrônica (NF-e)",
    "summary": "Módulo customizado para emissão de NF-e no Brasil baseado em l10n-br",
    "category": "Localisation/Brazil",
    "license": "AGPL-3",
    "author": "Custom",
    "website": "",
    "version": "18.0.1.0.0",
    "depends": [
        "l10n_br_base",
        "l10n_br_fiscal",
        "l10n_br_nfe_spec",
        "l10n_br_fiscal_certificate",  # Gerenciamento de certificado A1
        "l10n_br_fiscal_dfe",  # Consulta distribuição DFe (notas recebidas)
        "l10n_br_fiscal_edi",  # EDI: eventos, PDF, CCe, Cancelamento
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/nfe_sequence_data.xml",
        "report/report_nfe_danfe.xml",
        "views/res_company_view.xml",
        "views/fiscal_document_view.xml",
        "views/fiscal_document_edi_view.xml",
        "views/l10n_nfe_emissao_menus.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "installable": True,
    "application": False,
    "development_status": "Beta",
    "external_dependencies": {
        "python": [
            "erpbrasil.base",
            "brazilfiscalreport",  # DANFE layout padrão nacional
            "pypdf",  # Merge PDF DANFE + DACCe (ou PyPDF2)
        ],
    },
}
