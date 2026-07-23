# Gerenciador de Cadernos

App Streamlit para visualizar, filtrar, editar, adicionar e excluir questões do
banco de cadernos (multi-programa), com relatórios de cobertura por
direcionamento/banca/ano/concurso.

Os dados moram numa planilha Google Sheets (não neste repositório). O app lê e
escreve direto nela via uma **service account** do Google.

## Configuração (fazer uma vez, no Streamlit Community Cloud)

1. Publique este repositório no [share.streamlit.io](https://share.streamlit.io),
   apontando para `app.py`.
2. Em **Settings → Secrets** do app, cole:

   ```toml
   spreadsheet_id = "ID_DA_PLANILHA_AQUI"

   [gcp_service_account]
   type = "service_account"
   project_id = "..."
   private_key_id = "..."
   private_key = "..."
   client_email = "...@...iam.gserviceaccount.com"
   client_id = "..."
   auth_uri = "https://accounts.google.com/o/oauth2/auth"
   token_uri = "https://oauth2.googleapis.com/token"
   auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
   client_x509_cert_url = "..."
   ```

   Se você já tem uma service account configurada no app de Questões
   Comentadas (`meus-bizus-app`), pode reaproveitar exatamente os mesmos
   valores de `[gcp_service_account]` aqui — é a mesma conta, só muda o
   `spreadsheet_id`.

3. **Compartilhe a planilha** (`Banco de Cadernos`) com o e-mail que aparece em
   `client_email` do secrets acima, como **Editor**. Sem isso o app não
   consegue ler nem escrever.

4. A planilha precisa ter uma aba chamada exatamente **`Banco de questões`**
   com estas colunas, nesta ordem (A a L):

   ```
   Programa | Número da questão | Banca | Tipo (questão) | Ano | Concurso |
   Assunto | Código (caderno) | Tipo (caderno) | Direcionamento |
   Validação (caderno) | Validação (programa)
   ```

   As colunas **Validação (caderno)** e **Validação (programa)** devem conter
   fórmulas `COUNTIFS` (o app nunca escreve nelas ao editar — só lê; ao
   adicionar uma questão nova, o app escreve a fórmula certa automaticamente
   na linha nova).

   Uma aba **`Exclusoes`** é criada automaticamente pelo app na primeira
   exclusão, se ainda não existir.

## Múltiplos programas

Todos os programas vivem na mesma aba `Banco de questões`, diferenciados pela
coluna `Programa`. O app tem um seletor de Programa no topo. Não é preciso
criar planilha nova nem app novo para cada programa — só adicionar linhas com
o nome do novo Programa.

As "regrinhas" específicas de cada programa (ex.: quantos direcionamentos são
obrigatórios, quais valores de Tipo (caderno) são válidos) **não** são
aplicadas pelo app — isso continua sendo responsabilidade de quem registra os
dados (documentado no Prompt-Mestre de cada programa). O app é
intencionalmente genérico para não crescer em complexidade a cada programa
novo.
