# Guia de Implementação - Emissão de NF-e

Documentação técnica do módulo `l10n_nfe_emissao`.

## Arquitetura

```
l10n_br_fiscal.document (OCA)
         ↓ (herda)
l10n_nfe_emissao.nfe_document
         ↓ (usa)
    ┌────────────┬─────────────┬──────────────┐
    │            │             │              │
 nfelib      erpbrasil    erpbrasil      l10n_br_nfe_spec
 (XML)      .assinatura  .transmissao    (modelos abstratos)
```

## Fluxo de emissão

### 1. Preparação (`_prepare_nfe_emission`)

```python
# Mapeia documento fiscal → nfelib.Nfe
nfe = Nfe(
    infNFe=Nfe.InfNfe(
        ide=_build_nfe_ide(),      # Identificação
        emit=_build_nfe_emit(),    # Emitente
        dest=_build_nfe_dest(),    # Destinatário
        det=_build_nfe_items(),    # Itens
        total=_build_nfe_total(),  # Totais
    )
)
```

### 2. Serialização

```python
# nfelib → XML string
xml_nfe = nfe.to_xml(indent="  ")  # indent substitui pretty_print (deprecado)
```

### 3. Assinatura

```python
# erpbrasil.assinatura
from erpbrasil.assinatura import assinatura

pfx_data, password = get_certificate_data(company_id)
xml_assinado = assinatura.assina_xml(xml_nfe, pfx_data, password)
```

### 4. Transmissão (`nfe_transmissao.enviar_lote`)

```python
transmissor = self.env["l10n_nfe_emissao.transmissao"]
envio_info = transmissor.enviar_lote(
    xml_envelope=envelope_xml,
    lote_id=lote_id,
    processor=processor,  # já contém _transmissao com certificado
    codigo_uf=codigo_uf,  # 2 dígitos IBGE
    ambiente=ambiente,    # "1"=produção, "2"=homologação
)
retorno_raw = envio_info["response"]
```

O helper:

