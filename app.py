import re
import time
import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Gerenciador de Cadernos", layout="wide")

# ============================================================
# CONFIGURAÇÃO / CONEXÃO COM O GOOGLE SHEETS
# ============================================================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

WORKSHEET_DADOS = "Banco de questões"
WORKSHEET_EXCLUSOES = "Exclusoes"

# Colunas de dados (A:J) — as colunas K e L (Validação) NÃO são mais lidas nem
# gravadas pelo app: são calculadas em Python (ver calcular_validacoes), sempre
# do zero a cada carregamento, a partir dos dados reais em A:J. A planilha
# nunca é a fonte de verdade da validação.
COLS_DADOS = [
    "Programa", "Número da questão", "Banca", "Tipo (questão)", "Ano",
    "Concurso", "Assunto", "Código (caderno)", "Tipo (caderno)", "Direcionamento",
]
COL_VALID_CADERNO = "Validação (caderno)"
COL_VALID_PROGRAMA = "Validação (programa)"
COLS_TODAS = COLS_DADOS + [COL_VALID_CADERNO, COL_VALID_PROGRAMA]

COLS_EXCLUSOES = COLS_DADOS + ["Data Exclusão"]

CHAVE_OUTRO = "+ Outro (digitar)"

# Listas fechadas (validação + menu suspenso)
TIPO_QUESTAO_CANON = ["ABCDE", "C/E"]
TIPO_CADERNO_CANON = ["Questões", "Teste", "RevisãoFav"]
ANO_MIN = 2010

COR_REPETIDA = "#D93025"

PROMPT_IMPORTACAO = """Você vai me ajudar a transformar uma lista de questões de um caderno do TEC Concursos \
em uma tabela CSV com estas colunas EXATAS, nesta ordem (a primeira linha do CSV deve ser exatamente este \
cabeçalho):

Programa,Número da questão,Banca,Tipo (questão),Ano,Concurso,Assunto,Código (caderno),Tipo (caderno),Direcionamento

Regras:
- "Tipo (questão)": use exatamente "ABCDE" (múltipla escolha) ou "C/E" (certo/errado). Nunca outro valor.
- "Ano": 4 dígitos (ex.: 2024). Deixe vazio se não souber.
- "Tipo (caderno)": use exatamente um destes valores: Questões, Teste, RevisãoFav. Nunca outro valor.
- "Concurso": copie o texto tipo "CARGO (ÓRGÃO)/ÓRGÃO/ANO" que aparece no PDF/página da questão, sem o ano \
(o ano já vai na coluna Ano).
- Não invente dado nenhum: se não souber Banca, Ano, Concurso ou Assunto de alguma questão, deixe o campo vazio.
- Uma linha por questão, sem linhas em branco no meio.
- Não use vírgulas dentro dos valores sem colocar o campo entre aspas (formato CSV padrão).

Segue o conteúdo que preciso que você organize nesse formato:

[COLE AQUI O TEXTO OU PDF DO CADERNO]
"""


@st.cache_resource
def get_client():
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=SCOPES
    )
    return gspread.authorize(creds)


@st.cache_resource
def get_spreadsheet():
    client = get_client()
    return client.open_by_key(st.secrets["spreadsheet_id"])


def get_worksheet_dados():
    return get_spreadsheet().worksheet(WORKSHEET_DADOS)


def get_worksheet_exclusoes():
    ss = get_spreadsheet()
    try:
        return ss.worksheet(WORKSHEET_EXCLUSOES)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=WORKSHEET_EXCLUSOES, rows=1000, cols=len(COLS_EXCLUSOES))
        ws.update([COLS_EXCLUSOES])
        return ws


