# l10n_nfe_emissao – Emissão de Nota Fiscal Eletrônica (NF-e)

Módulo para emissão de NF-e no Odoo 18, utilizando localização brasileira OCA (l10n-brazil) e bibliotecas erpbrasil/nfelib.

## Funcionalidades implementadas

### 1. Geração de XML NF-e (leiaute 4.0)

- **`_prepare_nfe_emission()`**: Mapeia `l10n_br_fiscal.document` para `nfelib.Nfe`.
- **Tags implementadas**:
  - `<ide>`: Identificação (UF, modelo 55, série, número, datas, finalidade, refNFe para devolução).
  - `<emit>`: Emitente (CNPJ, razão social, endereço, IE, CRT).
  - `<dest>`: Destinatário (CPF/CNPJ, nome, endereço, indIEDest).
  - `<det>`: Itens/produtos (código, descrição, NCM, CFOP, quantidades, valores, ICMS, PIS/COFINS).
  - `<total>`: Totalizadores (bases ICMS, valores impostos, total NF-e).
  - `<transp>`: Transporte (modFrete 9 quando sem ocorrência).
  - `<pag>`: Pagamento (detPag obrigatório).
- **Cálculo de chave de acesso**: 44 dígitos com DV (dígito verificador).
- **Mapeamento conforme schema NF-e 4.0**: tpNF, finNFe, indFinal, indPres, tpAmb, procEmi, CRT, indIEDest, cNF (8 dígitos), dhEmi/dhSaiEnt (TDateTimeUTC), cMunFG, serie (0–999), cEAN/cEANTrib.

### 2. Assinatura digital

- Utiliza **erpbrasil.assinatura** para assinar XML com certificado A1.
- Integra com `l10n_br_fiscal.certificate` para obter certificado da empresa.
- Valida presença e validade do certificado antes da emissão.

### 3. Fluxo de confirmação e emissão em duas etapas

- **Botão "Confirmar"** (`action_confirmar_nfe`): Gera a **chave NF-e** (44 dígitos) e altera o estado do documento de "Em digitação" para "Aguardando envio" (confirmado).
  - A chave é gerada localmente com código numérico aleatório e DV (dígito verificador).
  - Após a confirmação, a **chave NF-e é imutável** e não pode mais ser alterada.
  - Série e número ficam readonly (não editáveis) após a confirmação.
  - A validação dos dados (empresa, destinatário, itens, certificado) é feita na confirmação.
- **Botão "Emitir NF-e"** (`action_emit_nfe`): Disponível apenas após a confirmação (estado "Aguardando envio") ou em reenvio (estado "Em digitação" com chave já definida).
  - Monta o XML usando a **chave já definida** na confirmação (não gera nova chave).
  - Assina digitalmente e transmite à SEFAZ.
  - Estado após transmissão: "Autorizada" (sucesso) ou "Rejeitada" (erro).

### 4. Transmissão à SEFAZ

- Fluxo: XML (com chave existente) → assinatura → NFeAdapter (envio) → parse do retorno.
- Usa `nfelib.nfe.ws.edoc_legacy.NFeAdapter` com `TransmissaoSOAP`.
- Ambiente (Produção/Homologação) via `company.nfe_environment` (aba NF-e na empresa).
- **Transmissão com fallback**: servidor principal da UF; em falha, contingência SVC-AN (ex.: Maranhão – UF 21), com tentativas automáticas e registro de erros.

### 5. Processamento do retorno SEFAZ

- Parsing do XML de retorno (retEnviNFe): chave, protocolo, cStat (100 = Autorizada, etc.).
- Atualização do documento: `nfe_protocol`, `state_edoc` (autorizada/rejeitada), `nfe_xml_signed`, `nfe_proc_xml` (NFe + protNFe).
- **Chave NF-e não é sobrescrita** quando já existe (confirmação ou reenvio); o retorno SEFAZ apenas confirma a mesma chave.

### 6. Rejeição e reenvio de NF-e

- Quando a SEFAZ **rejeita** a NF-e (cStat != 100), o estado do documento fica "Rejeitada".
- **Motivo da rejeição** (código + mensagem da SEFAZ) é exibido:
  - Em notificação popup no momento da rejeição.
  - No formulário do documento, logo abaixo do banner de estado (código e descrição detalhada da rejeição).
