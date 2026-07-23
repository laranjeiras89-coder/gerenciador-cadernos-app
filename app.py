import streamlit as st
import pandas as pd
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

# Colunas de dados (A:J) — as colunas K e L (Validação) têm fórmulas na planilha
# e NUNCA devem ser sobrescritas por este app.
COLS_DADOS = [
    "Programa", "Número da questão", "Banca", "Tipo (questão)", "Ano",
    "Concurso", "Assunto", "Código (caderno)", "Tipo (caderno)", "Direcionamento",
]
COL_VALID_CADERNO = "Validação (caderno)"
COL_VALID_PROGRAMA = "Validação (programa)"
COLS_TODAS = COLS_DADOS + [COL_VALID_CADERNO, COL_VALID_PROGRAMA]

COLS_EXCLUSOES = COLS_DADOS + ["Data Exclusão"]


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


def formula_validacao_caderno(linha: int) -> str:
    return (
        f'=IF($B{linha}="";"-";'
        f'IF(COUNTIFS($B$2:$B{linha};$B{linha};$H$2:$H{linha};$H{linha};'
        f'$J$2:$J{linha};$J{linha})>1;"REPETIDA";"OK"))'
    )


def formula_validacao_programa(linha: int) -> str:
    return (
        f'=IF($B{linha}="";"-";'
        f'IF(COUNTIFS($B$2:$B{linha};$B{linha};$A$2:$A{linha};$A{linha})>1;'
        f'"REPETIDA";"OK"))'
    )


@st.cache_data(ttl=30)
def carregar_dados() -> pd.DataFrame:
    ws = get_worksheet_dados()
    valores = ws.get_all_values()
    if not valores or len(valores) < 1:
        return pd.DataFrame(columns=COLS_TODAS)
    header = valores[0]
    linhas = valores[1:]
    df = pd.DataFrame(linhas, columns=header)
    for c in COLS_TODAS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLS_TODAS]
    # linha real na planilha (1-based, +2 porque a linha 1 é o header e o índice do df começa em 0)
    df.insert(0, "_linha", range(2, 2 + len(df)))
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
    ws.update(
        f"K{linha}:L{linha}",
        [[formula_validacao_caderno(linha), formula_validacao_programa(linha)]],
        value_input_option="USER_ENTERED",
    )
    limpar_cache()


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


def opcoes_com_outro(valores_existentes, chave_outro="+ Outro (digitar)"):
    base = sorted({v.strip() for v in valores_existentes if str(v).strip() != ""})
    return [""] + base + [chave_outro]


def resolver_valor_final(selecionado, digitado, chave_outro="+ Outro (digitar)"):
    if selecionado == chave_outro:
        return digitado.strip()
    return selecionado


CHAVE_OUTRO = "+ Outro (digitar)"

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

aba_ver, aba_add, aba_rel = st.tabs(["🔍 Visualizar & Editar", "➕ Adicionar", "📊 Relatórios"])

