# Copyright (C) 2025 - Custom
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

"""
Módulo responsável por transmitir NF-e à SEFAZ, escolhendo de forma
automática o melhor WebService disponível (principal ou contingência).
"""

import logging
import socket

from lxml import etree as ET

import requests
from odoo import _, models
from odoo.exceptions import UserError

try:
    from erpbrasil.edoc.nfe import WS_NFE_AUTORIZACAO, localizar_url
except ImportError:  # pragma: no cover
    localizar_url = None
    WS_NFE_AUTORIZACAO = None

_logger = logging.getLogger(__name__)


class NFeTransmissao(models.AbstractModel):
    """Helper de transmissão NF-e (seleção de servidores e fallback)."""

    _name = "l10n_nfe_emissao.transmissao"
    _description = "NF-e Transmission Helper"

    # Estados atendidos pelo SVC-AN para contingência (códigos IBGE com 2 dígitos)
    _SVC_AN_UFS = {"21"}  # Maranhão (MA); expandir conforme necessidade futura

    # URLs oficiais do SVC-AN para NF-e 4.00 (fonte: SEFAZ ES - relação de serviços)
    _SVC_AN_URLS = {
        1: "https://www.svc.fazenda.gov.br/NFeAutorizacao4/NFeAutorizacao4.asmx",  # Produção
        2: "https://hom.svc.fazenda.gov.br/NFeAutorizacao4/NFeAutorizacao4.asmx",  # Homologação
    }

    def _normalize_uf(self, codigo_uf):
        """Retorna o código da UF sempre com dois dígitos."""
        if not codigo_uf:
            return ""
        return str(codigo_uf).zfill(2)

    def _get_contingencia_urls(self, codigo_uf, ambiente):
        """Retorna URLs de contingência autorizadas para a UF."""
        urls = []
        uf = self._normalize_uf(codigo_uf)
        if uf in self._SVC_AN_UFS:
            svc_url = self._SVC_AN_URLS.get(int(ambiente))
            if svc_url:
                urls.append(
                    {
                        "url": svc_url,
                        "descricao": "SVC-AN (contingência)",
                        "contingencia": True,
                    }
                )
        return urls

    def _get_autorizacao_urls(self, codigo_uf, ambiente, modelo="55"):
        """
        Retorna lista ordenada de URLs para tentar transmissão (1º principal, demais contingência).
        """
        urls = []
        if localizar_url:
            try:
                principal = localizar_url(
                    WS_NFE_AUTORIZACAO,
                    str(self._normalize_uf(codigo_uf)),
                    mod=str(modelo),
                    ambiente=int(ambiente),
                    contingencia=False,
                )
                if principal:
                    urls.append(
                        {
                            "url": principal,
                            "descricao": "Autorizador principal",
                            "contingencia": False,
                        }
                    )
            except Exception as exc:  # pragma: no cover - depende das libs externas
                _logger.warning(
                    "Não foi possível localizar o WebService principal para UF %s: %s",
                    codigo_uf,
                    exc,
                    exc_info=True,
                )
        else:  # pragma: no cover
            _logger.warning(
                "Biblioteca erpbrasil.edoc.nfe não disponível; ignorando URL principal."
            )

        urls.extend(self._get_contingencia_urls(codigo_uf, ambiente))

        if not urls:
            raise UserError(
                _(
                    "Não foi possível determinar URLs de autorização para a UF %s (ambiente %s)."
                )
                % (codigo_uf, ambiente)
            )
        return urls

    def _log_tentativa(self, idx, total, descricao, url):
        """Log padronizado para cada tentativa de envio."""
        _logger.info(
            "Transmissão NF-e: tentativa %s/%s usando %s -> %s",
            idx,
            total,
            descricao or "WebService",
            url,
        )

    def enviar_lote(
        self,
        xml_envelope,
        lote_id,
        processor,
        codigo_uf,
        ambiente="2",
        modelo="55",
    ):
        """
        Envia o lote já assinado tentando os servidores disponíveis (principal + contingência).

        :param xml_envelope: XML <enviNFe> completo em string
        :param lote_id: identificador do lote enviado à SEFAZ
        :param processor: instância do adaptador (possui _transmissao com certificado)
        :param codigo_uf: código IBGE da UF emissora (2 dígitos)
        :param ambiente: "1" produção / "2" homologação
        :param modelo: modelo do documento ("55" NF-e)
        :return: dict {response, url, descricao, contingencia}
        """
        urls = self._get_autorizacao_urls(codigo_uf, ambiente, modelo=modelo)
        total = len(urls)
        last_error = None

        for idx, entry in enumerate(urls, start=1):
            url = entry["url"]
            descricao = entry.get("descricao")
            self._log_tentativa(idx, total, descricao, url)
            try:
                # Recria o XML a cada tentativa para evitar reutilizar objetos mutáveis
                envelope_etree = ET.fromstring(xml_envelope.encode("utf-8"))
                with processor._transmissao.cliente(url):
                    retorno_raw = processor._transmissao.enviar(
                        "nfeAutorizacaoLote", envelope_etree
                    )
                _logger.info(
                    "Transmissão NF-e concluída via %s (HTTP %s)",
                    descricao or url,
                    getattr(retorno_raw, "status_code", "desconhecido"),
                )
                return {
                    "response": retorno_raw,
                    "url": url,
                    "descricao": descricao,
                    "contingencia": entry.get("contingencia", False),
                }
            except (requests.exceptions.RequestException, socket.gaierror, TimeoutError) as exc:
                last_error = exc
                _logger.warning(
                    "Falha de comunicação com %s (%s): %s",
                    descricao or url,
                    url,
                    exc,
                    exc_info=True,
                )
            except Exception as exc:  # pragma: no cover - falhas inesperadas
                last_error = exc
                _logger.warning(
                    "Erro ao transmitir NF-e usando %s (%s): %s",
                    descricao or url,
                    url,
                    exc,
                    exc_info=True,
                )

        urls_testadas = ", ".join(f"{item.get('descricao')}: {item['url']}" for item in urls)
        raise UserError(
            _(
                "Não foi possível comunicar com a SEFAZ após tentar todos os endereços. "
                "URLs testadas: %s. Último erro: %s"
            )
            % (urls_testadas, last_error)
        )
