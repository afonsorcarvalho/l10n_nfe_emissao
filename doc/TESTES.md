# Testes e Validação - l10n_nfe_emissao

Guia para testar o módulo de emissão de NF-e.

## Pré-requisitos

### 1. Módulos instalados (na ordem)

```bash
# Via linha de comando (mais rápido):
docker compose exec web odoo -i uom_alias,l10n_br_base,l10n_br_fiscal_certificate,l10n_br_fiscal,l10n_br_nfe_spec,l10n_nfe_emissao -d odoo --stop-after-init

# Ou via interface Odoo:
# Apps → Remover filtro "Apps" → Buscar e instalar um por um
```

Ordem correta:
1. `uom_alias` (OCA/product-attribute)
2. `l10n_br_base` (localização brasileira base)
3. `l10n_br_fiscal_certificate` (certificados digitais)
4. `l10n_br_fiscal` (motor fiscal)
5. `l10n_br_nfe_spec` (especificação NF-e leiaute 4.0)
6. `l10n_br_fiscal_dfe` (consulta distribuição DFe - notas recebidas)
7. `l10n_nfe_emissao` (módulo customizado)

### 2. Configurar empresa

**Menu**: Configurações → Empresas → Sua empresa

Dados obrigatórios:
- **CNPJ**: Ex: `59.594.315/0001-57`
- **Razão Social**: Nome completo da empresa
- **Inscrição Estadual**: IE da empresa
- **Regime Tributário** (`tax_framework`):
  - `1` = Simples Nacional
  - `3` = Regime Normal
- **Endereço completo**:
  - Logradouro, número, bairro
  - CEP, cidade (com código IBGE), UF

### 3. Configurar certificado digital A1

**Menu**: **Fiscal** (menu principal) → **Configuração** → **Certificados**  
(Requer perfil Gestor Fiscal.)