def calcular_validacoes(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula 'Validação (caderno)' e 'Validação (programa)' em Python (pandas),
    a partir dos dados atuais — não são mais fórmulas gravadas na planilha.
    Isso elimina de vez a classe de bug 'fórmula desatualizada em linha antiga':
    a cada carregamento, os dois campos são recalculados do zero a partir dos
    dados reais, então nunca podem ficar dessincronizados.

    REPETIDA em 'Validação (caderno)' = mesma questão duas vezes no MESMO
    caderno+direcionamento do MESMO programa (o TEC não gera cadernos assim —
    é sempre erro de cadastro).
    REPETIDA em 'Validação (programa)' = mesma questão aparece mais de uma vez
    dentro do MESMO Programa+Direcionamento (cruzando vários cadernos daquele
    direcionamento). Usar a mesma questão em direcionamentos DIFERENTES do
    mesmo programa é normal e não conta como repetida."""
    df = df.copy()
    num_vazio = df["Número da questão"].astype(str).str.strip() == ""

    chave_caderno = df[["Programa", "Número da questão", "Código (caderno)", "Direcionamento"]].astype(str).apply(lambda s: s.str.strip())
    cont_caderno = chave_caderno.groupby(list(chave_caderno.columns))["Programa"].transform("size")

    chave_programa = df[["Programa", "Número da questão", "Direcionamento"]].astype(str).apply(lambda s: s.str.strip())
    cont_programa = chave_programa.groupby(list(chave_programa.columns))["Programa"].transform("size")

    df[COL_VALID_CADERNO] = "OK"
    df.loc[cont_caderno.values > 1, COL_VALID_CADERNO] = "REPETIDA"
    df.loc[num_vazio, COL_VALID_CADERNO] = "-"

    df[COL_VALID_PROGRAMA] = "OK"
    df.loc[cont_programa.values > 1, COL_VALID_PROGRAMA] = "REPETIDA"
    df.loc[num_vazio, COL_VALID_PROGRAMA] = "-"

    return df


@st.cache_data(ttl=30)
def carregar_dados() -> pd.DataFrame:
    ws = get_worksheet_dados()
    valores = ws.get_all_values()
    if not valores or len(valores) < 1:
        return pd.DataFrame(columns=COLS_TODAS)
    header = valores[0]
    linhas = valores[1:]
    df = pd.DataFrame(linhas, columns=header)
    for c in COLS_DADOS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLS_DADOS]
    # linha real na planilha (1-based, +2 porque a linha 1 é o header e o índice do df começa em 0)
    df.insert(0, "_linha", range(2, 2 + len(df)))
    df = calcular_validacoes(df)
    df = df[["_linha"] + COLS_TODAS]
    return df


def limpar_cache():
    carregar_dados.clear()


def proxima_linha_livre() -> int:
    df = carregar_dados()
    return int(df["_linha"].max()) + 1 if len(df) else 2


def adicionar_questao(dados: dict):
    ws = get_worksheet_dados()
    linha = proxima_linha_livre()
    valores = [str(dados.get(c, "")) for c in COLS_DADOS]
    ws.update(f"A{linha}:J{linha}", [valores], value_input_option="USER_ENTERED")
    limpar_cache()


TAMANHO_LOTE_IMPORTACAO = 50


def _update_com_retry(ws, range_name: str, values: list, tentativas: int = 3):
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            ws.update(range_name, values, value_input_option="USER_ENTERED")
            return
        except gspread.exceptions.APIError as e:
            ultimo_erro = e
            if tentativa < tentativas:
                time.sleep(2 * tentativa)
    raise RuntimeError(
        f"Falha ao escrever no intervalo {range_name} após {tentativas} tentativas. "
        f"Detalhe da API: {ultimo_erro}"
    ) from ultimo_erro


def adicionar_questoes_em_lote(linhas_dados: list):
    """linhas_dados: lista de listas, já na ordem de COLS_DADOS.
    Escreve em lotes pequenos (com retry) em vez de um único range gigante,
    pra evitar timeouts/erros da API do Sheets em importações grandes."""
    if not linhas_dados:
        return
    ws = get_worksheet_dados()
    linha_inicial = proxima_linha_livre()

    for offset in range(0, len(linhas_dados), TAMANHO_LOTE_IMPORTACAO):
        bloco = linhas_dados[offset : offset + TAMANHO_LOTE_IMPORTACAO]
        l_ini = linha_inicial + offset
        l_fim = l_ini + len(bloco) - 1

        _update_com_retry(ws, f"A{l_ini}:J{l_fim}", bloco)

    limpar_cache()


CAMPOS_INTRINSECOS = ["Banca", "Tipo (questão)", "Ano", "Concurso", "Assunto"]
COL_LETRA = {
    "Programa": "A", "Número da questão": "B", "Banca": "C", "Tipo (questão)": "D",
    "Ano": "E", "Concurso": "F", "Assunto": "G", "Código (caderno)": "H",
    "Tipo (caderno)": "I", "Direcionamento": "J",
}


def preencher_dados_faltantes() -> dict:
    """Usa 'Número da questão' como chave única em TODO o banco (todos os
    programas) pra preencher campos intrínsecos da questão (Banca, Tipo
    (questão), Ano, Concurso, Assunto) que estejam vazios, aproveitando
    valores já cadastrados em outra linha com o mesmo número. Só preenche
    quando o valor é inequívoco (um único valor distinto entre todas as
    ocorrências não-vazias daquele número) — se houver conflito real de
    dados entre ocorrências, não mexe e deixa pra revisão manual."""
    df_atual = carregar_dados()
    atualizacoes = []
    resumo = {campo: 0 for campo in CAMPOS_INTRINSECOS}
    detalhes = []

    for campo in CAMPOS_INTRINSECOS:
        preenchidos = df_atual[df_atual[campo].astype(str).str.strip() != ""]
        candidatos = preenchidos.groupby("Número da questão")[campo].apply(
            lambda s: sorted(set(v.strip() for v in s))
        )
        mapa_valor_unico = {num: vals[0] for num, vals in candidatos.items() if len(vals) == 1}

        vazios = df_atual[df_atual[campo].astype(str).str.strip() == ""]
        for _, row in vazios.iterrows():
            num = row["Número da questão"]
            if num in mapa_valor_unico:
                valor = mapa_valor_unico[num]
                linha = int(row["_linha"])
                col_letra = COL_LETRA[campo]
                atualizacoes.append({"range": f"{col_letra}{linha}", "values": [[valor]]})
                resumo[campo] += 1
                detalhes.append(f"Linha {linha} (nº {num}): {campo} = '{valor}'")

    if atualizacoes:
        ws = get_worksheet_dados()
        ws.batch_update(atualizacoes, value_input_option="USER_ENTERED")
        limpar_cache()

    return {"resumo": resumo, "detalhes": detalhes, "total": len(atualizacoes)}


def editar_questao(linha: int, dados: dict):
    ws = get_worksheet_dados()
    valores = [str(dados.get(c, "")) for c in COLS_DADOS]
    ws.update(f"A{linha}:J{linha}", [valores], value_input_option="USER_ENTERED")
    limpar_cache()


def excluir_questao(linha: int, row: pd.Series):
    ws_exc = get_worksheet_exclusoes()
    registro = [str(row.get(c, "")) for c in COLS_DADOS] + [datetime.now().strftime("%d/%m/%Y %H:%M")]
    ws_exc.append_row(registro, value_input_option="USER_ENTERED")

    ws = get_worksheet_dados()
    ws.delete_rows(linha)
    limpar_cache()


def opcoes_com_outro(valores_existentes, extra_canon=None, chave_outro=CHAVE_OUTRO):
    base = {v.strip() for v in valores_existentes if str(v).strip() != ""}
    if extra_canon:
        base |= set(extra_canon)
    return [""] + sorted(base) + [chave_outro]


def resolver_valor_final(selecionado, digitado, chave_outro=CHAVE_OUTRO):
    if selecionado == chave_outro:
        return digitado.strip()
    return selecionado


def validar_ano(ano_str) -> bool:
    s = str(ano_str).strip()
    if not s:
        return True
    if not re.match(r"^\d{4}$", s):
        return False
    return ANO_MIN <= int(s) <= datetime.now().year


def relatorio_direcionamento(df_base: pd.DataFrame, numero: str, programa: str, direcionamento: str) -> pd.DataFrame:
    sub = df_base[
        (df_base["Número da questão"] == numero)
        & (df_base["Programa"] == programa)
        & (df_base["Direcionamento"] == direcionamento)
    ]
    return sub[["Código (caderno)", "Tipo (caderno)", "Direcionamento"]].drop_duplicates().reset_index(drop=True)


def relatorio_programa(df_base: pd.DataFrame, numero: str, programa: str) -> pd.DataFrame:
    sub = df_base[(df_base["Número da questão"] == numero) & (df_base["Programa"] == programa)]
    return sub[["Código (caderno)", "Tipo (caderno)", "Direcionamento"]].drop_duplicates().reset_index(drop=True)


def relatorio_global(df_base: pd.DataFrame, numero: str) -> pd.DataFrame:
    sub = df_base[df_base["Número da questão"] == numero]
    return sub[["Programa", "Código (caderno)", "Tipo (caderno)", "Direcionamento"]].drop_duplicates().reset_index(drop=True)


def grafico_barra_horizontal(serie: pd.Series, top_n: int = None):
    s = serie
    if top_n:
        s = s.head(top_n)
    if s.empty:
        st.info("Sem dados suficientes.")
        return
    dfc = s.reset_index()
    dfc.columns = ["categoria", "quantidade"]
    altura = min(26 * len(dfc) + 20, 420)
    chart = (
        alt.Chart(dfc)
        .mark_bar(color="#4C78A8", size=14, cornerRadiusEnd=2)
        .encode(
            x=alt.X("quantidade:Q", title=None, axis=alt.Axis(grid=False)),
            y=alt.Y("categoria:N", sort="-x", title=None),
            tooltip=["categoria", "quantidade"],
        )
        .properties(height=altura)
        .configure_view(strokeWidth=0)
        .configure_axis(domain=False)
    )
    st.altair_chart(chart, use_container_width=True)


def estilizar_validacao(df_exibir: pd.DataFrame):
    def cor(val):
        return f"color: {COR_REPETIDA}; font-weight: 600" if val == "REPETIDA" else ""

    colunas_alvo = [c for c in (COL_VALID_CADERNO, COL_VALID_PROGRAMA) if c in df_exibir.columns]
    styler = df_exibir.style
    aplicar = getattr(styler, "map", None) or styler.applymap
    return aplicar(cor, subset=colunas_alvo)


# ============================================================
# CARREGAMENTO
# ============================================================
try:
    df = carregar_dados()
except Exception as e:
    st.error(
        "Não consegui conectar à planilha. Confira em Settings → Secrets se "
        "`gcp_service_account` e `spreadsheet_id` estão configurados, e se a "
        "planilha foi compartilhada com o e-mail da service account."
    )
    st.exception(e)
    st.stop()

# ============================================================
# ALERTA GLOBAL — VALIDAÇÃO (CADERNO)
# ============================================================
# "Validação (caderno)" não fica mais visível como coluna normal (é
# irrelevante no dia a dia, já que o TEC nunca gera cadernos com duplicata
# interna), mas continua sendo um duplocheck importante de erro de
# cadastro — por isso vira um alerta destacado, olhando o banco inteiro.
_repetidas_caderno = df[df[COL_VALID_CADERNO] == "REPETIDA"]
if not _repetidas_caderno.empty:
    st.error(
        f"⚠️ {len(_repetidas_caderno)} questão(ões) com **Validação (caderno) = REPETIDA** "
        "— mesma questão duas vezes no mesmo caderno+direcionamento do mesmo programa. "
        "O TEC não gera cadernos assim: isso é sempre erro de cadastro."
    )
    with st.expander("Ver questões com Validação (caderno) = REPETIDA"):
        st.dataframe(
            _repetidas_caderno[
                ["Programa", "Número da questão", "Código (caderno)", "Direcionamento", "_linha"]
            ],
            hide_index=True, use_container_width=True,
        )

# ============================================================
# CABEÇALHO + FILTRO DE PROGRAMA (multi-programa)
# ============================================================
st.title("📚 Gerenciador de Cadernos")

programas_disponiveis = sorted({p.strip() for p in df["Programa"] if str(p).strip() != ""})
if programas_disponiveis:
    programa_atual = st.selectbox("Programa", programas_disponiveis, index=0)
    df_prog = df[df["Programa"] == programa_atual].copy()
else:
    programa_atual = ""
    df_prog = df.copy()
    st.info("Nenhuma questão cadastrada ainda. Use a aba 'Adicionar' para começar.")

aba_ver, aba_pesq, aba_add, aba_import, aba_rel = st.tabs(
    ["🔍 Visualizar & Editar", "🔎 Pesquisar", "➕ Adicionar", "📥 Importar em lote", "📊 Relatórios"]
)

# ============================================================
# ABA 1 — VISUALIZAR & EDITAR
# ============================================================
with aba_ver:
    busca = st.text_input("Buscar (qualquer campo):", key="busca_texto")

    fc1, fc2, fc3 = st.columns(3)
    f_direc = fc1.multiselect("Direcionamento", sorted({v for v in df_prog["Direcionamento"] if v}))
    f_banca = fc2.multiselect("Banca", sorted({v for v in df_prog["Banca"] if v}))
    opcoes_tipo_cad_filtro = sorted(set(v for v in df_prog["Tipo (caderno)"] if v) | set(TIPO_CADERNO_CANON))
    f_tipocad = fc3.multiselect("Tipo (caderno)", opcoes_tipo_cad_filtro)

    fc4, fc5 = st.columns(2)
    f_codigo = fc4.multiselect("Código (caderno)", sorted({v for v in df_prog["Código (caderno)"] if v}))
    f_valid = fc5.multiselect(
        "Validação (programa)",
        ["OK", "REPETIDA"],
        help=(
            "É proibido ter a mesma questão duas vezes no mesmo caderno+direcionamento "
            "(o próprio TEC não gera cadernos assim). Este filtro olha só a repetição "
            "dentro do Programa+Direcionamento, cruzando vários cadernos."
        ),
    )

    df_filt = df_prog.copy()
    if busca:
        mask = df_filt.apply(lambda r: r.astype(str).str.contains(busca, case=False).any(), axis=1)
        df_filt = df_filt[mask]
    if f_direc:
        df_filt = df_filt[df_filt["Direcionamento"].isin(f_direc)]
    if f_banca:
        df_filt = df_filt[df_filt["Banca"].isin(f_banca)]
    if f_tipocad:
        df_filt = df_filt[df_filt["Tipo (caderno)"].isin(f_tipocad)]
    if f_codigo:
        df_filt = df_filt[df_filt["Código (caderno)"].isin(f_codigo)]
    if f_valid:
        df_filt = df_filt[df_filt[COL_VALID_PROGRAMA].isin(f_valid)]

    st.caption(f"{len(df_filt)} de {len(df_prog)} questões exibidas (programa: {programa_atual or '—'}).")

    colunas_exibir = [c for c in COLS_TODAS if c not in ("Programa", COL_VALID_CADERNO)]
    selecao = st.dataframe(
        estilizar_validacao(df_filt[colunas_exibir]),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="tabela_principal",
    )

    if len(selecao.selection.rows) > 0:
        idx = df_filt.index[selecao.selection.rows[0]]
        row = df.loc[idx]
        linha_sheet = int(row["_linha"])
        numero_row = row["Número da questão"]

        if st.session_state.get("linha_selecionada") != linha_sheet:
            st.session_state["linha_selecionada"] = linha_sheet
            st.session_state["modo_edicao"] = False

        st.divider()
        st.markdown(f"### 📌 Questão nº {numero_row}")
        st.write(
            f"**Banca:** {row['Banca'] or '—'}   |   **Ano:** {row['Ano'] or '—'}   |   "
            f"**Concurso:** {row['Concurso'] or '—'}"
        )
        st.write(f"**Assunto:** {row['Assunto'] or '—'}")

        st.markdown("#### 🔁 Reaproveitamento")
        col_r1, col_r2 = st.columns(2)
        with col_r1:
            st.caption(f"Neste direcionamento ({row['Direcionamento'] or '—'})")
            st.dataframe(
                relatorio_direcionamento(df, numero_row, row["Programa"], row["Direcionamento"]),
                hide_index=True, use_container_width=True,
            )
        with col_r2:
            st.caption("Neste programa (todos os direcionamentos)")
            st.dataframe(
                relatorio_programa(df, numero_row, row["Programa"]),
                hide_index=True, use_container_width=True,
            )

        st.markdown("#### 🌐 Em todos os programas")
        st.dataframe(
            relatorio_global(df, numero_row),
            hide_index=True, use_container_width=True,
        )

        st.divider()
        cbtn1, cbtn2, _ = st.columns([1, 1, 3])
        if cbtn1.button("✏️ Editar", key=f"btn_editar_{linha_sheet}"):
            st.session_state["modo_edicao"] = True
        if cbtn2.button("🗑️ Excluir", key=f"btn_excluir_{linha_sheet}"):
            excluir_questao(linha_sheet, row)
            st.session_state["modo_edicao"] = False
            st.warning("Questão excluída (registro guardado na aba Exclusoes).")
            st.rerun()

        if st.session_state.get("modo_edicao"):
            st.markdown("#### ✏️ Editando")

            c1, c2, c3 = st.columns(3)

            op_direc = opcoes_com_outro(df["Direcionamento"].unique())
            idx_d = op_direc.index(row["Direcionamento"]) if row["Direcionamento"] in op_direc else 0
            e_direc_sel = c1.selectbox("Direcionamento", op_direc, index=idx_d, key=f"e_direc_{linha_sheet}")
            e_direc_novo = ""
            if e_direc_sel == CHAVE_OUTRO:
                e_direc_novo = c1.text_input("Novo direcionamento:", key=f"e_direc_novo_{linha_sheet}")

            op_banca = opcoes_com_outro(df["Banca"].unique())
            idx_b = op_banca.index(row["Banca"]) if row["Banca"] in op_banca else 0
            e_banca_sel = c2.selectbox("Banca", op_banca, index=idx_b, key=f"e_banca_{linha_sheet}")
            e_banca_novo = ""
            if e_banca_sel == CHAVE_OUTRO:
                e_banca_novo = c2.text_input("Nova banca:", key=f"e_banca_novo_{linha_sheet}")

            idx_tc = TIPO_CADERNO_CANON.index(row["Tipo (caderno)"]) if row["Tipo (caderno)"] in TIPO_CADERNO_CANON else 0
            e_tipocad = c3.selectbox("Tipo (caderno)", TIPO_CADERNO_CANON, index=idx_tc, key=f"e_tipocad_{linha_sheet}")

            op_assunto = opcoes_com_outro(df["Assunto"].unique())
            idx_a = op_assunto.index(row["Assunto"]) if row["Assunto"] in op_assunto else 0
            e_assunto_sel = st.selectbox("Assunto", op_assunto, index=idx_a, key=f"e_assunto_{linha_sheet}")
            e_assunto_novo = ""
            if e_assunto_sel == CHAVE_OUTRO:
                e_assunto_novo = st.text_input("Novo assunto:", key=f"e_assunto_novo_{linha_sheet}")

            with st.form(f"form_editar_{linha_sheet}"):
                c4, c5, c6 = st.columns(3)
                e_num = c4.text_input("Número da questão", value=str(row["Número da questão"]))
                idx_tq = TIPO_QUESTAO_CANON.index(row["Tipo (questão)"]) if row["Tipo (questão)"] in TIPO_QUESTAO_CANON else 0
                e_tipoq = c5.selectbox("Tipo (questão)", TIPO_QUESTAO_CANON, index=idx_tq)
                e_ano = c6.text_input("Ano", value=str(row["Ano"]))

                c7, c8 = st.columns(2)
                e_codigo = c7.text_input("Código (caderno)", value=str(row["Código (caderno)"]))
                e_concurso = c8.text_input("Concurso", value=str(row["Concurso"]))

                col_b1, col_b2, col_b3 = st.columns([1, 1, 2])
                btn_salvar = col_b1.form_submit_button("💾 Salvar alterações")
                btn_cancelar = col_b2.form_submit_button("Cancelar")

                if btn_salvar:
                    e_assunto = resolver_valor_final(e_assunto_sel, e_assunto_novo)
                    dados = {
                        "Programa": row["Programa"],
                        "Número da questão": e_num.strip(),
                        "Banca": resolver_valor_final(e_banca_sel, e_banca_novo),
                        "Tipo (questão)": e_tipoq,
                        "Ano": e_ano.strip(),
                        "Concurso": e_concurso.strip(),
                        "Assunto": e_assunto,
                        "Código (caderno)": e_codigo.strip(),
                        "Tipo (caderno)": e_tipocad,
                        "Direcionamento": resolver_valor_final(e_direc_sel, e_direc_novo),
                    }
                    if not dados["Número da questão"]:
                        st.error("⚠️ Número da questão é obrigatório.")
                    elif not validar_ano(dados["Ano"]):
                        st.error(f"⚠️ Ano inválido. Use 4 dígitos entre {ANO_MIN} e {datetime.now().year}, ou deixe vazio.")
                    else:
                        editar_questao(linha_sheet, dados)
                        st.session_state["modo_edicao"] = False
                        st.success("Alterações salvas!")
                        st.rerun()

                if btn_cancelar:
                    st.session_state["modo_edicao"] = False
                    st.rerun()

# ============================================================
# ABA 2 — PESQUISAR
# ============================================================
with aba_pesq:
    st.subheader("🔎 Pesquisar questão")
    st.caption("Digite o número da questão para ver o relatório completo de reaproveitamento, em qualquer programa.")
    num_busca = st.text_input("Número da questão", key="pesquisa_num")

    if num_busca.strip():
        sub = df[df["Número da questão"] == num_busca.strip()]
        if sub.empty:
            st.warning("Nenhuma questão encontrada com esse número.")
        else:
            base = sub.iloc[0]
            st.write(
                f"**Banca:** {base['Banca'] or '—'}   |   **Ano:** {base['Ano'] or '—'}   |   "
                f"**Concurso:** {base['Concurso'] or '—'}"
            )
            st.write(f"**Assunto:** {base['Assunto'] or '—'}")

            for prog_val in sorted(sub["Programa"].unique()):
                st.markdown(f"#### 📁 Programa: {prog_val}")
                direcs = sorted(sub[sub["Programa"] == prog_val]["Direcionamento"].unique())
                for d in direcs:
                    st.caption(f"Direcionamento: {d or '—'}")
                    st.dataframe(
                        relatorio_direcionamento(df, num_busca.strip(), prog_val, d),
                        hide_index=True, use_container_width=True,
                    )
                st.caption("Neste programa (todos os direcionamentos)")
                st.dataframe(
                    relatorio_programa(df, num_busca.strip(), prog_val),
                    hide_index=True, use_container_width=True,
                )

            st.markdown("#### 🌐 Em todos os programas")
            st.dataframe(
                relatorio_global(df, num_busca.strip()),
                hide_index=True, use_container_width=True,
            )

# ============================================================
# ABA 3 — ADICIONAR
# ============================================================
with aba_add:
    st.subheader("➕ Cadastrar nova questão")

    c1, c2, c3 = st.columns(3)

    op_prog = opcoes_com_outro(df["Programa"].unique())
    idx_p = op_prog.index(programa_atual) if programa_atual in op_prog else 0
    n_prog_sel = c1.selectbox("Programa", op_prog, index=idx_p, key="add_programa")
    n_prog_novo = ""
    if n_prog_sel == CHAVE_OUTRO:
        n_prog_novo = c1.text_input("Novo programa:", key="add_programa_novo")

    op_direc_add = opcoes_com_outro(df["Direcionamento"].unique())
    n_direc_sel = c2.selectbox("Direcionamento", op_direc_add, key="add_direc")
    n_direc_novo = ""
    if n_direc_sel == CHAVE_OUTRO:
        n_direc_novo = c2.text_input("Novo direcionamento:", key="add_direc_novo")

    n_tipocad = c3.selectbox("Tipo (caderno)", TIPO_CADERNO_CANON, key="add_tipocad")

    c9, c10 = st.columns(2)
    op_banca_add = opcoes_com_outro(df["Banca"].unique())
    n_banca_sel = c9.selectbox("Banca", op_banca_add, key="add_banca")
    n_banca_novo = ""
    if n_banca_sel == CHAVE_OUTRO:
        n_banca_novo = c9.text_input("Nova banca:", key="add_banca_novo")

    op_assunto_add = opcoes_com_outro(df["Assunto"].unique())
    n_assunto_sel = c10.selectbox("Assunto", op_assunto_add, key="add_assunto")
    n_assunto_novo = ""
    if n_assunto_sel == CHAVE_OUTRO:
        n_assunto_novo = c10.text_input("Novo assunto:", key="add_assunto_novo")

    with st.form("form_adicionar", clear_on_submit=True):
        c4, c5, c6 = st.columns(3)
        n_num = c4.text_input("Número da questão*")
        n_tipoq = c5.selectbox("Tipo (questão)", TIPO_QUESTAO_CANON)
        n_ano = c6.text_input("Ano (4 dígitos, ou vazio)")

        c7, c8 = st.columns(2)
        n_codigo = c7.text_input("Código (caderno)*")
        n_concurso = c8.text_input("Concurso")

        st.caption("* Campos obrigatórios.")

        if st.form_submit_button("🚀 Adicionar questão"):
            n_prog = resolver_valor_final(n_prog_sel, n_prog_novo)
            n_direc = resolver_valor_final(n_direc_sel, n_direc_novo)
            n_banca = resolver_valor_final(n_banca_sel, n_banca_novo)
            n_assunto = resolver_valor_final(n_assunto_sel, n_assunto_novo)

            if not n_num.strip() or not n_codigo.strip() or not n_prog.strip():
                st.error("⚠️ Programa, Número da questão e Código (caderno) são obrigatórios.")
            elif not validar_ano(n_ano):
                st.error(f"⚠️ Ano inválido. Use 4 dígitos entre {ANO_MIN} e {datetime.now().year}, ou deixe vazio.")
            else:
                ja_existe = (
                    (df["Número da questão"] == n_num.strip())
                    & (df["Código (caderno)"] == n_codigo.strip())
                    & (df["Direcionamento"] == n_direc)
                ).any()
                dados = {
                    "Programa": n_prog.strip(),
                    "Número da questão": n_num.strip(),
                    "Banca": n_banca.strip(),
                    "Tipo (questão)": n_tipoq,
                    "Ano": n_ano.strip(),
                    "Concurso": n_concurso.strip(),
                    "Assunto": n_assunto.strip(),
                    "Código (caderno)": n_codigo.strip(),
                    "Tipo (caderno)": n_tipocad,
                    "Direcionamento": n_direc.strip(),
                }
                adicionar_questao(dados)
                if ja_existe:
                    st.warning(
                        "Questão adicionada, mas já existia outra com o mesmo número, "
                        "código e direcionamento — confira se não é um erro de cadastro."
                    )
                else:
                    st.success("Questão adicionada com sucesso!")
                st.rerun()

# ============================================================
# ABA 4 — IMPORTAR EM LOTE
# ============================================================
with aba_import:
    st.subheader("📥 Importar questões em lote (CSV ou Excel)")

    with st.expander("🔧 Manutenção — preencher dados faltantes usando o resto do banco"):
        st.caption(
            "Usa 'Número da questão' como chave única em todo o banco (todos os programas) "
            "para preencher Banca, Tipo (questão), Ano, Concurso e Assunto vazios, quando "
            "já existir um valor inequívoco cadastrado em outra linha com o mesmo número. "
            "Não mexe em nada se houver conflito de dados entre ocorrências do mesmo número."
        )
        if st.button("🔍 Verificar e preencher dados faltantes"):
            with st.spinner("Cruzando questões e preenchendo o que for possível..."):
                resultado = preencher_dados_faltantes()
            if resultado["total"] == 0:
                st.info("Nada para preencher — não achei nenhum campo vazio com valor inequívoco disponível.")
            else:
                st.success(f"{resultado['total']} campo(s) preenchido(s).")
                st.write(
                    ", ".join(f"{campo}: {qtd}" for campo, qtd in resultado["resumo"].items() if qtd > 0)
                )
                with st.expander("Ver detalhes do que foi preenchido"):
                    for linha_txt in resultado["detalhes"]:
                        st.write(f"- {linha_txt}")
                st.rerun()

    with st.expander("📋 Prompt pronto pra pedir a uma IA formatar os dados antes de importar"):
        st.code(PROMPT_IMPORTACAO, language="markdown")
        st.caption("Copie este texto, cole numa IA junto com o conteúdo do caderno, e use o CSV que ela devolver.")

    arquivo = st.file_uploader("Arquivo CSV ou Excel", type=["csv", "xlsx", "xls"])

    if arquivo is not None:
        df_novo = None
        try:
            if arquivo.name.lower().endswith(".csv"):
                df_novo = pd.read_csv(arquivo, dtype=str).fillna("")
            else:
                df_novo = pd.read_excel(arquivo, dtype=str).fillna("")
        except Exception as e:
            st.error(f"Não consegui ler o arquivo: {e}")

        if df_novo is not None:
            faltando = [c for c in COLS_DADOS if c not in df_novo.columns]
            if faltando:
                st.error(f"Faltam colunas obrigatórias no arquivo: {', '.join(faltando)}")
            else:
                df_novo = df_novo[COLS_DADOS].astype(str).apply(lambda s: s.str.strip())

                erros = []
                for i, r in df_novo.iterrows():
                    linha_n = i + 2
                    if not r["Programa"]:
                        erros.append(f"Linha {linha_n}: Programa vazio.")
                    if not r["Número da questão"]:
                        erros.append(f"Linha {linha_n}: Número da questão vazio.")
                    if not r["Código (caderno)"]:
                        erros.append(f"Linha {linha_n}: Código (caderno) vazio.")
                    if r["Tipo (questão)"] and r["Tipo (questão)"] not in TIPO_QUESTAO_CANON:
                        erros.append(
                            f"Linha {linha_n}: Tipo (questão) inválido ('{r['Tipo (questão)']}') — "
                            f"use um destes: {', '.join(TIPO_QUESTAO_CANON)}."
                        )
                    if r["Tipo (caderno)"] and r["Tipo (caderno)"] not in TIPO_CADERNO_CANON:
                        erros.append(
                            f"Linha {linha_n}: Tipo (caderno) inválido ('{r['Tipo (caderno)']}') — "
                            f"use um destes: {', '.join(TIPO_CADERNO_CANON)}."
                        )
                    if not validar_ano(r["Ano"]):
                        erros.append(f"Linha {linha_n}: Ano inválido ('{r['Ano']}').")

                st.caption(f"{len(df_novo)} linha(s) no arquivo.")
                st.dataframe(df_novo, use_container_width=True, hide_index=True)

                if erros:
                    st.error(f"{len(erros)} problema(s) encontrado(s) — corrija o arquivo e suba de novo:")
                    for e in erros[:40]:
                        st.write(f"- {e}")
                else:
                    st.success("Nenhum problema encontrado nas colunas obrigatórias.")
                    if st.button("🚀 Confirmar importação"):
                        with st.spinner(f"Importando {len(df_novo)} questões em lotes de {TAMANHO_LOTE_IMPORTACAO}..."):
                            try:
                                adicionar_questoes_em_lote(df_novo.values.tolist())
                            except RuntimeError as e:
                                st.error(f"⚠️ Importação falhou no meio do caminho: {e}")
                                st.info(
                                    "As linhas já escritas antes da falha permanecem na planilha — "
                                    "confira na aba Visualizar & Editar antes de tentar de novo, para não duplicar."
                                )
                                st.stop()
                        st.success(f"{len(df_novo)} questões importadas com sucesso!")
                        st.rerun()

# ============================================================
# ABA 5 — RELATÓRIOS
# ============================================================
with aba_rel:
    modo_rel = st.radio("Escopo do relatório", ["Por Programa", "Geral (todos os programas)"], horizontal=True)

    if modo_rel == "Por Programa":
        st.subheader(f"📊 Relatório — {programa_atual or '—'}")

        m1, m2 = st.columns(2)
        m1.metric("Total de questões", len(df_prog))
        m2.metric("Direcionamentos", df_prog["Direcionamento"].nunique())

        st.markdown("#### Cobertura por Direcionamento")
        st.caption("Total de questões, distribuição por Banca e quantas já foram reaproveitadas de outro caderno do mesmo direcionamento.")
        if not df_prog.empty:
            pivot = df_prog.pivot_table(
                index="Direcionamento", columns="Banca", values="Número da questão",
                aggfunc="count", fill_value=0,
            )
            pivot.insert(0, "Total", pivot.sum(axis=1))
            reap = df_prog.groupby("Direcionamento")[COL_VALID_PROGRAMA].apply(lambda s: int((s == "REPETIDA").sum()))
            pivot["Reaproveitadas"] = reap
            st.dataframe(pivot, use_container_width=True)
        else:
            st.info("Sem dados para este programa.")

        st.markdown("---")
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.write("**Questões por Banca**")
            bancas_validas = df_prog[df_prog["Banca"].astype(str).str.strip() != ""]
            grafico_barra_horizontal(bancas_validas["Banca"].value_counts())
        with col_g2:
            st.write("**Questões por Ano**")
            anos_validos = df_prog[df_prog["Ano"].astype(str).str.strip() != ""]
            grafico_barra_horizontal(anos_validos["Ano"].value_counts().sort_index(ascending=False))

        col_g3, col_g4 = st.columns(2)
        with col_g3:
            st.write("**Top 10 Concursos**")
            cv = df_prog[df_prog["Concurso"].astype(str).str.strip() != ""]
            grafico_barra_horizontal(cv["Concurso"].value_counts(), top_n=10)
        with col_g4:
            st.write("**Top 10 Assuntos**")
            av = df_prog[df_prog["Assunto"].astype(str).str.strip() != ""]
            grafico_barra_horizontal(av["Assunto"].value_counts(), top_n=10)

        st.markdown("---")
        st.write("**🏆 Top 10 questões mais reaproveitadas**")
        contagem_q = df_prog["Número da questão"].value_counts().head(10)
        if not contagem_q.empty:
            detalhes = df_prog.drop_duplicates("Número da questão").set_index("Número da questão")
            cartoes = pd.DataFrame({
                "Número": contagem_q.index,
                "Vezes usada": contagem_q.values,
                "Banca": [detalhes.loc[n, "Banca"] for n in contagem_q.index],
                "Ano": [detalhes.loc[n, "Ano"] for n in contagem_q.index],
                "Concurso": [detalhes.loc[n, "Concurso"] for n in contagem_q.index],
                "Assunto": [detalhes.loc[n, "Assunto"] for n in contagem_q.index],
            })
            st.dataframe(cartoes, use_container_width=True, hide_index=True)
        else:
            st.info("Sem dados suficientes.")

        st.markdown("---")
        st.download_button(
            "⬇️ Baixar backup deste programa (CSV)",
            data=df_prog[COLS_TODAS].to_csv(index=False).encode("utf-8"),
            file_name=f"backup_{(programa_atual or 'todos').replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )

        st.write("#### 🗑️ Últimas exclusões")
        try:
            ws_exc = get_worksheet_exclusoes()
            valores_exc = ws_exc.get_all_values()
            if len(valores_exc) > 1:
                df_exc = pd.DataFrame(valores_exc[1:], columns=valores_exc[0])
                st.dataframe(df_exc.tail(10), use_container_width=True, hide_index=True)
            else:
                st.info("Nenhuma exclusão registrada ainda.")
        except Exception:
            st.info("Nenhuma exclusão registrada ainda.")

    else:
        st.subheader("📊 Relatório Geral — todos os programas")

        st.metric("Total de questões (todos os programas)", len(df))

        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.write("**Questões por Banca**")
            bancas_validas = df[df["Banca"].astype(str).str.strip() != ""]
            grafico_barra_horizontal(bancas_validas["Banca"].value_counts())
        with col_g2:
            st.write("**Questões por Ano**")
            anos_validos = df[df["Ano"].astype(str).str.strip() != ""]
            grafico_barra_horizontal(anos_validos["Ano"].value_counts().sort_index(ascending=False))

        st.markdown("---")
        st.write("**🏆 Top 10 questões mais reaproveitadas (geral)**")
        contagem_q_g = df["Número da questão"].value_counts().head(10)
        if not contagem_q_g.empty:
            detalhes_g = df.drop_duplicates("Número da questão").set_index("Número da questão")
            cartoes_g = pd.DataFrame({
                "Número": contagem_q_g.index,
                "Vezes usada": contagem_q_g.values,
                "Programa(s)": [
                    ", ".join(sorted(df[df["Número da questão"] == n]["Programa"].unique()))
                    for n in contagem_q_g.index
                ],
                "Banca": [detalhes_g.loc[n, "Banca"] for n in contagem_q_g.index],
                "Ano": [detalhes_g.loc[n, "Ano"] for n in contagem_q_g.index],
                "Concurso": [detalhes_g.loc[n, "Concurso"] for n in contagem_q_g.index],
            })
            st.dataframe(cartoes_g, use_container_width=True, hide_index=True)
        else:
            st.info("Sem dados suficientes.")
