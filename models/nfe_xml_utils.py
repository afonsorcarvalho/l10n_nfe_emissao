# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Utilitários puros para manipulação de XML NF-e.

Funções sem dependência de models Odoo; usadas por mixins de emissão,
PDF e eventos. Responsabilidade única: tratamento de strings/XML do leiaute NFe.
"""

import logging
import re

_logger = logging.getLogger(__name__)


def strip_xml_declaration(xml_str):
    """
    Remove declaração XML (<?xml ...?>) do início da string.

    Necessário ao montar procNFe: NFe e protNFe são concatenados dentro
    de <nfeProc>; se trouxerem <?xml ...?>, o parser falha com
    "XML or text declaration not at start of entity".
    """
    if not xml_str:
        return xml_str
    s = str(xml_str).strip()
    if s.upper().startswith("<?XML"):
        end = s.find("?>") + 2
        return s[end:].lstrip()
    return s


def escape_xml_text(text):
    """
    Escapa caracteres especiais para XML (evita erro EntityName ao validar).

    & -> &amp;, < -> &lt;, > -> &gt;, " -> &quot;, ' -> &apos;
    """
    if not text:
        return text
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def ensure_protocol_in_procnfe(xml_proc, nfe_protocol, dh_recbto=None):
    """
    Garante que protNFe/infProt tenha nProt e dhRecbto para o BrazilFiscalReport.

    O BrazilFiscalReport exibe o protocolo via extract_text(prot_nfe, "nProt").
    Se o XML tiver estrutura diferente (ex.: infProt com namespace), injeta os
    valores quando nfe_protocol estiver disponível no documento.
    """
    if not nfe_protocol or not xml_proc:
        return xml_proc
    try:
        from lxml import etree

        ns = "http://www.portalfiscal.inf.br/nfe"
        root = etree.fromstring(xml_proc.encode("utf-8"))
        prot_nfe = root.find(f".//{{{ns}}}protNFe")
        if prot_nfe is None:
            return xml_proc
        inf_prot = prot_nfe.find(f"{{{ns}}}infProt")
        if inf_prot is None:
            inf_prot = etree.SubElement(prot_nfe, f"{{{ns}}}infProt")
        n_prot_elem = inf_prot.find(f"{{{ns}}}nProt")
        if n_prot_elem is not None and (n_prot_elem.text or "").strip():
            return xml_proc  # Já tem protocolo válido
        if n_prot_elem is None:
            n_prot_elem = etree.SubElement(inf_prot, f"{{{ns}}}nProt")
        n_prot_elem.text = str(nfe_protocol).strip()
        if dh_recbto:
            dh_elem = inf_prot.find(f"{{{ns}}}dhRecbto")
            if dh_elem is None:
                dh_elem = etree.SubElement(inf_prot, f"{{{ns}}}dhRecbto")
            dh_elem.text = dh_recbto
        return etree.tostring(
            root, encoding="unicode", method="xml", xml_declaration=True
        )
    except Exception as e:
        _logger.debug("ensure_protocol_in_procnfe: %s", e)
        return xml_proc


def format_ret_envi_nfe_details(ret_env_nfe):
    """
    Extrai e formata todos os detalhes relevantes de retEnviNFe para o log.

    Inclui dados do lote (tpAmb, verAplic, cStat, xMotivo, dhRecbto) e de cada
    protNFe/infProt (chNFe, nProt, cStat, xMotivo) — autorização, rejeição ou
    denegação de cada NF-e do lote.

    :param ret_env_nfe: elemento lxml (retEnviNFe)
    :return: str formatado para body_extra do chatter
    """
    if ret_env_nfe is None:
        return ""
    ns = "http://www.portalfiscal.inf.br/nfe"
    parts = []

    # Dados do lote (retEnviNFe)
    for tag in ("tpAmb", "verAplic", "cUF", "dhRecbto"):
        el = ret_env_nfe.find(f".//{{{ns}}}{tag}")
        if el is not None and el.text:
            label = {"tpAmb": "Ambiente", "verAplic": "Versão SEFAZ", "cUF": "UF", "dhRecbto": "Recebimento"}.get(tag, tag)
            parts.append(f"{label}: {el.text}")

    # infRec / nRec (número do recibo do lote)
    inf_rec = ret_env_nfe.find(f".//{{{ns}}}infRec")
    if inf_rec is not None:
        n_rec = inf_rec.find(f".//{{{ns}}}nRec")
        if n_rec is not None and n_rec.text:
            parts.append(f"Nº Recibo Lote: {n_rec.text}")

    # Cada protNFe/infProt (resultado por NF-e)
    for prot in ret_env_nfe.findall(f".//{{{ns}}}protNFe"):
        inf_prot = prot.find(f".//{{{ns}}}infProt")
        if inf_prot is not None:
            items = []
            for tag, label in (
                ("chNFe", "Chave"),
                ("nProt", "Protocolo"),
                ("cStat", "cStat"),
                ("xMotivo", "Motivo"),
                ("dhRecbto", "Data/Hora"),
            ):
                el = inf_prot.find(f".//{{{ns}}}{tag}")
                if el is not None and el.text:
                    items.append(f"{label}: {el.text}")
            if items:
                parts.append("--- NF-e no lote ---")
                parts.extend(items)

    return "\n".join(parts) if parts else ""


def wrap_inf_evento_as_proc_evento(inf_evento_xml):
    """
    Envolve infEvento em procEventoNFe para DACCe (BrazilFiscalReport).

    O DaCCe espera procEventoNFe; o retorno da CCe é apenas infEvento.
    """
    if not inf_evento_xml or not inf_evento_xml.strip():
        return None
    ns = "http://www.portalfiscal.inf.br/nfe"
    inner = inf_evento_xml.strip()
    if inner.upper().startswith("<?XML"):
        idx = inner.find("?>") + 2
        inner = inner[idx:].strip()
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<procEventoNFe versao="1.00" xmlns="{ns}">'
        f'<retEvento versao="1.00">{inner}</retEvento>'
        f"</procEventoNFe>"
    )


# Tag do wrapper usado no anexo da CCe (chatter): inclui resposta SEFAZ + texto da correção.
# O retEvento (resposta) não contém xCorrecao; o texto fica em <correcaoTexto> para o DACCe.
NFE_CCE_WRAPPER_ROOT = "nfe_cce_attachment"
NFE_CCE_CORRECAO_TAG = "correcaoTexto"


def wrap_cce_response_with_text(response_xml, correcao_texto):
    """
    Empacota a resposta SEFAZ (retEvento) e o texto da correção para anexo no documento.

    O retEvento não contém xCorrecao; ao anexar no chatter guardamos o texto aqui
    para que o DACCe (PDF) possa exibir "Texto da correção". Retorna XML único
    com retEvento em CDATA e correcaoTexto em CDATA.
    """
    if not response_xml:
        response_xml = ""
    text = (correcao_texto or "").strip()
    # CDATA não pode conter ]]>, substituir por espaço se existir
    if "]]>" in text:
        text = text.replace("]]>", " ")
    if "]]>" in response_xml:
        response_xml = response_xml.replace("]]>", " ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<{NFE_CCE_WRAPPER_ROOT}>"
        f"<retEventoXml><![CDATA[{response_xml.strip()}]]></retEventoXml>"
        f"<{NFE_CCE_CORRECAO_TAG}><![CDATA[{text}]]></{NFE_CCE_CORRECAO_TAG}>"
        f"</{NFE_CCE_WRAPPER_ROOT}>"
    )


def get_ret_evento_from_cce_attachment(xml_str):
    """
    Retorna o XML do retEvento para uso no DaCCe (BrazilFiscalReport).

    Se o conteúdo for o wrapper (nfe_cce_attachment), extrai o CDATA de retEventoXml.
    Caso contrário retorna o próprio xml_str (anexos antigos ou resposta pura).
    """
    if not xml_str or not xml_str.strip():
        return xml_str
    s = xml_str.strip()
    if NFE_CCE_WRAPPER_ROOT in s[:200]:
        match = re.search(
            r"<retEventoXml>\s*<!\[CDATA\[(.*?)\]\]>\s*</retEventoXml>",
            s,
            re.DOTALL,
        )
        if match:
            return match.group(1).strip()
    return xml_str


def extract_xcorrecao_from_infevento(xml_str):
    """
    Extrai texto da correção para exibição no DACCe.

    Ordem: (1) tag correcaoTexto do wrapper (anexo CCe do chatter);
    (2) xCorrecao do XML (pedido envEvento, se algum dia for armazenado).
    """
    if not xml_str:
        return ""
    s = xml_str
    # Wrapper do anexo CCe (correcaoTexto em CDATA ou texto direto)
    match = re.search(
        rf"<{NFE_CCE_CORRECAO_TAG}>\s*<!\[CDATA\[(.*?)\]\]>\s*</{NFE_CCE_CORRECAO_TAG}>",
        s,
        re.DOTALL,
    )
    if match:
        return (match.group(1) or "").strip()
    match = re.search(
        rf"<{NFE_CCE_CORRECAO_TAG}[^>]*>([^<]*)</{NFE_CCE_CORRECAO_TAG}>",
        s,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return (match.group(1) or "").strip()
    # XML do pedido (infEvento com detEvento/xCorrecao)
    match = re.search(r"<xCorrecao[^>]*>([^<]*)</xCorrecao>", s, re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else "").strip()


def extract_nprot_from_infevento(xml_str):
    """Extrai protocolo (nProt) do XML infEvento retornado pela SEFAZ."""
    if not xml_str:
        return ""
    match = re.search(r"<nProt[^>]*>([^<]*)</nProt>", xml_str, re.IGNORECASE)
    return (match.group(1) if match else "").strip()