1. Criar novo certificado
2. Tipo: `A1`
3. Anexar arquivo `.pfx` (PKCS#12)
4. Informar senha do certificado
5. Validar datas de validade

### 4. Configurar impostos padrão

**Menu**: Faturamento → Configuração → Empresas → Aba "Fiscal"

- **PIS/COFINS padrão**
- **ICMS padrão** (conforme regime)
- **IPI padrão** (se aplicável)

### 5. Configurar DFe (para Consultar Notas Recebidas)

**Menu**: Configurações → Empresas → Sua empresa → Aba "Fiscal" → Aba "DF-e"

- **Versão DFe**: `1.01`
- **Ambiente**: `Produção` (1) ou `Homologação` (2)

## Teste 0: Consultar Notas Recebidas

Permite baixar NF-e em que a empresa é destinatária, via webservice de distribuição da SEFAZ.

1. **Menu**: Fiscal → Documentos → **Consultar Notas Recebidas** (ou DF-e)
2. Criar registro:
   - **Empresa**: Selecionar empresa com certificado configurado
   - **Last NSU**: Iniciar com `0` (primeira consulta)
3. Clicar **"Search Documents"** (Buscar Documentos)
4. As NF-e recebidas são importadas como documentos fiscais de entrada
5. O XML procNFe fica anexado a cada documento

**Nota**: Na primeira consulta use NSU `0`. Após a consulta, o NSU é atualizado automaticamente para a próxima.

## Teste 1: Validação de XML

### Criar documento fiscal de teste

1. **Menu**: Faturamento → Documentos Fiscais → Criar
2. **Dados básicos**:
   - Tipo de documento: `NF-e` (modelo 55)
   - Série: `1`
   - Número: `1` (sequencial)
   - Operação fiscal: Selecionar operação de venda
3. **Destinatário**:
   - Criar/selecionar parceiro com CPF ou CNPJ válido
   - Preencher endereço completo
4. **Itens**:
   - Adicionar produto com NCM e CFOP configurados
   - Quantidade e preço unitário

### Emitir NF-e (modo teste)

1. Clicar botão **"Emitir NF-e"** no topo do documento
2. **Resultado esperado**: Notificação verde "NF-e gerada"
3. **Verificações**:
   - Aba "Dados NF-e" aparece com campos preenchidos
   - Campo "XML NF-e": fazer download e validar estrutura
   - Campo "XML Assinado": verificar tag `<Signature>`

### Validar XML gerado

```bash
# Download do XML assinado do Odoo
# Salvar como nfe_test.xml

# Validar estrutura XML
xmllint --format nfe_test.xml

# Verificar assinatura digital
# (usar ferramenta validador SEFAZ ou erpbrasil.assinatura)
```

### Estrutura esperada do XML

```xml
<NFe xmlns="http://www.portalfiscal.inf.br/nfe">
  <infNFe versao="4.00" Id="NFe35...">
    <ide>
      <cUF>35</cUF>
      <cNF>00000001</cNF>
      <natOp>Venda</natOp>
      <mod>55</mod>
      <serie>1</serie>
      <nNF>1</nNF>
      <dhEmi>2025-02-02T...</dhEmi>
      <!-- ... -->
    </ide>
    <emit>
      <CNPJ>59594315000157</CNPJ>
      <xNome>Sua Empresa LTDA</xNome>
      <!-- ... -->
    </emit>
    <dest>
      <CPF>12345678901</CPF>
      <xNome>Cliente Teste</xNome>
      <!-- ... -->
    </dest>
    <det nItem="1">
      <prod>
        <cProd>PROD001</cProd>
        <xProd>Produto Teste</xProd>
        <!-- ... -->
      </prod>
      <imposto>
        <!-- ... -->
      </imposto>
    </det>
    <total>
      <ICMSTot>
        <vBC>100.00</vBC>
        <vNF>100.00</vNF>
        <!-- ... -->
      </ICMSTot>
    </total>
  </infNFe>
  <Signature xmlns="http://www.w3.org/2000/09/xmldsig#">
    <!-- Assinatura digital -->
  </Signature>
</NFe>
```

## Teste 2: Cálculo de chave de acesso

### Validar chave NF-e

A chave deve ter **44 dígitos** no formato:

```
[UF][AAMM][CNPJ][Mod][Serie][Numero][TpEmis][CodNum][DV]
 2    4    14     2    3      9       1       8      1  = 44 dígitos
```

Exemplo: `35250159594315000157550010000000011062777161`

### Verificar DV (Dígito Verificador)

O último dígito é calculado por módulo 11. Para validar:

```python
# No Python console do Odoo:
from odoo import api, SUPERUSER_ID

env = api.Environment(cr, SUPERUSER_ID, {})
doc = env['l10n_br_fiscal.document'].browse(1)  # ID do documento
chave_parcial = doc.nfe_key[:-1]  # Remove DV
dv_calculado = doc._calcular_dv_nfe(chave_parcial)
print(f"DV esperado: {doc.nfe_key[-1]}, calculado: {dv_calculado}")
```

## Teste 3: Transmissão SEFAZ (Homologação)

⚠️ **Status**: Implementação básica pronta, transmissão real a finalizar.

### Configuração para homologação

1. **Certificado válido**: A1 homologação SEFAZ
2. **Ambiente**: Alterar em `nfe_document.py`:
   ```python
   tpAmb="2",  # 2=Homologação
   ```

### Dados de teste SEFAZ

Para homologação, usar:
- **CNPJ destinatário**: `99.999.999/0001-91` (teste SEFAZ)
- **Produtos**: NCM e valores reais
- **Impostos**: Cálculos corretos mesmo em homologação

### Validar retorno

Retorno esperado da SEFAZ:
- **cStat = 100**: NF-e autorizada pela SEFAZ (protocolo retornado)
- **Protocolo**: 15 dígitos
- **Chave**: 44 dígitos
- **XML retorno**: Com tag `<protNFe>`

## Teste 4: Validações e erros

### Cenários de erro esperados

| Cenário | Erro esperado | Validação |
|---------|---------------|-----------|
| Empresa sem CNPJ | `ValidationError: CNPJ da empresa não configurado` | ✅ |
| Sem destinatário | `ValidationError: Destinatário não definido` | ✅ |
| Documento vazio | `ValidationError: Documento sem itens` | ✅ |
| Sem certificado | `ValidationError: Certificado digital A1 não encontrado` | ✅ |

### Testar cada validação

```python
# No Odoo shell:
doc = env['l10n_br_fiscal.document'].create({
    'document_type_id': tipo_nfe_id,
    # Omitir campos para testar validações
})
doc.action_emit_nfe()  # Deve retornar erro específico
```

## Logs e debug

### Verificar logs do Odoo

```bash
# Acompanhar logs em tempo real
docker compose logs -f web

# Filtrar por NF-e
docker compose logs web | grep -i "nfe\|emiss"
```

### Logs esperados na emissão

```
INFO l10n_nfe_emissao.nfe_document: Documento DOC/2025/0001 validado para emissão de NF-e
INFO l10n_nfe_emissao.transmissao: Transmissão NF-e simulada - implementar erpbrasil.transmissao.envia_lote_nfe
```

## Checklist completo

- [ ] Módulos instalados na ordem correta
- [ ] Empresa configurada (CNPJ, IE, endereço, regime)
- [ ] Certificado digital A1 válido cadastrado
- [ ] Impostos padrão configurados
- [ ] Documento fiscal criado com todos os dados
- [ ] Botão "Emitir NF-e" visível no documento
- [ ] XML gerado sem erros de estrutura
- [ ] XML assinado digitalmente (tag `<Signature>` presente)
- [ ] Chave de acesso com 44 dígitos e DV correto
- [ ] Logs sem erros críticos

## Solução de problemas

### Erro: "Certificado digital A1 não encontrado"

**Solução**: Faturamento → Config → Certificados → Criar certificado tipo A1

### Erro: "CNPJ da empresa não configurado"

**Solução**: Configurações → Empresas → Editar empresa → Preencher CNPJ

### Erro: "Invalid field parent"

**Solução**: Já corrigido em `views/res_company_view.xml`. Fazer upgrade do módulo.

### XML gerado mas não assinado

**Solução**: Verificar certificado (.pfx) anexado e senha correta

### Erro ao importar erpbrasil

**Solução**: Rebuild da imagem Docker:
```bash
docker compose build web
docker compose up -d
```

## Próxima fase: Transmissão real

Para conectar à SEFAZ e autorizar NF-e de verdade, finalizar em `models/nfe_transmissao.py`:

1. Implementar `transmitir_nfe()` com `erpbrasil.transmissao`
2. Consultar recibo assíncrono
3. Parse do retorno XML (protocolo, status)
4. Atualizar campos `nfe_key`, `nfe_protocol`, `state_edoc`
5. Criar evento de autorização em `l10n_br_fiscal.event`

Referência: https://github.com/erpbrasil/erpbrasil.transmissao