- **Botão "Voltar p/ Em Digitação"** (padrão do l10n_br_fiscal): Disponível quando o documento está rejeitado.
  - Volta o documento para o estado "Em digitação", permitindo editar os dados para corrigir as inconsistências.
  - A **chave NF-e não é alterada** (permanece a mesma) - proteção implementada no método `write()`.
  - Após corrigir, use "Emitir NF-e" para **reenviar** à SEFAZ com a mesma chave.

### 7. Consultar NF-e

- **`action_consultar_nfe()`**: Consulta a SEFAZ via `nfeConsultaNF`.
- Obtém protNFe e reconstrói procNFe (a partir de `nfe_xml_signed` ou re-assinando `nfe_xml`).
- Útil para documentos antigos sem `nfe_proc_xml` gravado.

### 8. Validações

- Empresa com CNPJ configurado (14 dígitos).
- Destinatário válido.
- Itens no documento.
- Certificado digital disponível e válido.
- Validações executadas na confirmação do documento (antes de gerar a chave).

### 9. Interface – documento fiscal

- **Botões**:
  - **Confirmar**: Visível em "Em digitação" sem chave; gera chave NF-e e confirma documento.
  - **Emitir NF-e**: Visível em "Aguardando envio" (após confirmação) ou em "Em digitação" com chave (reenvio).
  - **Voltar p/ Em Digitação**: Botão padrão do l10n_br_fiscal; permite editar documento rejeitado e reenviar com a mesma chave.
  - **Consultar NF-e**: Visível quando há chave; consulta situação na SEFAZ.
- **Campos**:
  - **Chave NF-e**: Imutável após geração (confirmação); exibida quando preenchida.
  - **Série e Número**: Editáveis apenas antes da confirmação; readonly quando há chave.
  - **Motivo da Rejeição**: Exibido logo abaixo do banner de estado quando documento está rejeitado (código + descrição SEFAZ).
- **Aba EDI**: Protocolo, XMLs, eventos SEFAZ, lista de eventos com Sequência, Parceiro, Nº Recibo Lote, Mensagem SEFAZ.
- **Notificações**: Sucesso/erro com recarregamento do formulário após confirmação/emissão/consulta/cancelamento.

### 10. Imutabilidade da chave NF-e

- A **chave de acesso** é gerada na **confirmação** do documento (botão "Confirmar") e **não pode mais ser alterada** após definida.
- Proteção no modelo: método `write()` sobrescrito para bloquear tentativas de modificar `nfe_key` quando já preenchida.
- Chave usada no XML de emissão: sempre a já gravada no documento (não gera nova chave no momento da transmissão).
- Reenvio após rejeição: usa a **mesma chave** definida na primeira confirmação.

### 11. DANFE (PDF)

- **`make_pdf()`**: Geração de DANFE com **BrazilFiscalReport** (layout manual nacional).
  - Configuração: InvoiceDisplay.FULL_DETAILS, display_pis_cofins, DecimalConfig, logo da empresa.
- **Fallback**: relatório QWeb `report_nfe_danfe` (A4 retrato).
- **Merge DACCe**: anexa páginas de Carta de Correção ao PDF do DANFE (BrazilFiscalReport DaCCe ou FPDF).
- **Banner NF-e cancelada**: primeira página com aviso "NF-e CANCELADA" quando `state_edoc = cancelada`.
- **`action_download_danfe()`**: Download do PDF do DANFE.

### 12. Cancelamento de NF-e

- Evento 110111 enviado via NFeAdapter (`cancela_documento`).
- Justificativa mínima 15 caracteres.
- **Wizard** herdado: `l10n_br_fiscal.document.cancel.wizard` → para NF-e 55 chama `_document_cancel_nfe`, notificação e reload do formulário.
- Resposta SEFAZ (retEvento) registrada no chatter com XML anexo.

### 13. Carta de Correção (CCe)

- Evento 110110 via `NFeAdapter.carta_correcao(chave, sequencia, justificativa)`.
- Justificativa entre 15 e 1000 caracteres; até 20 CCe por NF-e.
- Resposta SEFAZ (retEvento) registrada no chatter.

### 14. NF-e de Devolução

- Documento de devolução criado via **Devolver** (workflow base); campo `return_origin_document_id` vincula à NF-e original.
- No XML: grupo **NFref** com **refNFe** (chave original) em `<ide>` quando `finNFe=4` e há documento de origem.
- Operação fiscal de devolução deve ter `edoc_purpose=4`.

### 15. Consultar Notas Recebidas (DFe)

