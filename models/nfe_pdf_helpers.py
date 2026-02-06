# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Utilitários para geração de PDF auxiliares (DACCe fallback e banner NF-e cancelada).

Isolam uso de FPDF; recebem documento (record) e dados para montar o PDF.
"""

import logging

_logger = logging.getLogger(__name__)

# Fuso padrão para exibição de datas de evento SEFAZ (dhRegEvento vem em -03:00)
_DEFAULT_TZ_DISPLAY = "America/Sao_Paulo"


def _format_protocol_date_local(prot_date, document):
    """
    Formata a data do protocolo (armazenada em UTC no banco) para o fuso da empresa.
    dhRegEvento na SEFAZ vem ex.: 2026-02-05T22:10:46-03:00; no banco fica UTC naive (01:10:46 do dia 06).
    Retorna string DD/MM/YYYY HH:MM:SS no fuso America/Sao_Paulo (ou company.tz).
    Usa zoneinfo (stdlib) para não depender de pytz no container.
    """
    if not prot_date:
        return ""
    from datetime import datetime

    # Normalizar para datetime (naive UTC quando vindo do banco; string no formato Odoo ou ISO)
    dt = prot_date
    if isinstance(dt, str):
        s = (dt or "").strip()[:26]
        try:
            # Formato Odoo: "2026-02-06 01:32:36" ou ISO "2026-02-06T01:32:36"
            dt = datetime.strptime(s[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                from dateutil.parser import parse as dateutil_parse
                dt = dateutil_parse(s.replace("Z", "+00:00"))
            except Exception:
                _logger.warning("_format_protocol_date_local: não foi possível interpretar data %r", prot_date)
                return str(prot_date)[:25]

    tz_display = _DEFAULT_TZ_DISPLAY
    company = getattr(document, "company_id", None)
    if company:
        # res.company pode não ter campo 'tz' (depende do módulo de localização); usar getattr
        tz_name = (getattr(company, "tz", None) or "").strip()
        if tz_name:
            tz_display = tz_name

    try:
        from zoneinfo import ZoneInfo
        utc = ZoneInfo("UTC")
        local_tz = ZoneInfo(tz_display)
    except Exception:
        try:
            import pytz
            utc = pytz.utc
            local_tz = pytz.timezone(tz_display)
        except Exception as e:
            _logger.warning("_format_protocol_date_local: zoneinfo/pytz indisponível (%s), exibindo UTC", e)
            if hasattr(prot_date, "strftime"):
                return prot_date.strftime("%d/%m/%Y %H:%M:%S")
            return str(prot_date)[:25]

    try:
        if getattr(dt, "tzinfo", None) is None:
            dt_utc = dt.replace(tzinfo=utc)
        else:
            dt_utc = dt.astimezone(utc)
        dt_local = dt_utc.astimezone(local_tz)
        return dt_local.strftime("%d/%m/%Y %H:%M:%S")
    except Exception as e:
        _logger.warning("_format_protocol_date_local: conversão de fuso falhou (%s), exibindo valor original", e)
        if hasattr(prot_date, "strftime"):
            return prot_date.strftime("%d/%m/%Y %H:%M:%S")
        return str(prot_date)[:25]


def build_cce_fallback_pdf(document, x_correcao, n_prot=None):
    """
    Gera PDF da CCe quando BrazilFiscalReport DaCCe falha.

    Layout harmonizado com DANFE: seções com bordas, títulos em destaque,
    chave formatada, estrutura similar ao documento auxiliar da NF-e.
    """
    try:
        from fpdf import FPDF  # fpdf2 (dep do BrazilFiscalReport)

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font("Helvetica", "", 9)
        w_full = 190
        h_cell = 6
        company = document.company_id
        emitente = (company.legal_name or company.name or "-")[:70]
        chave_raw = (document.nfe_key or "").replace(" ", "")
        if len(chave_raw) == 44:
            chave = (
                f"{chave_raw[0:4]} {chave_raw[4:8]} {chave_raw[8:12]} {chave_raw[12:16]} "
                f"{chave_raw[16:20]} {chave_raw[20:24]} {chave_raw[24:28]} {chave_raw[28:32]} "
                f"{chave_raw[32:36]} {chave_raw[36:40]} {chave_raw[40:44]}"
            )
        else:
            chave = document.nfe_key or "-"
        numero = str(document.document_number or "-")
        serie = (document.document_serie_id.code or "1").strip()
        cnpj_cpf = getattr(company, "cnpj_cpf", None) or getattr(company, "vat", "") or "-"
        if cnpj_cpf and len(str(cnpj_cpf)) >= 11:
            cnpj_fmt = str(cnpj_cpf).replace(".", "").replace("/", "").replace("-", "")
            if len(cnpj_fmt) == 14:
                cnpj_cpf = f"{cnpj_fmt[0:2]}.{cnpj_fmt[2:5]}.{cnpj_fmt[5:8]}/{cnpj_fmt[8:12]}-{cnpj_fmt[12:14]}"
            elif len(cnpj_fmt) == 11:
                cnpj_cpf = f"{cnpj_fmt[0:3]}.{cnpj_fmt[3:6]}.{cnpj_fmt[6:9]}-{cnpj_fmt[9:11]}"

        # --- Bloco identificação (estilo DANFE) ---
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(95, h_cell + 2, "DACCe", border=1, new_x="RIGHT", new_y="TOP")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(95, h_cell + 2, "DOCUMENTO AUXILIAR DA CARTA DE CORRECAO ELETRONICA", border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        num_fmt = numero.zfill(9)
        if len(num_fmt) >= 9:
            num_fmt = f"{num_fmt[0:3]}.{num_fmt[3:6]}.{num_fmt[6:9]}"
        pdf.cell(95, h_cell, f"N° {num_fmt}", border=1, new_x="RIGHT", new_y="TOP")
        pdf.cell(95, h_cell, f"SERIE {serie}", border=1, new_x="LMARGIN", new_y="NEXT")
        # Protocolo de autorização (extraído do infEvento da CCe)
        if n_prot:
            pdf.cell(95, h_cell, "PROTOCOLO DE AUTORIZACAO DE USO", border=1, new_x="RIGHT", new_y="TOP")
            pdf.cell(95, h_cell, str(n_prot).strip()[:25], border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        # --- EMITENTE (estilo DESTINATARIO/REMETENTE) ---
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(w_full, h_cell, "EMITENTE", border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(50, h_cell, "NOME / RAZAO SOCIAL", border=1, new_x="RIGHT", new_y="TOP")
        pdf.cell(140, h_cell, emitente, border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.cell(50, h_cell, "CNPJ / CPF", border=1, new_x="RIGHT", new_y="TOP")
        pdf.cell(140, h_cell, str(cnpj_cpf), border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # --- CHAVE DE ACESSO ---
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(w_full, h_cell, "CHAVE DE ACESSO DA NF-e", border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(w_full, 5, chave if len(chave) <= 80 else chave[:77] + "...", border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # --- CORRECAO ---
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(w_full, h_cell, "TEXTO DA CORRECAO", border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        txt = (x_correcao or "(sem texto)")[:2000]
        pdf.multi_cell(w_full, 5, txt, border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(
            w_full,
            5,
            "A Carta de Correcao e disciplinada pelo par. 1o-A do art. 7o do Convenio S/N, de 15/12/1970. "
            "Consulta: www.nfe.fazenda.gov.br/portal",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        out = pdf.output()
        # fpdf2 output() retorna bytearray ou bytes
        if isinstance(out, bytes):
            return out
        if isinstance(out, bytearray):
            return bytes(out)
        return str(out).encode("latin-1")
    except Exception as e:
        _logger.warning("[DANFE] Erro ao gerar PDF fallback CCe: %s", e)
        return None


def build_cancelada_banner_pdf(document):
    """
    Gera PDF de uma página com aviso "NF-e CANCELADA" para anexar ao DANFE.

    Layout em harmonia com DANFE/DACCe: bordas, título em destaque.
    Usado quando state_edoc == cancelada; o procNFe não é alterado (cancelamento
    é apenas evento 110111 na SEFAZ, como a Carta de Correção).
    """
    try:
        from fpdf import FPDF

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font("Helvetica", "", 9)
        w_full = 190
        h_cell = 6
        company = document.company_id
        emitente = (company.legal_name or company.name or "-")[:70]
        chave_raw = (document.nfe_key or "").replace(" ", "")
        if len(chave_raw) == 44:
            chave = (
                f"{chave_raw[0:4]} {chave_raw[4:8]} {chave_raw[8:12]} {chave_raw[12:16]} "
                f"{chave_raw[16:20]} {chave_raw[20:24]} {chave_raw[24:28]} {chave_raw[28:32]} "
                f"{chave_raw[32:36]} {chave_raw[36:40]} {chave_raw[40:44]}"
            )
        else:
            chave = document.nfe_key or "-"
        numero = str(document.document_number or "-")
        serie = (document.document_serie_id.code or "1").strip()

        # Título em destaque: NF-e CANCELADA
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_fill_color(220, 220, 220)
        pdf.cell(w_full, 14, "NF-e CANCELADA", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(w_full, h_cell, "Esta Nota Fiscal Eletronica foi cancelada na SEFAZ.", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # Dados da nota (estilo DANFE)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(40, h_cell, "Emitente:", border=1, new_x="RIGHT", new_y="TOP")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(w_full - 40, h_cell, emitente[:80], border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(40, h_cell, "Numero / Serie:", border=1, new_x="RIGHT", new_y="TOP")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(w_full - 40, h_cell, f"{numero} / {serie}", border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(40, h_cell, "Chave de acesso:", border=1, new_x="RIGHT", new_y="TOP")
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(w_full - 40, h_cell, chave, border=1, new_x="LMARGIN", new_y="NEXT")
        # Protocolo do evento de cancelamento (retEvento/infEvento: nProt, dhRegEvento)
        cancel_event = getattr(document, "cancel_event_id", None)
        if cancel_event:
            prot_num = (cancel_event.protocol_number or "").strip()
            prot_date = cancel_event.protocol_date
            if prot_num or prot_date:
                pdf.ln(2)
                if prot_num:
                    pdf.set_font("Helvetica", "B", 9)
                    pdf.cell(40, h_cell, "Protocolo do evento:", border=1, new_x="RIGHT", new_y="TOP")
                    pdf.set_font("Helvetica", "", 8)
                    pdf.cell(w_full - 40, h_cell, prot_num[:60], border=1, new_x="LMARGIN", new_y="NEXT")
                if prot_date:
                    prot_date_str = _format_protocol_date_local(prot_date, document)
                    pdf.set_font("Helvetica", "B", 9)
                    pdf.cell(40, h_cell, "Data do registro:", border=1, new_x="RIGHT", new_y="TOP")
                    pdf.set_font("Helvetica", "", 8)
                    pdf.cell(w_full - 40, h_cell, prot_date_str[:40], border=1, new_x="LMARGIN", new_y="NEXT")
        # Justificativa do cancelamento: documento (cancel_reason) ou evento de cancelamento
        justificativa = (getattr(document, "cancel_reason", None) or "").strip()
        if not justificativa and getattr(document, "cancel_event_id", None):
            justificativa = (document.cancel_event_id.justification or "").strip()
        justificativa = justificativa[:500]
        if justificativa:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(w_full, h_cell, "Justificativa do cancelamento:", border=1, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 8)
            pdf.multi_cell(w_full, 5, justificativa, border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(
            w_full,
            h_cell,
            "O cancelamento e um evento registrado na SEFAZ (evento 110111). O XML da NF-e (procNFe) nao e alterado.",
            new_x="LMARGIN",
            new_y="NEXT",
        )

        out = pdf.output()
        if isinstance(out, str):
            out = out.encode("latin-1")
        return out
    except Exception as e:
        _logger.debug("build_cancelada_banner_pdf: %s", e)
        return None
