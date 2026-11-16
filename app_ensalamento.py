import io
import os
import time
import re
import tempfile
import unicodedata
import sys
from PIL import Image
import altair as alt

import pandas as pd
import streamlit as st

def caminho_recurso(nome):
    """Funciona com 'streamlit run' e dentro do .exe do PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome)

########################################
# FUNÇÕES DE NORMALIZAÇÃO / MAPEAMENTO #
########################################
MAPA_SEXO = {"f": 1, "feminino": 1, "fem": 1, "m": 0, "masculino": 0, "masc": 0}
MAPA_DISP = {"manha": 0, "m": 0, "tarde": 1, "t": 1}

SEXO_LABEL = {0: "M", 1: "F"}
DISP_LABEL = {0: "Manhã", 1: "Tarde"}
SEXO_OPCOES = ["Selecione...", "F", "M"]
DISP_OPCOES = ["Selecione...", "Manhã", "Tarde"]


def sem_acento(s):
    """Minusculas, sem acento, sem espaços nas pontas/duplicados."""
    if s is None or pd.isna(s):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def mapear_sexo(s):
    """Retorna 1 (F), 0 (M) ou None se vazio/inválido."""
    return MAPA_SEXO.get(sem_acento(s))


def mapear_disp(s):
    """Retorna 0 (Manha), 1 (Tarde) ou None se vazio/invalido."""
    return MAPA_DISP.get(sem_acento(s))


################################
# LEITURA E LIMPEZA AUTOMÁTICA #
################################
def ler_parametros_turmas(caminho_excel):
    df = pd.read_excel(caminho_excel, sheet_name="Turmas")
    df["Recursos"] = df["Recursos"].astype(str).str.strip()
    p = dict(zip(df["Recursos"], df["Quantidade"]))
    faltando = [k for k in ("turmas_manha", "turmas_tarde", "vagas_manha", "vagas_tarde") if k not in p]
    if faltando:
        raise ValueError(f"Parâmetros ausentes na aba 'Turmas': {faltando}")
    return (int(p["turmas_manha"]), int(p["turmas_tarde"]),
            int(p["vagas_manha"]), int(p["vagas_tarde"]))


def ler_alunos(caminho_excel):
    """Le a aba 'Alunos' e padroniza para as colunas Linha, Nome, Sexo, Disponibilidade.
    'Linha' eh o número da linha na planilha original (cabecalho = linha 1)."""
    df = pd.read_excel(caminho_excel, sheet_name="Alunos")
    df.columns = df.columns.astype(str).str.strip()

    def achar(opcoes):
        for c in df.columns:
            if any(op in sem_acento(c) for op in opcoes):
                return c
        return None

    c_nome = achar(["nome"])
    c_sexo = achar(["sexo"])
    c_disp = achar(["disponibilidade"])
    if not (c_nome and c_sexo and c_disp):
        raise ValueError(
            "Não encontrei as colunas Nome, Sexo e Disponibilidade na aba 'Alunos'. "
            f"Colunas encontradas: {list(df.columns)}"
        )
    out = pd.DataFrame({
        "Linha": df.index + 2,
        "Nome": df[c_nome],
        "Sexo": df[c_sexo],
        "Disponibilidade": df[c_disp],
    })
    return out


def limpeza_automatica(df):
    """Correcoes que nao precisam de decisao do usuario.
    Retorna (df_limpo, log) onde log é lista de dicts."""
    log = []
    df = df.copy()

    # Texto vazio / só espaços -> NaN
    for c in ["Nome", "Sexo", "Disponibilidade"]:
        df[c] = df[c].apply(lambda x: None if (pd.isna(x) or str(x).strip() == "") else x)

    # Linhas totalmente vazias
    vazias = df[["Nome", "Sexo", "Disponibilidade"]].isna().all(axis=1)
    for _, r in df[vazias].iterrows():
        log.append({"Linha": r["Linha"], "Campo": "-", "Valor original": "",
                    "Valor final": "", "Ação": "Linha vazia removida"})
    df = df[~vazias]

    # Linha de total
    total = df["Nome"].apply(sem_acento) == "total"
    for _, r in df[total].iterrows():
        log.append({"Linha": r["Linha"], "Nome": r["Nome"], "Campo": "Nome",
                    "Valor original": r["Nome"], "Valor final": "",
                    "Ação": "Linha de 'Total' ignorada (não é aluno)"})
    df = df[~total]

    # Espaços no nome
    def limpa_nome(x):
        return None if pd.isna(x) else re.sub(r"\s+", " ", str(x)).strip()

    novo = df["Nome"].apply(limpa_nome)
    for i in df.index:
        if not pd.isna(df.at[i, "Nome"]) and novo[i] != df.at[i, "Nome"]:
            log.append({"Linha": df.at[i, "Linha"], "Nome": novo[i], "Campo": "Nome",
                        "Valor original": repr(df.at[i, "Nome"]), "Valor final": novo[i],
                        "Ação": "Espaços extras removidos"})
    df["Nome"] = novo

    # Padroniza sexo/disponibilidade validos (ex.: ' F', 'feminino', 'manha', ' TARDE')
    for campo, fn, rotulos in (("Sexo", mapear_sexo, SEXO_LABEL),
                               ("Disponibilidade", mapear_disp, DISP_LABEL)):
        for i in df.index:
            orig = df.at[i, campo]
            if pd.isna(orig):
                continue
            v = fn(orig)
            if v is not None and rotulos[v] != orig:
                log.append({"Linha": df.at[i, "Linha"], "Nome": df.at[i, "Nome"], "Campo": campo,
                            "Valor original": repr(orig), "Valor final": rotulos[v],
                            "Ação": "Valor padronizado"})
                df.at[i, campo] = rotulos[v]

    return df.reset_index(drop=True), log


def diagnosticar_pendencias(df):
    """Retorna dict {linha: {'nome': motivo|None, 'sexo': motivo|None, 'disp': motivo|None}}
    apenas para linhas com algum problema."""
    pend = {}
    for _, r in df.iterrows():
        p = {"nome": None, "sexo": None, "disp": None}
        if pd.isna(r["Nome"]):
            p["nome"] = "vazio"
        if pd.isna(r["Sexo"]):
            p["sexo"] = "vazio"
        elif mapear_sexo(r["Sexo"]) is None:
            p["sexo"] = f"inválido ({r['Sexo']!r})"
        if pd.isna(r["Disponibilidade"]):
            p["disp"] = "vazio"
        elif mapear_disp(r["Disponibilidade"]) is None:
            p["disp"] = f"inválido ({r['Disponibilidade']!r})"
        if any(p.values()):
            pend[int(r["Linha"])] = p
    return pend


def grupos_duplicados(df):
    """Grupos de linhas com o mesmo nome (ignorando acento/caixa/espaços)."""
    chave = df["Nome"].apply(sem_acento)
    d = df[(chave != "") & chave.duplicated(keep=False)].copy()
    d["_k"] = chave[d.index]
    return [g.drop(columns="_k") for _, g in d.groupby("_k", sort=False)]


##########################################
# MODELAGEM MATEMÁTICA (OR-TOOLS + SCIP) #
##########################################
def resolver_ensalamento(df_alunos, num_salas_manha, num_salas_tarde, vagas_manha, vagas_tarde,
                         tempo_limite_s=60):
    from ortools.linear_solver import pywraplp

    df_trabalho = df_alunos.reset_index(drop=True).copy()
    df_trabalho["Id"] = range(len(df_trabalho))
    df_trabalho["Sexo_Num"] = df_trabalho["Sexo"].map(mapear_sexo)
    df_trabalho["Disp_Num"] = df_trabalho["Disponibilidade"].map(mapear_disp)

    contagem = df_trabalho.groupby(["Sexo_Num", "Disp_Num"]).size().to_dict()
    N = sum(contagem.values())
    F = sum(v for k, v in contagem.items() if k[0] == 1)

    alunos_manha = sum(v for k, v in contagem.items() if k[1] == 0)
    alunos_tarde = sum(v for k, v in contagem.items() if k[1] == 1)
    if alunos_manha > num_salas_manha * vagas_manha:
        raise ValueError(f"Inconsistência: {alunos_manha} alunos de manhã para "
                         f"{num_salas_manha * vagas_manha} vagas totais.")
    if alunos_tarde > num_salas_tarde * vagas_tarde:
        raise ValueError(f"Inconsistência: {alunos_tarde} alunos à tarde para "
                         f"{num_salas_tarde * vagas_tarde} vagas totais.")

    alfabeto = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    salas_manha = [f"MANHÃ - {alfabeto[i]}" for i in range(num_salas_manha)]
    salas_tarde = [f"TARDE - {alfabeto[num_salas_manha + i]}" for i in range(num_salas_tarde)]
    todas_salas = salas_manha + salas_tarde
    salas_permitidas = {0: salas_manha, 1: salas_tarde}

    w_tam, w_max, w_soma = 10, 10, 1

    solver = pywraplp.Solver.CreateSolver("SCIP")
    if not solver:
        raise RuntimeError("Solver SCIP não encontrado.")
    solver.SetTimeLimit(int(tempo_limite_s * 1000))

    y = {}
    for k, nk in contagem.items():
        for r in salas_permitidas[k[1]]:
            y[k, r] = solver.IntVar(0, nk, f"y_sexo{k[0]}_disp{k[1]}_{r}")

    n = {r: sum(y[k, r] for k in contagem if r in salas_permitidas[k[1]]) for r in todas_salas}
    f = {r: sum(y[k, r] for k in contagem if r in salas_permitidas[k[1]] and k[0] == 1)
         for r in todas_salas}

    for k, nk in contagem.items():
        solver.Add(sum(y[k, r] for r in salas_permitidas[k[1]]) == nk)

    for r in salas_manha:
        solver.Add(n[r] <= vagas_manha) #o numero de pessoas de manha nao pode passar da capacidade maxima das salas da manha
    for r in salas_tarde:
        solver.Add(n[r] <= vagas_tarde) #o numero de pessoas de tarde nao pode passar da capacidade maxima das salas da tarde

    # Quebra de simetria
    for i in range(len(salas_manha) - 1):
        solver.Add(n[salas_manha[i]] >= n[salas_manha[i + 1]])
    for i in range(len(salas_tarde) - 1):
        solver.Add(n[salas_tarde[i]] >= n[salas_tarde[i + 1]])

    nmax_m = solver.IntVar(0, N, "nmax_m")
    nmin_m = solver.IntVar(0, N, "nmin_m")
    for r in salas_manha:
        solver.Add(n[r] <= nmax_m)
        solver.Add(n[r] >= nmin_m)

    nmax_t = solver.IntVar(0, N, "nmax_t")
    nmin_t = solver.IntVar(0, N, "nmin_t")
    for r in salas_tarde:
        solver.Add(n[r] <= nmax_t)
        solver.Add(n[r] >= nmin_t)

    E = {r: solver.NumVar(0, solver.infinity(), f"E_{r}") for r in todas_salas}
    Emax = solver.NumVar(0, solver.infinity(), "Emax")
    for r in todas_salas:
        D_r = N * f[r] - F * n[r]
        solver.Add(E[r] >= D_r)
        solver.Add(E[r] >= -D_r)
        solver.Add(Emax >= E[r])

    #funcao objetivo
    solver.Minimize(
        w_tam * N * (nmax_m - nmin_m)
        + w_tam * N * (nmax_t - nmin_t)
        + w_max * Emax
        + w_soma * sum(E[r] for r in todas_salas)
    )

    status = solver.Solve()
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError("O solver não conseguiu encontrar uma alocação viável.")

    sala_de = {}
    for k in contagem:
        ids = df_trabalho.loc[(df_trabalho.Sexo_Num == k[0]) & (df_trabalho.Disp_Num == k[1]), "Id"].tolist()
        pos = 0
        for r in salas_permitidas[k[1]]:
            q = int(round(y[k, r].solution_value()))
            for id_aluno in ids[pos:pos + q]:
                sala_de[id_aluno] = r
            pos += q

    df_saida = pd.DataFrame({
        "Nome": df_trabalho["Nome"],
        "Sexo": df_trabalho["Sexo"],
        "Disponibilidade": df_trabalho["Disponibilidade"],
        "Turma": df_trabalho["Id"].map(sala_de),
    })
    otimo = status == pywraplp.Solver.OPTIMAL
    return df_saida, otimo


##################
# SAÍDA EM EXCEL #
##################
def gravar_excel(bytes_original, df_saida, df_log):
    """Grava abas 'Ensalamento' e 'Inconsistências' numa cópia do arquivo original."""
    with tempfile.TemporaryDirectory() as tmp:
        caminho = os.path.join(tmp, "instancia.xlsx")
        with open(caminho, "wb") as fh:
            fh.write(bytes_original)
        with pd.ExcelWriter(caminho, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
            df_saida.to_excel(w, sheet_name="Ensalamento", index=False)
            df_log.to_excel(w, sheet_name="Inconsistências", index=False)
        with open(caminho, "rb") as fh:
            return fh.read()

def escolher_arquivo():
    """Abre a janela do Windows para escolher a planilha e devolve o caminho."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilename(title="Selecione a planilha",
                                          filetypes=[("Planilha Excel", "*.xlsx")])
    finally:
        root.destroy()