- Integra com **l10n_br_fiscal_dfe** (distribuição DFe).
- Menu: **Fiscal** → **Documentos** → **Consultar Notas Recebidas**.
- Busca NF-e em que a empresa é destinatária no webservice da SEFAZ.
- Processamento: docZip (procNFe) descompactado, armazenado como anexo e criado `l10n_br_fiscal.document` de entrada vinculado ao DFe.

### 16. Registro de eventos SEFAZ no chatter

- **Autorização NF-e**: retEnviNFe (cStat, xMotivo, XML anexo).
- **Consulta NF-e**: retConsSitNfe (cStat, xMotivo, XML anexo).
- **Cancelamento NF-e**: retEvento (cStat, xMotivo, XML anexo).
- **Carta de Correção**: retEvento (cStat, xMotivo, XML anexo).

### 14. Configuração da empresa

- **Ambiente NF-e**: Produção (1) ou Homologação (2) – `nfe_environment`.
- **Série por ambiente** (opcional): `nfe_serie_homologacao_id`, `nfe_serie_producao_id` para usar série distinta conforme ambiente.

## Dependências

| Módulo/Biblioteca           | Tipo   | Descrição                                              |
|----------------------------|--------|--------------------------------------------------------|
| l10n_br_base               | Odoo   | Base localização BR (CNPJ, CPF, cidades)              |
| l10n_br_fiscal_certificate | Odoo   | Gerenciamento certificado A1                           |
| l10n_br_fiscal             | Odoo   | Motor fiscal (documentos, impostos, CFOP)             |
| l10n_br_nfe_spec           | Odoo   | Modelos abstratos NF-e (leiaute 4.0)                   |
| l10n_br_fiscal_dfe         | Odoo   | Consulta distribuição DFe (notas recebidas)            |
| l10n_br_fiscal_edi         | Odoo   | EDI: eventos, PDF, CCe, cancelamento                   |
| erpbrasil.base             | Python | Validação e utilitários fiscais                        |
| erpbrasil.assinatura       | Python | Assinatura digital XML                                |
| erpbrasil.transmissao      | Python | Comunicação SOAP com SEFAZ                             |
| nfelib                     | Python | Bindings XML NF-e (leiaute 4.0)                        |
| brazilfiscalreport         | Python | DANFE layout padrão nacional                           |
| pypdf                      | Python | Merge PDF DANFE + DACCe                                |

## Instalação

```bash
# 1. Build da imagem com dependências Python
docker compose build web

# 2. Subir containers
docker compose up -d

# 3. Instalar módulos pela interface (Apps) ou via CLI, nesta ordem:
#    1. uom_alias
#    2. l10n_br_base
#    3. l10n_br_fiscal_certificate
#    4. l10n_br_fiscal
#    5. l10n_br_nfe_spec
#    6. l10n_br_fiscal_dfe
#    7. l10n_br_fiscal_edi
#    8. l10n_nfe_emissao
```

## Uso

1. **Configurar empresa** (Configurações > Empresas): CNPJ, IE, endereço, regime tributário (tax_framework), impostos padrão. Aba **NF-e**: ambiente (Produção/Homologação), séries por ambiente (opcional).

2. **Configurar certificado digital**: **Fiscal** → **Configuração** → **Certificados** (certificado A1, anexar .pfx e senha).

3. **Criar documento fiscal**: tipo NF-e (modelo 55), destinatário, itens, impostos.

4. **Emitir NF-e**: botão **Emitir NF-e** no documento; XML gerado, assinado e transmitido; retorno processado e formulário atualizado.

5. **Consultar NF-e**: botão **Consultar NF-e** quando já existe chave (atualiza procNFe/XML quando ausentes).

6. **DANFE**: geração de PDF (BrazilFiscalReport ou QWeb), merge com DACCe, banner para nota cancelada; download via ação **Baixar DANFE**.

7. **Cancelar**: usar o wizard de cancelamento do documento (justificativa ≥ 15 caracteres); evento enviado à SEFAZ e chatter atualizado.

8. **Carta de Correção**: via fluxo EDI do documento (justificativa 15–1000 caracteres).

9. **Consultar Notas Recebidas**: **Fiscal** → **Documentos** → **Consultar Notas Recebidas**; configurar DFe em Empresa → Aba Fiscal → DF-e; NF-e importadas como documentos de entrada com procNFe anexo.

## Licença

AGPL-3