# ============================================================
# ABA 1 — VISUALIZAR & EDITAR
# ============================================================
with aba_ver:
    busca = st.text_input("Buscar (qualquer campo):", key="busca_texto")

    fc1, fc2, fc3, fc4 = st.columns(4)
    f_direc = fc1.multiselect("Direcionamento", sorted({v for v in df_prog["Direcionamento"] if v}))
    f_banca = fc2.multiselect("Banca", sorted({v for v in df_prog["Banca"] if v}))
    f_codigo = fc3.multiselect("Código (caderno)", sorted({v for v in df_prog["Código (caderno)"] if v}))
    f_valid = fc4.multiselect(
        "Validação",
        ["OK", "REPETIDA", "-"],
        help="Validação (caderno): REPETIDA aqui é uma questão duplicada dentro do MESMO caderno/direcionamento — normalmente é erro de cadastro.",
    )

    df_filt = df_prog.copy()
    if busca:
        mask = df_filt.apply(lambda r: r.astype(str).str.contains(busca, case=False).any(), axis=1)
        df_filt = df_filt[mask]
    if f_direc:
        df_filt = df_filt[df_filt["Direcionamento"].isin(f_direc)]
    if f_banca:
        df_filt = df_filt[df_filt["Banca"].isin(f_banca)]
    if f_codigo:
        df_filt = df_filt[df_filt["Código (caderno)"].isin(f_codigo)]
    if f_valid:
        df_filt = df_filt[
            df_filt[COL_VALID_CADERNO].isin(f_valid) | df_filt[COL_VALID_PROGRAMA].isin(f_valid)
        ]

    st.caption(f"{len(df_filt)} de {len(df_prog)} questões exibidas (programa: {programa_atual or '—'}).")

    colunas_exibir = [c for c in COLS_TODAS if c != "Programa"]
    selecao = st.dataframe(
        df_filt[colunas_exibir],
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
        st.markdown(f"### 📝 Editando questão nº {row['Número da questão']} (linha {linha_sheet} da planilha)")

        # Direcionamento e Banca ficam fora do form para permitir digitar "outro" reativamente
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

        op_tipo_cad = opcoes_com_outro(df["Tipo (caderno)"].unique())
        idx_tc = op_tipo_cad.index(row["Tipo (caderno)"]) if row["Tipo (caderno)"] in op_tipo_cad else 0
        e_tipocad_sel = c3.selectbox("Tipo (caderno)", op_tipo_cad, index=idx_tc, key=f"e_tipocad_{linha_sheet}")
        e_tipocad_novo = ""
        if e_tipocad_sel == CHAVE_OUTRO:
            e_tipocad_novo = c3.text_input("Novo tipo de caderno:", key=f"e_tipocad_novo_{linha_sheet}")

        with st.form(f"form_editar_{linha_sheet}"):
            c4, c5, c6 = st.columns(3)
            e_num = c4.text_input("Número da questão", value=str(row["Número da questão"]))
            e_tipoq = c5.text_input("Tipo (questão)", value=str(row["Tipo (questão)"]))
            e_ano = c6.text_input("Ano", value=str(row["Ano"]))

            c7, c8 = st.columns(2)
            e_codigo = c7.text_input("Código (caderno)", value=str(row["Código (caderno)"]))
            e_concurso = c8.text_input("Concurso", value=str(row["Concurso"]))

            e_assunto = st.text_input("Assunto", value=str(row["Assunto"]))

            col_b1, col_b2, _ = st.columns([1, 1, 2])
            btn_salvar = col_b1.form_submit_button("💾 Salvar alterações")
            btn_excluir = col_b2.form_submit_button("🗑️ Excluir questão", type="primary")

            if btn_salvar:
                dados = {
                    "Programa": row["Programa"],
                    "Número da questão": e_num.strip(),
                    "Banca": resolver_valor_final(e_banca_sel, e_banca_novo),
                    "Tipo (questão)": e_tipoq.strip(),
                    "Ano": e_ano.strip(),
                    "Concurso": e_concurso.strip(),
                    "Assunto": e_assunto.strip(),
                    "Código (caderno)": e_codigo.strip(),
                    "Tipo (caderno)": resolver_valor_final(e_tipocad_sel, e_tipocad_novo),
                    "Direcionamento": resolver_valor_final(e_direc_sel, e_direc_novo),
                }
                if not dados["Número da questão"]:
                    st.error("⚠️ Número da questão é obrigatório.")
                else:
                    editar_questao(linha_sheet, dados)
                    st.success("Alterações salvas!")
                    st.rerun()

            if btn_excluir:
                excluir_questao(linha_sheet, row)
                st.warning("Questão excluída (registro guardado na aba Exclusoes).")
                st.rerun()

# ============================================================
# ABA 2 — ADICIONAR
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

    op_tipocad_add = opcoes_com_outro(df["Tipo (caderno)"].unique())
    n_tipocad_sel = c3.selectbox("Tipo (caderno)", op_tipocad_add, key="add_tipocad")
    n_tipocad_novo = ""
    if n_tipocad_sel == CHAVE_OUTRO:
        n_tipocad_novo = c3.text_input("Novo tipo de caderno:", key="add_tipocad_novo")

    op_banca_add = opcoes_com_outro(df["Banca"].unique())
    n_banca_sel = st.selectbox("Banca", op_banca_add, key="add_banca")
    n_banca_novo = ""
    if n_banca_sel == CHAVE_OUTRO:
        n_banca_novo = st.text_input("Nova banca:", key="add_banca_novo")

    with st.form("form_adicionar", clear_on_submit=True):
        c4, c5, c6 = st.columns(3)
        n_num = c4.text_input("Número da questão*")
        n_tipoq = c5.text_input("Tipo (questão) (ex.: ABCDE, C/E)")
        n_ano = c6.text_input("Ano")

        c7, c8 = st.columns(2)
        n_codigo = c7.text_input("Código (caderno)*")
        n_concurso = c8.text_input("Concurso")

        n_assunto = st.text_input("Assunto")

        st.caption("* Campos obrigatórios.")

        if st.form_submit_button("🚀 Adicionar questão"):
            n_prog = resolver_valor_final(n_prog_sel, n_prog_novo)
            n_direc = resolver_valor_final(n_direc_sel, n_direc_novo)
            n_tipocad = resolver_valor_final(n_tipocad_sel, n_tipocad_novo)
            n_banca = resolver_valor_final(n_banca_sel, n_banca_novo)

            if not n_num.strip() or not n_codigo.strip() or not n_prog.strip():
                st.error("⚠️ Programa, Número da questão e Código (caderno) são obrigatórios.")
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
                    "Tipo (questão)": n_tipoq.strip(),
                    "Ano": n_ano.strip(),
                    "Concurso": n_concurso.strip(),
                    "Assunto": n_assunto.strip(),
                    "Código (caderno)": n_codigo.strip(),
                    "Tipo (caderno)": n_tipocad.strip(),
                    "Direcionamento": n_direc.strip(),
                }
                adicionar_questao(dados)
                if ja_existe:
                    st.warning(
                        "Questão adicionada, mas já existia outra com o mesmo número, "
                        "código e direcionamento — vai aparecer como REPETIDA na validação."
                    )
                else:
                    st.success("Questão adicionada com sucesso!")
                st.rerun()