1. Usa `erpbrasil.edoc.nfe.localizar_url` para descobrir o **autorizador principal** da UF.  
2. Para o Maranhão (UF 21) adiciona automaticamente o **SVC-AN** como contingência usando as URLs oficiais para NF-e 4.00 (fonte: [SEFAZ/ES – Relação de serviço web](https://sefaz.es.gov.br/relacao-de-servico-web-2)).  
3. Itera sobre as URLs (principal → contingência), abrindo `processor._transmissao.cliente(url)` e chamando `enviar("nfeAutorizacaoLote", xml)`.  
4. Em falha de rede (`requests`, `socket.gaierror`, `TimeoutError`) registra a causa e tenta o próximo servidor.  
5. Se todos falharem, lança `UserError` informando as URLs testadas e o último erro.

Quando a transmissão ocorre em contingência, o log alerta explicitamente (`SVC-AN (contingência)`), mas o parsing e a atualização do documento permanecem idênticos.

### 5. DANFE (PDF)

Prioridade para **BrazilFiscalReport** (layout manual nacional NF-e): quando o campo `nfe_proc_xml` está preenchido (armazenado ao autorizar), `make_pdf()` usa a biblioteca para gerar o PDF. Configuração via `DanfeConfig`:

- `InvoiceDisplay.FULL_DETAILS` – seção Fatura/Duplicata completa (manual nacional)
- `display_pis_cofins=True` – exibe PIS/COFINS nos totais
- `DecimalConfig(price_precision=2, quantity_precision=2)` – formatação padrão
- Logo da empresa (se configurado em `res.company.logo`)

Referência: [BrazilFiscalReport - DANFE](https://engenere.github.io/BrazilFiscalReport/danfe/). Fallback: relatório QWeb (`report/report_nfe_danfe.xml`).

Ao autorizar, são gravados `nfe_xml_signed` (XML assinado) e `nfe_proc_xml` (NFe + protNFe). Para documentos antigos sem esses campos, use o botão **Consultar NF-e** (`action_consultar_nfe`), que consulta a SEFAZ via `nfeConsultaNF`, obtém o protNFe e reconstrói o procNFe (a partir de `nfe_xml_signed` ou re-assinando `nfe_xml`).

### 6. Carta de Correção (CCe)

O evento 110110 é enviado via `NFeAdapter.carta_correcao(chave, sequencia, justificativa)`. A justificativa deve ter entre 15 e 1000 caracteres. Máximo 20 CCe por NF-e.

### 7. Cancelamento

O evento 110111 é enviado via `NFeAdapter.cancela_documento(chave, protocolo_autorizacao, justificativa)`. A justificativa deve ter no mínimo 15 caracteres.

**O XML da NF-e (procNFe / `nfe_proc_xml`) não é alterado no cancelamento.** Assim como na Carta de Correção, o cancelamento é apenas um evento registrado na SEFAZ e vinculado à chave da NF-e; o conteúdo do procNFe (NFe + protNFe de autorização) permanece o mesmo. Ao gerar o DANFE (PDF) de uma nota cancelada, o sistema usa o mesmo `nfe_proc_xml` e insere uma primeira página com o aviso "NF-e CANCELADA".

### 8. NF-e de Devolução

O documento de devolução é criado via `action_create_return` (botão "Devolver"). O campo `return_origin_document_id` vincula à NF-e original. No XML, o grupo NFref com refNFe (chave original) é incluído em `<ide>` quando `finNFe=4` e há documento de origem. A operação fiscal de devolução deve ter `edoc_purpose=4`.

### 9. Atualização do formulário e registro de eventos SEFAZ

Após **Consultar NF-e**, **Emitir NF-e** ou **Cancelar NF-e**, o formulário é recarregado automaticamente para exibir os campos XML e o estado atualizado. O wizard de cancelamento retorna ação que reabre o documento.

As respostas da SEFAZ são registradas no documento via chatter (`message_post`):

- **Autorização NF-e**: retEnviNFe (cStat, xMotivo, XML anexo)
- **Consulta NF-e**: retConsSitNfe (cStat, xMotivo, XML anexo)
- **Cancelamento NF-e**: retEvento (cStat, xMotivo, XML anexo)
- **Carta de Correção**: retEvento (cStat, xMotivo, XML anexo)

Os XMLs das respostas ficam disponíveis como anexos nas mensagens do documento.

### 10. Processamento do retorno

```python
# Parse XML de retorno SEFAZ
# Extrair: chave, protocolo, status (100=Autorizada, etc.)
# Atualizar documento:
#   - nfe_key = chave_acesso
#   - nfe_protocol = protocolo
#   - state_edoc = 'autorizada'
```

## Mapeamento de dados e conformidade com o schema NF-e 4.0

Todos os campos com Enumeration ou Pattern no schema são mapeados via helpers
em `nfe_document.py` para evitar rejeição 225 (Falha no Esquema XML).

### Valores permitidos (Layout NF-e 4.0)

| Campo | Valores | Mapeamento |
|-------|---------|------------|
| tpNF | 0, 1 | in→0, out/all→1 |
| finNFe | 1, 2, 3, 4 | edoc_purpose; 5→1 |
| indFinal | 0, 1 | ind_final |
| indPres | 0, 1, 2, 3, 4, 5, 9 | ind_pres |
| tpAmb | 1, 2 | nfe_environment |
| procEmi | 0, 1, 2, 3 | 0=Aplicação própria |
| CRT | 1, 2, 3 | tax_framework |
| indIEDest | 1, 2, 9 | IE/ind_ie_dest |
| cNF | 8 dígitos | document_number (apenas dígitos) |
| dhEmi, dhSaiEnt | TDateTimeUTC | YYYY-MM-DDTHH:MM:SS-03:00 |
| cMunFG | 7 dígitos | ibge_code município |
| serie | 0-999 | document_serie_id.code |
| cEAN, cEANTrib | 0 ou GTIN | 0 quando sem código de barras |

### l10n_br_fiscal.document → nfelib.Nfe.InfNfe.Ide

| Campo fiscal | Campo NF-e | Obs |
|--------------|------------|-----|
| company_id.state_id | cUF | Código IBGE 2 dígitos |
| document_number | cNF, nNF | Apenas dígitos; cNF 8 dígitos |
| document_serie_id.code | serie | 0-999 via _map_serie_nfe |
| document_date | dhEmi, dhSaiEnt | TDateTimeUTC -03:00 |
| fiscal_operation_type | tpNF | _map_tpnf (in/out→0/1) |
| edoc_purpose | finNFe | _map_finnfe (1-4) |
| ind_final | indFinal | _map_indfinal |
| ind_pres | indPres | _map_indpres |
| company_id.nfe_environment | tpAmb | _map_tpamb |

### l10n_br_fiscal.document → nfelib.Nfe.InfNfe.Emit

| Campo fiscal | Campo NF-e |
|--------------|------------|
| company_id.cnpj_cpf | CNPJ |
| company_id.legal_name | xNome |
| company_id.name | xFant |
| company_id.street | enderEmit.xLgr |
| company_id.inscr_est | IE |
| company_id.tax_framework | CRT |

### l10n_br_fiscal.document.line → nfelib.Nfe.InfNfe.Det

| Campo fiscal | Campo NF-e |
|--------------|------------|
| product_id.default_code | prod.cProd |
| name | prod.xProd |
| ncm_id.code | prod.NCM |
| cfop_id.code | prod.CFOP |
| quantity | prod.qCom |
| price_unit | prod.vUnCom |

## Próximos passos

## Referências

- **nfelib**: https://github.com/akretion/nfelib
- **erpbrasil**: https://github.com/erpbrasil
- **OCA l10n-brazil**: https://github.com/OCA/l10n-brazil
- **Leiaute NF-e 4.0**: https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=/fwLvNZGWMJ8=