def _procurar():
    try:
        p = escolher_arquivo()
    except Exception:
        st.session_state["aviso_procurar"] = True
        return
    if p:
        st.session_state["caminho"] = p


def salvar_no_original(caminho, dados):
    """Sobrescreve o arquivo original (grava num temporário e troca, para não corromper)."""
    tmp = caminho + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(dados)
        os.replace(tmp, caminho)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        if isinstance(e, PermissionError):
            raise PermissionError("o arquivo está aberto em outro programa (feche o Excel)")
        raise

#######################
# INTERFACE STREAMLIT #
#######################
COLS_LOG = ["Linha", "Nome", "Campo", "Valor original", "Valor final", "Ação"]
OP_EXCLUIR = "Não alocar (registrar em Inconsistências)"
OP_COMPLETAR = "Completar os dados"


def main():
    logo_unisoma = caminho_recurso("unisoma.webp")
    logo_ABC = caminho_recurso("logo_ABC.webp")
    tem_logo = os.path.exists(logo_unisoma) and os.path.exists(logo_ABC)

    st.set_page_config(page_title="Alocatrix",
                       page_icon=Image.open(logo_ABC) if tem_logo else None,
                       layout="wide")

    if tem_logo:
        col_esq, col_titulo, col_dir = st.columns([1, 6, 1], vertical_alignment="center")
        col_esq.image(logo_ABC, width=90)
        col_titulo.markdown("<h1 style='text-align:center'>Alocatrix</h1>",
                            unsafe_allow_html=True)
        col_dir.image(logo_unisoma, width=90)
    else:
        st.title("Alocatrix")
    st.caption("Carregue a planilha, revise os erros e encontre a distribuição otimizada.")

    col_c, col_b = st.columns([6, 1], vertical_alignment="bottom")
    col_b.button("Procurar...", on_click=_procurar)
    col_c.text_input("Caminho da planilha (.xlsx) com as abas 'Alunos' e 'Turmas'", key="caminho")
    if st.session_state.pop("aviso_procurar", False):
        st.warning("Não foi possível abrir a janela de seleção. Cole o caminho do arquivo no campo ao lado.")
    caminho = st.session_state.get("caminho", "").strip().strip('"')

    if not caminho:
        st.info("Clique em 'Procurar...' para escolher a planilha.")
        return
    if not os.path.isfile(caminho) or not caminho.lower().endswith(".xlsx"):
        st.error("Arquivo não encontrado ou não é .xlsx.")
        return

    with open(caminho, "rb") as fh:
        bytes_original = fh.read()
    if st.session_state.get("_arquivo_id") != caminho:
        for k in list(st.session_state.keys()):
            if k != "caminho":
                del st.session_state[k]
        st.session_state["_arquivo_id"] = caminho

    try:
        bruto = ler_alunos(io.BytesIO(bytes_original))
        params = ler_parametros_turmas(io.BytesIO(bytes_original))
    except Exception as e:
        st.error(f"Erro ao ler a planilha: {e}")
        return

    num_m, num_t, vag_m, vag_t = params
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Turmas manhã", num_m)
    c2.metric("Vagas/turma manhã", vag_m)
    c3.metric("Turmas tarde", num_t)
    c4.metric("Vagas/turma tarde", vag_t)

    #Limpeza automatica
    df, log = limpeza_automatica(bruto)
    with st.expander(f"Correções automáticas aplicadas ({len(log)})"):
        if log:
            st.dataframe(pd.DataFrame(log, columns=COLS_LOG), use_container_width=True,
                         hide_index=True)
        else:
            st.write("Nenhuma.")

    #Dados faltantes / invalidos
    st.header("Dados faltantes ou inválidos")
    pend = diagnosticar_pendencias(df)
    excluir_linhas = set()
    bloqueado = False

    if not pend:
        st.success("Nenhum dado faltante ou inválido.")
    else:
        st.warning(f"{len(pend)} linha(s) com dados faltantes/inválidos. Por padrão, elas **não são "
                   "alocadas** e ficam registradas na aba 'Inconsistências'. Se preferir, escolha "
                   "completar os dados.")
        for linha, p in pend.items():
            row = df[df["Linha"] == linha].iloc[0]
            motivos = [f"{c}: {m}" for c, m in (("Nome", p["nome"]), ("Sexo", p["sexo"]),
                                                  ("Disponibilidade", p["disp"])) if m]
            nome_log = "" if pd.isna(row["Nome"]) else row["Nome"]
            with st.container(border=True):
                st.markdown(f"**Linha {linha}** — Nome: `{row['Nome']}` · Sexo: `{row['Sexo']}` · "
                            f"Disponibilidade: `{row['Disponibilidade']}`  \n"
                            f"Problema: {'; '.join(motivos)}")
                acao = st.radio("O que fazer?", [OP_EXCLUIR, OP_COMPLETAR], key=f"acao_{linha}",
                                horizontal=True)
                if acao == OP_EXCLUIR:
                    excluir_linhas.add(linha)
                    log.append({"Linha": linha, "Nome": nome_log,
                                "Campo": ", ".join(c for c, m in (("Nome", p["nome"]),
                                                                  ("Sexo", p["sexo"]),
                                                                  ("Disponibilidade", p["disp"])) if m),
                                "Valor original": f"{row['Sexo']} / {row['Disponibilidade']}",
                                "Valor final": "",
                                "Ação": "NÃO ALOCADO — " + "; ".join(motivos)})
                    continue

                cols = st.columns(3)
                mask = df["Linha"] == linha
                if p["nome"]:
                    v = cols[0].text_input("Nome", key=f"nome_{linha}").strip()
                    if v:
                        v = re.sub(r"\s+", " ", v)
                        df.loc[mask, "Nome"] = v
                        nome_log = v
                        log.append({"Linha": linha, "Nome": v, "Campo": "Nome", "Valor original": "",
                                    "Valor final": v, "Ação": "Preenchido pelo usuário"})
                    else:
                        bloqueado = True
                if p["sexo"]:
                    v = cols[1].selectbox("Sexo", SEXO_OPCOES, key=f"sexo_{linha}")
                    if v != SEXO_OPCOES[0]:
                        df.loc[mask, "Sexo"] = v
                        log.append({"Linha": linha, "Nome": nome_log, "Campo": "Sexo",
                                    "Valor original": row["Sexo"], "Valor final": v,
                                    "Ação": "Preenchido pelo usuário"})
                    else:
                        bloqueado = True
                if p["disp"]:
                    v = cols[2].selectbox("Disponibilidade", DISP_OPCOES, key=f"disp_{linha}")
                    if v != DISP_OPCOES[0]:
                        df.loc[mask, "Disponibilidade"] = v
                        log.append({"Linha": linha, "Nome": nome_log, "Campo": "Disponibilidade",
                                    "Valor original": row["Disponibilidade"], "Valor final": v,
                                    "Ação": "Preenchido pelo usuário"})
                    else:
                        bloqueado = True
        if any(st.session_state.get(f"acao_{l}") == OP_COMPLETAR for l in pend):
            st.info("Se houver mais de uma linha com o mesmo nome, as duplicatas vão para "
                "'Inconsistências', mas continuam sendo alocadas em sala. Só deixam de ser "
                "alocadas se você desmarcar todas as caixas.")

    df = df[~df["Linha"].isin(excluir_linhas)].reset_index(drop=True)

    #Duplicidade
    st.header("Nomes duplicados")
    grupos = grupos_duplicados(df)
    nao_alocar_dup = set()
    if not grupos:
        st.success("Nenhum nome duplicado.")
    else:
        st.warning(f"{len(grupos)} nome(s) aparecem mais de uma vez. Podem ser erro de cadastro ou "
                   "homônimos. Por padrão **todas as linhas são alocadas**. Desmarque a caixa das "
                   "linhas que você não quer enviar para nenhuma sala; tudo fica registrado em "
                   "'Inconsistências'.")
        for g in grupos:
            linhas = ", ".join(str(int(x)) for x in g["Linha"])
            with st.container(border=True):
                st.markdown(f"**{g['Nome'].iloc[0]}** — linhas {linhas}")
                for _, r in g.iterrows():
                    linha = int(r["Linha"])
                    alocar = st.checkbox(
                        f"Alocar linha {linha} ({r['Sexo']}, {r['Disponibilidade']})",
                        value=True, key=f"alocar_{linha}")
                    if alocar:
                        log.append({"Linha": linha, "Nome": r["Nome"], "Campo": "Nome",
                                    "Valor original": r["Nome"], "Valor final": r["Nome"],
                                    "Ação": f"DUPLICIDADE — mesmo nome nas linhas {linhas}; "
                                            "alocado (verificar possível erro de cadastro)"})
                    else:
                        nao_alocar_dup.add(linha)
                        log.append({"Linha": linha, "Nome": r["Nome"], "Campo": "Nome",
                                    "Valor original": r["Nome"], "Valor final": "",
                                    "Ação": f"NÃO ALOCADO — duplicidade (mesmo nome nas linhas "
                                            f"{linhas}); excluído pelo usuário"})
        if nao_alocar_dup:
            st.info("⚠️ O Validador oficial exige que todos os homônimos sejam alocados: "
                    "as linhas desmarcadas serão reprovadas por ele.")

    df = df[~df["Linha"].isin(nao_alocar_dup)].reset_index(drop=True)

    #Resumo e execução
    st.header("Resumo e execução")
    n_m = int((df["Disponibilidade"].map(mapear_disp) == 0).sum())
    n_t = int((df["Disponibilidade"].map(mapear_disp) == 1).sum())
    n_f = int((df["Sexo"].map(mapear_sexo) == 1).sum())
    m1, m2, m3 = st.columns(3)
    m1.metric("Alunos a alocar", len(df))
    m2.metric("Manhã", f"{n_m} / {num_m * vag_m} vagas")
    m3.metric("Tarde", f"{n_t} / {num_t * vag_t} vagas")

    cap_ok = n_m <= num_m * vag_m and n_t <= num_t * vag_t
    if not cap_ok:
        st.error("Não é possível ensalar todos: há mais alunos do que vagas totais em algum turno.")
    if bloqueado:
        st.error("Complete os campos marcados como 'Completar os dados' (ou escolha não alocar).")

    df_log = pd.DataFrame(log, columns=COLS_LOG).fillna("")

    if st.button("Rodar otimização", type="primary", disabled=(not cap_ok or bloqueado)):
        with st.spinner("Otimizando..."):
            try:
                t0 = time.perf_counter()
                df_saida, otimo = resolver_ensalamento(
                    df[["Nome", "Sexo", "Disponibilidade"]], num_m, num_t, vag_m, vag_t)
                duracao = time.perf_counter() - t0
                xlsx_bytes = gravar_excel(bytes_original, df_saida, df_log)
                st.session_state["resultado"] = (df_saida, df_log, otimo, xlsx_bytes, duracao)
                st.session_state.pop("salvo", None)
            except Exception as e:
                st.session_state.pop("resultado", None)
                st.error(f"Erro ao distribuir alunos: {e}")

    if "resultado" in st.session_state:
        df_saida, df_log_res, otimo, xlsx_bytes, duracao = st.session_state["resultado"]
        if otimo:
            st.success(f"Distribuição de alunos em salas concluída (solução ótima) em {duracao:.2f} s.")
        else:
            st.warning(f"Distribuição de alunos em salas concluída em {duracao:.2f} s, mas o solver atingiu o tempo "
                       "limite (solução viável, não necessariamente ótima).")

        d = df_saida.assign(F=df_saida["Sexo"].map(mapear_sexo))
        d["Turno"] = d["Turma"].str.split(" - ").str[0]
        resumo = (d.groupby(["Turno", "Turma"])
                  .agg(Alunos=("Nome", "size"), Feminino=("F", "sum"))
                  .assign(Masculino=lambda x: x["Alunos"] - x["Feminino"])
                  .reset_index())
        resumo["Feminino (%)"] = (resumo["Feminino"] / resumo["Alunos"] * 100).round(1)
        resumo["Masculino (%)"] = (resumo["Masculino"] / resumo["Alunos"] * 100).round(1)
        resumo["Vagas"] = resumo["Turno"].map({"MANHÃ": vag_m, "TARDE": vag_t})
        resumo["Ocupação (%)"] = (resumo["Alunos"] / resumo["Vagas"] * 100).round(1)

        st.subheader("Resumo por turma")
        tabela = resumo.drop(columns="Turno")

        def colorir_turno(linha):
            cor = ("rgba(255, 193, 7, 0.22)" if linha["Turma"].startswith("MANHÃ")
                   else "rgba(171, 71, 188, 0.25)")
            return [f"background-color: {cor}"] * len(linha)

        st.caption("🟨 Amarelo = turmas da manhã · 🟪 Roxo = turmas da tarde")
        st.dataframe(
            tabela.style.apply(colorir_turno, axis=1)
                  .format({"Feminino (%)": "{:.1f}", "Masculino (%)": "{:.1f}",
                           "Ocupação (%)": "{:.1f}"}),
            hide_index=True, use_container_width=True)

        st.subheader("Gráficos")
        idx = resumo.set_index("Turma")

        # Uma pizza por turma: meninas x meninos
        st.markdown("**Distribuição por sexo em cada turma**")
        turmas = resumo["Turma"].tolist()
        for ini in range(0, len(turmas), 3):
            cols = st.columns(3)
            for col, turma in zip(cols, turmas[ini:ini + 3]):
                r = idx.loc[turma]
                dados = pd.DataFrame({
                    "Sexo": [f"Feminino ({int(r['Feminino'])})", f"Masculino ({int(r['Masculino'])})"],
                    "Alunos": [int(r["Feminino"]), int(r["Masculino"])],
                })
                escala = alt.Scale(domain=list(dados["Sexo"]), range=["#E4572E", "#2E86DE"])
                pizza = (
                    alt.Chart(dados)
                    .mark_arc()
                    .encode(
                        theta=alt.Theta("Alunos:Q"),
                        color=alt.Color("Sexo:N", scale=escala,
                                        legend=alt.Legend(title=None, orient="bottom")),
                        tooltip=["Sexo", "Alunos"],
                    )
                    .properties(title=f"{turma} ({int(r['Alunos'])} alunos)", height=240)
                )
                col.altair_chart(pizza, use_container_width=True)

        # Ocupação e % de meninas, lado a lado, abaixo das pizzas
        g2, g3 = st.columns(2)
        # Ocupação das vagas, colorida por turno
        st.markdown("**Ocupação das vagas por turma (%)**")
        dados_ocup = resumo[["Turma", "Turno", "Alunos", "Vagas"]].assign(
            Ocupacao=resumo["Ocupação (%)"])
        barras = (
            alt.Chart(dados_ocup)
            .mark_bar()
            .encode(
                x=alt.X("Turma:N", sort=None, axis=alt.Axis(labelAngle=0, title=None)),
                y=alt.Y("Ocupacao:Q", scale=alt.Scale(domain=[0, 100]),
                        axis=alt.Axis(title="Ocupação (%)")),
                color=alt.Color("Turno:N",
                                scale=alt.Scale(domain=["MANHÃ", "TARDE"],
                                                range=["#FFC107", "#AB47BC"]),
                                legend=alt.Legend(title="Turno", orient="bottom")),
                tooltip=["Turma", "Alunos", "Vagas", alt.Tooltip("Ocupacao:Q", title="Ocupação (%)")],
            )
            .properties(height=320)
        )
        st.altair_chart(barras, use_container_width=True)
        st.subheader("Alocação de Estudantes")
        st.dataframe(df_saida.sort_values(["Turma", "Nome"]), hide_index=True,
                     use_container_width=True)
        st.divider()
        if st.button("Gravar resultado na planilha original"):
            try:
                salvar_no_original(caminho, xlsx_bytes)
                st.session_state["salvo"] = ("ok", caminho)
            except Exception as e:
                st.session_state["salvo"] = ("erro", str(e))

        salvo = st.session_state.get("salvo")
        if salvo:
            if salvo[0] == "ok":
                st.success(f"Abas 'Ensalamento' e 'Inconsistências' gravadas em: {salvo[1]}")
            else:
                st.error(f"Não foi possível gravar no arquivo original: {salvo[1]}. "
                         "Se a planilha estiver aberta no Excel, feche-a e clique de novo.")


if __name__ == "__main__":
    main()