# ============================================================
# ABA 3 — RELATÓRIOS
# ============================================================
with aba_rel:
    st.subheader(f"📊 Relatórios — {programa_atual or 'todos os programas'}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total de questões", len(df_prog))
    m2.metric("Cadernos únicos", df_prog["Código (caderno)"].nunique())
    m3.metric("Direcionamentos", df_prog["Direcionamento"].nunique())
    repetidas_caderno = (df_prog[COL_VALID_CADERNO] == "REPETIDA").sum()
    m4.metric("⚠️ Repetidas no MESMO caderno", int(repetidas_caderno))

    if repetidas_caderno > 0:
        st.error(
            f"{repetidas_caderno} questão(ões) repetida(s) dentro do mesmo caderno/direcionamento — "
            "isso normalmente é erro de cadastro (a mesma questão não devia aparecer duas vezes "
            "no mesmo direcionamento)."
        )
        with st.expander("Ver quais"):
            st.dataframe(
                df_prog[df_prog[COL_VALID_CADERNO] == "REPETIDA"][
                    ["Número da questão", "Código (caderno)", "Direcionamento", "Banca", "Assunto"]
                ],
                use_container_width=True, hide_index=True,
            )
    else:
        st.success("✅ Nenhuma questão repetida dentro do mesmo caderno/direcionamento.")

    st.markdown("---")
    st.write("#### Cobertura por Direcionamento")
    st.caption(
        "Quantas questões cada Direcionamento tem, e quantas já vieram do GERAL/de outro "
        "direcionamento (Validação (programa) = REPETIDA é esperado e bom sinal de reaproveitamento)."
    )
    if not df_prog.empty:
        cobertura = (
            df_prog.groupby("Direcionamento")
            .agg(
                Questões=("Número da questão", "count"),
                Reaproveitadas=(COL_VALID_PROGRAMA, lambda s: (s == "REPETIDA").sum()),
            )
            .reset_index()
        )
        st.dataframe(cobertura, use_container_width=True, hide_index=True)

    st.markdown("---")
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        st.write("**Questões por Banca**")
        bancas_validas = df_prog[df_prog["Banca"].astype(str).str.strip() != ""]
        if not bancas_validas.empty:
            st.bar_chart(bancas_validas["Banca"].value_counts())
        else:
            st.info("Nenhuma banca preenchida.")

    with col_g2:
        st.write("**Questões por Ano**")
        anos_validos = df_prog[df_prog["Ano"].astype(str).str.strip() != ""]
        if not anos_validos.empty:
            st.bar_chart(anos_validos["Ano"].value_counts().sort_index())
        else:
            st.info("Nenhum ano preenchido.")

    col_g3, col_g4 = st.columns(2)
    with col_g3:
        st.write("**Top 10 Concursos**")
        concursos_validos = df_prog[df_prog["Concurso"].astype(str).str.strip() != ""]
        if not concursos_validos.empty:
            st.bar_chart(concursos_validos["Concurso"].value_counts().head(10))
        else:
            st.info("Nenhum concurso preenchido.")

    with col_g4:
        st.write("**Top 10 Assuntos**")
        assuntos_validos = df_prog[df_prog["Assunto"].astype(str).str.strip() != ""]
        if not assuntos_validos.empty:
            st.bar_chart(assuntos_validos["Assunto"].value_counts().head(10))
        else:
            st.info("Nenhum assunto preenchido.")

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
