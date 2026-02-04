# l10n_nfe_emissao - Emissão de Nota Fiscal Eletrônica (NF-e)

Módulo para emissão de NF-e no Odoo 18, utilizando localização brasileira OCA (l10n-brazil) e bibliotecas erpbrasil.

## Funcionalidades implementadas

### 1. Geração de XML NF-e (leiaute 4.0)

- **`_prepare_nfe_emission()`**: Mapeia `l10n_br_fiscal.document` para `nfelib.Nfe`
- **Tags implementadas**:
  - `<ide>`: Identificação (UF, modelo 55, série, número, datas, finalidade)
  - `<emit>`: Emitente (CNPJ, razão social, endereço, IE, regime tributário)
  - `<dest>`: Destinatário (CPF/CNPJ, nome, endereço, IE)
  - `<det>`: Itens/produtos (código, descrição, NCM, CFOP, quantidades, valores)
  - `<total>`: Totalizadores (bases ICMS, valores impostos, total NF-e)
- **Cálculo de chave de acesso**: 44 dígitos com DV (dígito verificador)

### 2. Assinatura digital

- Utiliza **erpbrasil.assinatura** para assinar XML com certificado A1
- Integra com `l10n_br_fiscal.certificate` para obter certificado da empresa
- Valida presença e validade do certificado antes da emissão

### 3. Transmissão à SEFAZ

- **`action_emit_nfe()`**: Botão de ação para emitir NF-e
- Fluxo: validação → XML → NFeAdapter.processar_documento (assina e envia)
- Usa `nfelib.nfe.ws.edoc_legacy.NFeAdapter` com `TransmissaoSOAP`
- Ambiente (Produção/Homologação) via `company.nfe_environment` (aba NF-e na empresa)

### 4. Validações

- Empresa com CNPJ configurado
- Destinatário válido
- Itens no documento
- Certificado digital disponível e válido

### 5. Interface

- Botão "Emitir NF-e" na view de documento fiscal
- Campos: Chave NF-e, Protocolo, XML, XML Assinado
- Notificação de sucesso/erro após emissão

### 6. Consultar Notas Recebidas

- Integra com **l10n_br_fiscal_dfe** (distribuição DFe)
- Menu: **Fiscal** → **Documentos** → **Consultar Notas Recebidas**
- Busca NF-e em que a empresa é destinatária no webservice da SEFAZ
- Importa procNFe automaticamente como documento fiscal de entrada
- XML armazenado como anexo em cada documento

## Dependências

| Módulo/Biblioteca | Tipo | Descrição |
|-------------------|------|-----------|
| l10n_br_base | Odoo | Base localização BR (CNPJ, CPF, cidades) |
| l10n_br_fiscal_certificate | Odoo | Gerenciamento certificado A1 |
| l10n_br_fiscal | Odoo | Motor fiscal (documentos, impostos, CFOP) |
| l10n_br_nfe_spec | Odoo | Modelos abstratos NF-e (leiaute 4.0) |
| l10n_br_fiscal_dfe | Odoo | Consulta distribuição DFe (notas recebidas) |
| erpbrasil.base | Python | Validação e utilitários fiscais |
| erpbrasil.assinatura | Python | Assinatura digital XML |
| erpbrasil.transmissao | Python | Comunicação SOAP com SEFAZ |
| nfelib | Python | Bindings XML NF-e (leiaute 4.0) |

## Instalação

```bash
# 1. Build da imagem com dependências Python
docker compose build web

# 2. Subir containers
docker compose up -d

# 3. Instalar módulos pela interface (Apps) ou via CLI, nesta ordem:
#    1. uom_alias
#    2. l10n_br_base
#    3. l10n_br_fiscal_certificate (⚠️ obrigatório para certificado A1)
#    4. l10n_br_fiscal
#    5. l10n_br_nfe_spec
#    6. l10n_br_fiscal_dfe (consulta notas recebidas)
#    7. l10n_nfe_emissao
```

## Uso

1. **Configurar empresa** (Configurações > Empresas):
   - CNPJ, IE, endereço completo
   - Regime tributário (tax_framework)
   - Impostos padrão (PIS/COFINS, ICMS, IPI)

2. **Configurar certificado digital**:
   - Menu: **Fiscal** (menu principal) → **Configuração** → **Certificados**
   - Requer perfil **Gestor Fiscal** (l10n_br_fiscal.group_manager)
   - Criar certificado tipo A1, anexar .pfx e informar senha

3. **Criar documento fiscal**:
   - Tipo: NF-e (modelo 55)
   - Preencher destinatário, itens, impostos

4. **Emitir NF-e**:
   - Botão "Emitir NF-e" no documento
   - XML gerado e assinado automaticamente
   - Transmissão à SEFAZ (a finalizar implementação)

5. **Consultar Notas Recebidas**:
   - Menu: **Fiscal** → **Documentos** → **Consultar Notas Recebidas**
   - Configurar DFe em Empresa → Aba Fiscal → Aba DF-e
   - Clicar "Search Documents" para buscar NF-e recebidas
   - NF-e importadas automaticamente como documentos de entrada

## Próximas implementações

- [ ] Consultar recibo assíncrono (quando SEFAZ retorna cStat 103)
- [ ] Parsing do retorno XML da SEFAZ
- [ ] Eventos: cancelamento, carta de correção
- [ ] Geração de DANFE (PDF) via BrazilFiscalReport
- [ ] Impostos detalhados (ICMS, IPI, PIS/COFINS) no XML

## Licença

AGPL-3
