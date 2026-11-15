"""Monta a rede deputado–município ("Emenda vira voto?") a partir de dados/brutos/.

Uso (a partir da raiz do repositório, depois de scripts/baixar_dados.py):
    python3 scripts/construir_rede.py

Saídas em dados/processados/ (vão para o git; dicionário em dados/processados/LEIA-ME.md):
    deputados.csv           nós deputado (648 da legislatura 57) + ligação com o TSE 2022/2026
    municipios.csv          nós município (5.571) + totais de voto 2022/2026
    arestas_dinheiro.csv    camada dinheiro: emendas individuais 2024-26 pagas a entes municipais até 09/2026
    arestas_voto_2026.csv   camada voto 2026: votos de cada deputado em cada município
    arestas_voto_2022.csv   linha de base: votos dos mesmos deputados em 2022
    relatorio_construcao.txt  diagnóstico de cada cruzamento (quanto casou, quanto ficou de fora)

A agregação dos votos (≈14 GB de CSV dentro dos zips do TSE) é guardada em
dados/intermediarios/ (fora do git) e só é refeita se esses arquivos forem apagados.
"""
import difflib
import json
import re
import sys
import time
import unicodedata
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
BRUTOS = RAIZ / "dados" / "brutos"
INTER = RAIZ / "dados" / "intermediarios"
PROC = RAIZ / "dados" / "processados"

ANOS_EMENDA = {"2024", "2025", "2026"}   # emendas da legislatura atual (as de 2023 foram escritas pela anterior)
CORTE_PAGAMENTO = "202609"               # só pagamentos até set/2026: o 1º turno foi em 04/10/2026
NATUREZA_MUNICIPAL = {"Município", "Fundo Público da Administração Direta Municipal"}
CD_CARGO_DEP_FEDERAL = "6"

# Casos conferidos à mão: nome do autor na base de emendas -> id da Câmara.
# (preenchido a partir do relatório de casamento aproximado; ver relatorio_construcao.txt)
AUTORES_MANUAL: dict[str, str] = {
    "DUARTE JR.": "220686",   # Câmara: "Duarte" (Hildelis Silva Duarte Junior, MA)
    "ZUCCO": "220552",        # Câmara: "Tenente Coronel Zucco" (Luciano Lorenzini Zucco, RS)
}

# Grafias de município que diferem entre o Portal da Transparência e o TSE/IBGE: (UF, nome no Portal) -> nome IBGE
MUNICIPIOS_MANUAL: dict[tuple[str, str], str] = {
    ("PA", "SANTA ISABEL DO PARÁ"): "Santa Izabel Do Pará",
    ("RS", "SANTANA DO LIVRAMENTO"): "Sant' Ana Do Livramento",
    ("CE", "ITAPAGÉ"): "Itapajé",
    ("PE", "IGUARACI"): "Iguaracy",
    ("RJ", "TRAJANO DE MORAIS"): "Trajano De Moraes",
}

# Sufixos que distinguem pessoas (Otto Alencar, senador  x  Otto Alencar Filho, deputado)
SUFIXOS = {"FILHO", "NETO", "JUNIOR", "JR", "SOBRINHO"}

_LOG: list[str] = []


def log(*partes) -> None:
    linha = " ".join(str(p) for p in partes)
    print(linha, flush=True)
    _LOG.append(linha)


def norm(s) -> str:
    """Maiúsculas, sem acento e sem pontuação: 'Dr. Allan Garcês' -> 'DR ALLAN GARCES'."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", s).split())


def reais(col: pd.Series) -> pd.Series:
    return col.str.replace(".", "", regex=False).str.replace(",", ".", regex=False).astype(float)


# ---------------------------------------------------------------------------
# 1. Votos: agrega zona -> município, só deputado federal (lento, fica em cache)
# ---------------------------------------------------------------------------
COLS_VOTO = ["NR_TURNO", "SG_UF", "CD_MUNICIPIO", "CD_CARGO", "SQ_CANDIDATO", "NM_URNA_CANDIDATO",
             "SG_PARTIDO", "DS_SIT_TOT_TURNO", "QT_VOTOS_NOMINAIS_VALIDOS"]
CHAVE_VOTO = ["SG_UF", "CD_MUNICIPIO", "SQ_CANDIDATO", "NM_URNA_CANDIDATO", "SG_PARTIDO", "DS_SIT_TOT_TURNO"]


def agregar_votos(ano: int) -> Path:
    saida = INTER / f"votos_depfed_{ano}.csv.gz"
    if saida.exists():
        return saida
    t0 = time.time()
    z = zipfile.ZipFile(BRUTOS / "tse" / f"votacao_candidato_munzona_{ano}.zip")
    nomes = sorted(n for n in z.namelist() if re.search(r"_[A-Z]{2}\.csv$", n) and not n.endswith("_BR.csv"))
    partes = []
    for n in nomes:
        with z.open(n) as fh:
            for ch in pd.read_csv(fh, sep=";", encoding="latin1", dtype=str, usecols=COLS_VOTO, chunksize=1_000_000):
                ch = ch[(ch.CD_CARGO == CD_CARGO_DEP_FEDERAL) & (ch.NR_TURNO == "1")]
                if ch.empty:
                    continue
                ch = ch.assign(QT_VOTOS_NOMINAIS_VALIDOS=ch.QT_VOTOS_NOMINAIS_VALIDOS.astype(int))
                partes.append(ch.groupby(CHAVE_VOTO, dropna=False).QT_VOTOS_NOMINAIS_VALIDOS.sum().reset_index())
        print(f"  votos {ano}: {n} ({time.time() - t0:.0f} s)", flush=True)
    df = pd.concat(partes).groupby(CHAVE_VOTO, dropna=False).QT_VOTOS_NOMINAIS_VALIDOS.sum().reset_index()
    df["CD_MUNICIPIO"] = df.CD_MUNICIPIO.str.zfill(5)
    INTER.mkdir(parents=True, exist_ok=True)
    df.to_csv(saida, index=False, compression="gzip")
    return saida


# ---------------------------------------------------------------------------
# 2. Municípios
# ---------------------------------------------------------------------------
def carregar_municipios() -> pd.DataFrame:
    with zipfile.ZipFile(BRUTOS / "tse" / "municipio_tse_ibge.zip") as z:
        t = pd.read_csv(z.open("municipio_tse_ibge.csv"), sep=";", encoding="latin1", dtype=str)
    t["CD_MUNICIPIO_TSE"] = t.CD_MUNICIPIO_TSE.str.zfill(5)
    return t.rename(columns={"CD_MUNICIPIO_IBGE": "cd_ibge", "CD_MUNICIPIO_TSE": "cd_tse",
                             "NM_MUNICIPIO_IBGE": "nome", "SG_UF": "uf"})[["cd_ibge", "cd_tse", "nome", "uf",
                                                                           "NM_MUNICIPIO_TSE"]]


def votos_com_ibge(ano: int, mun: pd.DataFrame) -> pd.DataFrame:
    v = pd.read_csv(INTER / f"votos_depfed_{ano}.csv.gz", dtype={"CD_MUNICIPIO": str, "SQ_CANDIDATO": str})
    v = v.merge(mun[["cd_tse", "cd_ibge"]], left_on="CD_MUNICIPIO", right_on="cd_tse", how="left")
    sem = v[v.cd_ibge.isna()]
    log(f"[votos {ano}] linhas candidato×município: {len(v):,} | candidatos: {v.SQ_CANDIDATO.nunique():,} | "
        f"municípios: {v.CD_MUNICIPIO.nunique():,} | votos nominais válidos: {v.QT_VOTOS_NOMINAIS_VALIDOS.sum():,}")
    ufs_sem_voto = sorted(set(mun.uf) - set(v.SG_UF))
    if ufs_sem_voto:
        log(f"  ATENÇÃO: UF sem nenhum voto de deputado federal no arquivo do TSE: {ufs_sem_voto} "
            f"({mun.uf.isin(ufs_sem_voto).sum()} municípios) — ficam fora da camada voto {ano}")
    if len(sem):
        log(f"  ATENÇÃO: {sem.CD_MUNICIPIO.nunique()} municípios TSE sem código IBGE ({sem.QT_VOTOS_NOMINAIS_VALIDOS.sum():,} votos) — descartados")
    v = v.dropna(subset=["cd_ibge"])
    tot = v.groupby("cd_ibge").QT_VOTOS_NOMINAIS_VALIDOS.transform("sum")
    v["fatia_cidade"] = v.QT_VOTOS_NOMINAIS_VALIDOS / tot
    v["fatia_candidato"] = v.QT_VOTOS_NOMINAIS_VALIDOS / v.groupby("SQ_CANDIDATO").QT_VOTOS_NOMINAIS_VALIDOS.transform("sum")
    return v


def resumo_municipal(v: pd.DataFrame, ano: int) -> pd.DataFrame:
    """Total de votos válidos para dep. federal e nº de candidatos votados no município."""
    g = v[v.QT_VOTOS_NOMINAIS_VALIDOS > 0].groupby("cd_ibge")
    return pd.DataFrame({
        f"votos_validos_{ano}": g.QT_VOTOS_NOMINAIS_VALIDOS.sum(),
        f"n_candidatos_{ano}": g.size(),
    })


# ---------------------------------------------------------------------------
# 3. Deputados: Câmara (legislatura 57) -> candidatos TSE 2022 e 2026
# ---------------------------------------------------------------------------
def carregar_deputados() -> pd.DataFrame:
    leg = pd.DataFrame(json.load(open(BRUTOS / "camara" / "deputados_leg57.json"))["dados"])
    leg["id"] = leg.id.astype(str)
    dep = leg.groupby("id").agg(nome=("nome", "first"), uf=("siglaUf", "first"),
                                partidos_camara=("siglaPartido", lambda s: "/".join(dict.fromkeys(s)))).reset_index()
    todos = pd.read_csv(BRUTOS / "camara" / "deputados.csv", sep=";", dtype=str, encoding="utf-8-sig")
    todos["id"] = todos.uri.str.extract(r"(\d+)$")[0]
    dep = dep.merge(todos[["id", "nomeCivil", "dataNascimento"]], on="id", how="left")
    dep = dep.rename(columns={"id": "id_camara", "nomeCivil": "nome_civil", "dataNascimento": "nascimento"})
    log(f"[deputados] legislatura 57: {len(dep)} deputados que exerceram mandato (titulares + suplentes)")
    return dep


def carregar_candidatos(ano: int) -> pd.DataFrame:
    with zipfile.ZipFile(BRUTOS / "tse" / f"consulta_cand_{ano}.zip") as z:
        c = pd.read_csv(z.open(f"consulta_cand_{ano}_BRASIL.csv"), sep=";", encoding="latin1", dtype=str)
    c = c[c.NR_TURNO == "1"].drop_duplicates("SQ_CANDIDATO")
    c["nasc"] = pd.to_datetime(c.DT_NASCIMENTO, format="%d/%m/%Y", errors="coerce").dt.strftime("%Y-%m-%d")
    c["chave"] = c.NM_CANDIDATO.map(norm) + "|" + c.nasc.fillna("")
    return c


def ligar_tse(dep: pd.DataFrame, cand: pd.DataFrame, ano: int) -> pd.DataFrame:
    """Casa deputado da Câmara com candidato do TSE por nome civil + data de nascimento.
    Reserva: mesma data de nascimento e mesmo primeiro nome (resolve grafias diferentes do sobrenome)."""
    dep = dep.copy()
    dep["chave"] = dep.nome_civil.map(norm) + "|" + dep.nascimento.fillna("")
    # se a pessoa tem mais de uma candidatura no ano (ex.: substituição), fica a que recebeu voto / a última
    # quem teve mais de uma candidatura no ano (troca de cargo, substituição): fica a que foi à urna
    # (resultado diferente de #NULO) e, em empate, a de deputado federal
    cand = (cand.assign(r1=cand.DS_SIT_TOT_TURNO.ne("#NULO"), r2=cand.DS_CARGO.eq("DEPUTADO FEDERAL"))
            .sort_values(["chave", "r1", "r2"], kind="stable").drop_duplicates("chave", keep="last"))
    m = dep.merge(cand[["chave", "SQ_CANDIDATO"]], on="chave", how="left")
    falta = m.SQ_CANDIDATO.isna()
    if falta.any():
        c2 = cand.assign(prim=cand.NM_CANDIDATO.map(lambda s: norm(s).split()[0]))
        c2 = c2.drop_duplicates(["nasc", "prim"], keep=False)
        m2 = m.loc[falta, ["id_camara", "nome_civil", "nascimento"]].assign(
            prim=lambda d: d.nome_civil.map(lambda s: norm(s).split()[0] if isinstance(s, str) else ""))
        m2 = m2.merge(c2[["nasc", "prim", "SQ_CANDIDATO", "NM_CANDIDATO"]], left_on=["nascimento", "prim"],
                      right_on=["nasc", "prim"], how="inner")
        for _, r in m2.iterrows():
            log(f"  [{ano}] casado por nascimento+1º nome: {r.nome_civil} ~ {r.NM_CANDIDATO}")
        m.loc[m.id_camara.isin(m2.id_camara), "SQ_CANDIDATO"] = m.id_camara.map(m2.set_index("id_camara").SQ_CANDIDATO)
    m = m.merge(cand[["SQ_CANDIDATO", "DS_CARGO", "SG_UF", "SG_PARTIDO", "SG_FEDERACAO", "NM_URNA_CANDIDATO",
                      "DS_SITUACAO_CANDIDATURA", "DS_SIT_TOT_TURNO"]], on="SQ_CANDIDATO", how="left")
    ren = {"SQ_CANDIDATO": "sq", "DS_CARGO": "cargo", "SG_UF": "uf_tse", "SG_PARTIDO": "partido",
           "SG_FEDERACAO": "federacao", "NM_URNA_CANDIDATO": "nome_urna",
           "DS_SITUACAO_CANDIDATURA": "situacao_candidatura", "DS_SIT_TOT_TURNO": "resultado"}
    m = m.rename(columns={k: f"{v}_{ano}" for k, v in ren.items()})
    log(f"[TSE {ano}] deputados encontrados como candidatos (qualquer cargo): {m[f'sq_{ano}'].notna().sum()} de {len(m)}")
    log("  cargo disputado:", m[f"cargo_{ano}"].fillna("(não concorreu / não achado)").value_counts().to_dict())
    return m.drop(columns="chave")


# ---------------------------------------------------------------------------
# 4. Camada dinheiro
# ---------------------------------------------------------------------------
TITULOS = {"DR", "DRA", "DEP", "JR", "JUNIOR", "FILHO", "NETO", "SOBRINHO", "PAI", "DE", "DA", "DO", "DOS", "DAS", "E",
           "PROFESSOR", "PROFESSORA", "PASTOR", "PASTORA", "DELEGADO", "DELEGADA", "CAPITAO", "TENENTE", "CORONEL",
           "GENERAL", "SARGENTO", "CABO", "SOLDADO", "MISSIONARIO", "ENFERMEIRA", "ENFERMEIRO", "BISPO", "IRMAO"}


def tokens(s: str) -> set[str]:
    return {t for t in norm(s).split() if t not in TITULOS and len(t) > 1}


def cobre(a: set[str], b: set[str]) -> float:
    """Fração dos tokens de `a` que aparecem em `b` (aceita grafias parecidas: FOLLETTO~FOLETTO)."""
    if not a:
        return 0.0
    ok = sum(any(x == y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.85 for y in b) for x in a)
    return ok / len(a)


def ligar_autores(fav: pd.DataFrame, dep: pd.DataFrame, cand22: pd.DataFrame) -> pd.Series:
    """Nome do autor da emenda -> id_camara. Senadores e quem não é da legislatura 57 ficam sem id."""
    autores = (fav.groupby("autor").agg(v=("v", "sum"), uf=("UF Favorecido", lambda s: s.value_counts().index[0]))
               .reset_index())
    autores["limpo"] = (autores.autor.str.replace(r"\s*\(EX-PARLAMENTAR.*$", "", regex=True)
                        .str.replace(r"^DEP\.\s*", "", regex=True).map(norm))
    por_nome = dep.assign(n=dep.nome.map(norm)).set_index("n").id_camara
    autores["id_camara"] = autores.limpo.map(por_nome)
    autores["metodo"] = autores.id_camara.notna().map({True: "nome exato", False: None})

    # nível 2: nome de urna (TSE 2022, dep. federal) na mesma UF -> nome civil + nascimento -> Câmara
    c = cand22[cand22.DS_CARGO == "DEPUTADO FEDERAL"].assign(u=lambda d: d.NM_URNA_CANDIDATO.map(norm))
    c = c.drop_duplicates(["SG_UF", "u"], keep=False)
    chave_dep = dep.assign(k=dep.nome_civil.map(norm) + "|" + dep.nascimento.fillna("")).set_index("k").id_camara
    urna = c.assign(id_camara=c.chave.map(chave_dep)).dropna(subset=["id_camara"]).set_index(["SG_UF", "u"]).id_camara
    falta = autores.id_camara.isna()
    achou = [urna.get((r.uf, r.limpo)) for r in autores[falta].itertuples()]
    autores.loc[falta, "id_camara"] = achou
    autores.loc[falta & autores.id_camara.notna(), "metodo"] = "nome de urna 2022"

    # nível 3: tokens do nome (sem títulos) contidos no nome parlamentar ou civil de um deputado da mesma UF
    falta = autores.id_camara.isna()
    dep_tok = dep.assign(tk=[tokens(a) | tokens(b if isinstance(b, str) else "") for a, b in zip(dep.nome, dep.nome_civil)])
    for i, r in autores[falta].iterrows():
        suf_autor = set(r.limpo.split()) & SUFIXOS
        cands = dep_tok[(dep_tok.uf == r.uf) & dep_tok.nome.map(lambda n: set(norm(n).split()) & SUFIXOS <= suf_autor)]
        a = tokens(r.limpo)
        sc = cands.tk.map(lambda b: cobre(a, b))
        melhores = cands[sc == sc.max()] if len(sc) else cands.iloc[:0]
        if len(sc) and sc.max() == 1.0 and len(melhores) == 1 and len(a) >= 2:
            autores.loc[i, ["id_camara", "metodo"]] = [melhores.id_camara.iloc[0], "aproximado (conferir)"]
            log(f"  aproximado: '{r.autor[:60]}' ({r.uf}) -> {melhores.nome.iloc[0]} [{melhores.id_camara.iloc[0]}]")

    for nome, id_ in AUTORES_MANUAL.items():
        autores.loc[autores.autor == nome, ["id_camara", "metodo"]] = [id_, "manual"]

    uf_dep = autores.id_camara.map(dep.set_index("id_camara").uf)
    for r in autores[autores.id_camara.notna() & (uf_dep != autores.uf)].itertuples():
        log(f"  (info) {r.autor[:45]}: deputado do {uf_dep[r.Index]}, mas a maior parte do dinheiro foi para o {r.uf}")
    resumo = autores.groupby(autores.metodo.fillna("sem deputado (senador/outro)")).agg(
        autores=("autor", "size"), valor_bi=("v", lambda s: round(s.sum() / 1e9, 2)))
    log("[autores -> Câmara] (emendas individuais 2024-26, entes municipais)\n" + resumo.to_string())
    # conferência: quem ficou sem deputado deve ser senador (lista oficial do Senado, legislatura 57)
    sen = json.load(open(BRUTOS / "senado" / "senadores_leg57.json"))
    sen = {norm(p["IdentificacaoParlamentar"]["NomeParlamentar"])
           for p in sen["ListaParlamentarLegislatura"]["Parlamentares"]["Parlamentar"]}
    sem = autores[autores.id_camara.isna()].sort_values("v", ascending=False)
    eh_senador = sem.limpo.isin(sen)
    log(f"  sem deputado: {len(sem)} autores, dos quais {eh_senador.sum()} estão na lista de senadores da legislatura 57"
        f" (R$ {sem.v[eh_senador].sum() / 1e9:.2f} bi — fora da rede por definição)")
    if (~eh_senador).any():
        log("  ATENÇÃO, sem deputado e fora da lista do Senado:",
            "; ".join(f"{a} ({u}, R$ {v / 1e6:.1f} mi)" for a, u, v in sem.loc[~eh_senador, ["autor", "uf", "v"]]
                      .itertuples(index=False)))
    return autores.set_index("autor").id_camara


def ligar_municipios(fav: pd.DataFrame, mun: pd.DataFrame) -> pd.Series:
    mapa = {}
    for col in ["NM_MUNICIPIO_TSE", "nome"]:
        mapa.update({(u, norm(n)): c for u, n, c in zip(mun.uf, mun[col], mun.cd_ibge)})
    por_nome_ibge = {(u, norm(n)): c for u, n, c in zip(mun.uf, mun.nome, mun.cd_ibge)}
    for (u, n), nome_ibge in MUNICIPIOS_MANUAL.items():
        mapa[(u, norm(n))] = por_nome_ibge[(u, norm(nome_ibge))]
    chave = list(zip(fav["UF Favorecido"].fillna(""), fav["Município Favorecido"].map(norm)))
    return pd.Series([mapa.get(k) for k in chave], index=fav.index)


def camada_dinheiro(dep: pd.DataFrame, mun: pd.DataFrame, cand22: pd.DataFrame) -> pd.DataFrame:
    with zipfile.ZipFile(BRUTOS / "emendas" / "EmendasParlamentares.zip") as z:
        f = pd.read_csv(z.open("EmendasParlamentares_PorFavorecido.csv"), sep=";", encoding="latin1", dtype=str)
    f["v"] = reais(f["Valor Recebido"])
    f["ano_emenda"] = f["Código da Emenda"].str[:4]
    f["autor"] = f["Nome do Autor da Emenda"]
    log(f"[emendas] PorFavorecido: {len(f):,} pagamentos, R$ {f.v.sum() / 1e9:.1f} bi (todos os tipos e anos)")

    ind = f[f["Tipo de Emenda"].str.startswith("Emenda Individual") & f.ano_emenda.isin(ANOS_EMENDA)]
    log(f"  individuais de {min(ANOS_EMENDA)}-{max(ANOS_EMENDA)}: R$ {ind.v.sum() / 1e9:.2f} bi")
    antes = ind[ind["Ano/Mês"] <= CORTE_PAGAMENTO]
    log(f"  pagos até {CORTE_PAGAMENTO[4:]}/{CORTE_PAGAMENTO[:4]} (antes da eleição): R$ {antes.v.sum() / 1e9:.2f} bi")
    por_nat = antes.groupby(antes["Natureza Jurídica"].isin(NATUREZA_MUNICIPAL)).v.sum() / antes.v.sum()
    log(f"  fatia paga a entes municipais (prefeitura/fundo municipal): {por_nat.get(True, 0):.1%} "
        f"— o resto vai a estados, bancos (ex.: R$ da saúde via Banco do Brasil) e entidades privadas")
    e = antes[antes["Natureza Jurídica"].isin(NATUREZA_MUNICIPAL)].copy()

    e["cd_ibge"] = ligar_municipios(e, mun)
    sem_mun = e[e.cd_ibge.isna()]
    log(f"[municípios] valor casado com código IBGE: {1 - sem_mun.v.sum() / e.v.sum():.2%}")
    if len(sem_mun):
        log("  não casados:", sem_mun.groupby(["UF Favorecido", "Município Favorecido"]).v.sum()
            .sort_values(ascending=False).head(20).round(0).to_dict())

    e["id_camara"] = e.autor.map(ligar_autores(e, dep, cand22))
    log(f"[dinheiro] valor ligado a um deputado da legislatura 57: {e.v[e.id_camara.notna()].sum() / e.v.sum():.1%}")
    e = e.dropna(subset=["cd_ibge", "id_camara"])
    e["pix"] = e["Tipo de Emenda"].str.contains("Especiais")
    e["ano_pag"] = e["Ano/Mês"].str[:4]
    g = e.groupby(["id_camara", "cd_ibge"])
    arestas = pd.DataFrame({
        "valor": g.v.sum(),
        "valor_pix": e[e.pix].groupby(["id_camara", "cd_ibge"]).v.sum(),
        "valor_finalidade": e[~e.pix].groupby(["id_camara", "cd_ibge"]).v.sum(),
        **{f"valor_pago_{a}": e[e.ano_pag == a].groupby(["id_camara", "cd_ibge"]).v.sum() for a in sorted(ANOS_EMENDA)},
        "n_emendas": g["Código da Emenda"].nunique(),
    }).fillna(0).reset_index()
    arestas = arestas.merge(mun[["cd_ibge", "uf"]], on="cd_ibge").rename(columns={"uf": "uf_municipio"})
    arestas = arestas.merge(dep[["id_camara", "uf"]].rename(columns={"uf": "uf_deputado"}), on="id_camara")
    arestas["mesma_uf"] = arestas.uf_municipio == arestas.uf_deputado
    # só entra par com valor líquido > 0 (pagamento estornado = nada chegou ao município)
    sem_valor = arestas.valor.round(2) <= 0
    log(f"[dinheiro] pares com valor líquido ≤ 0 (pagamento estornado) removidos: {sem_valor.sum()} "
        f"(R$ {arestas.valor[sem_valor].sum():,.2f})")
    arestas = arestas[~sem_valor].reset_index(drop=True)
    log(f"[dinheiro] arestas: {len(arestas):,} | deputados: {arestas.id_camara.nunique()} | municípios: "
        f"{arestas.cd_ibge.nunique():,} | R$ {arestas.valor.sum() / 1e9:.2f} bi | "
        f"fora da UF do deputado: {arestas.valor[~arestas.mesma_uf].sum() / arestas.valor.sum():.2%} do valor")
    return arestas


# ---------------------------------------------------------------------------
# 5. Camada voto
# ---------------------------------------------------------------------------
def camada_voto(v: pd.DataFrame, dep: pd.DataFrame, ano: int) -> pd.DataFrame:
    fed = dep[dep[f"cargo_{ano}"] == "DEPUTADO FEDERAL"][["id_camara", f"sq_{ano}"]]
    a = v.merge(fed, left_on="SQ_CANDIDATO", right_on=f"sq_{ano}")
    a = a[a.QT_VOTOS_NOMINAIS_VALIDOS > 0]
    a = a.rename(columns={"SG_UF": "uf", "QT_VOTOS_NOMINAIS_VALIDOS": "votos"})
    sem_voto = set(fed.id_camara) - set(a.id_camara)
    log(f"[voto {ano}] deputados candidatos a dep. federal: {len(fed)} | com voto: {a.id_camara.nunique()} | "
        f"arestas (voto > 0): {len(a):,}")
    if sem_voto:
        sv = dep[dep.id_camara.isin(sem_voto)]
        ufs_vazias = set(dep.loc[dep[f"cargo_{ano}"] == "DEPUTADO FEDERAL", f"uf_tse_{ano}"]) - set(v.SG_UF)
        log(f"  sem nenhum voto válido: {len(sv)} — {sv[f'uf_tse_{ano}'].isin(ufs_vazias).sum()} do(s) estado(s) "
            f"{sorted(ufs_vazias)} (sem dados) e os demais com candidatura que não foi à urna ou foi anulada: "
            f"{sv.loc[~sv[f'uf_tse_{ano}'].isin(ufs_vazias), 'nome'].tolist()}")
    return a[["id_camara", "cd_ibge", "uf", "votos", "fatia_cidade", "fatia_candidato"]]


# ---------------------------------------------------------------------------
def main() -> None:
    PROC.mkdir(parents=True, exist_ok=True)
    pendentes = [a for a in (2022, 2026) if not (INTER / f"votos_depfed_{a}.csv.gz").exists()]
    if pendentes:
        print(f"Agregando votos de {pendentes} (alguns minutos; fica em cache)...", flush=True)
        with ProcessPoolExecutor(len(pendentes)) as ex:
            list(ex.map(agregar_votos, pendentes))

    mun = carregar_municipios()
    log(f"[municípios] tabela TSE↔IBGE: {len(mun):,} municípios")
    v26, v22 = votos_com_ibge(2026, mun), votos_com_ibge(2022, mun)

    dep = carregar_deputados()
    cand26, cand22 = carregar_candidatos(2026), carregar_candidatos(2022)
    dep = ligar_tse(dep, cand26, 2026)
    dep = ligar_tse(dep, cand22, 2022)

    din = camada_dinheiro(dep, mun, cand22)
    voto26, voto22 = camada_voto(v26, dep, 2026), camada_voto(v22, dep, 2022)

    dep["na_camada_dinheiro"] = dep.id_camara.isin(din.id_camara)
    dep["na_camada_voto_2026"] = dep.id_camara.isin(voto26.id_camara)
    dep["na_camada_voto_2022"] = dep.id_camara.isin(voto22.id_camara)
    log(f"[deputados] nas duas camadas (dinheiro e voto 2026): {(dep.na_camada_dinheiro & dep.na_camada_voto_2026).sum()} | "
        f"e também em 2022: {(dep.na_camada_dinheiro & dep.na_camada_voto_2026 & dep.na_camada_voto_2022).sum()}")

    xy = (pd.read_csv(BRUTOS / "geo" / "municipios_coordenadas.csv", dtype={"codigo_ibge": str})
          .set_index("codigo_ibge")[["latitude", "longitude"]])
    municipios = (mun.drop(columns="NM_MUNICIPIO_TSE").set_index("cd_ibge").join(xy)
                  .join(resumo_municipal(v26, 2026)).join(resumo_municipal(v22, 2022)).reset_index())
    for c in [c for c in municipios.columns if c.startswith(("votos_validos_", "n_candidatos_"))]:
        municipios[c] = municipios[c].round().astype("Int64")  # contagens como inteiros (o %.6g cortava dígitos)
    sem_xy = municipios[municipios.latitude.isna()]
    log(f"[municípios] com coordenadas: {len(municipios) - len(sem_xy)} de {len(municipios)}"
        + (f" — sem: {sem_xy.nome.tolist()}" if len(sem_xy) else ""))

    cols_dep = ["id_camara", "nome", "uf", "partidos_camara"] + [
        f"{c}_{a}" for a in (2026, 2022) for c in ("sq", "cargo", "uf_tse", "partido", "federacao", "nome_urna",
                                                   "situacao_candidatura", "resultado")] + [
        "na_camada_dinheiro", "na_camada_voto_2026", "na_camada_voto_2022"]
    # '#NULO'/'#NE' do TSE = não se aplica / não divulgado -> vazio (o '#NULO' de resultado_* fica: "sem resultado")
    for c in [c for c in cols_dep if c.startswith(("federacao_", "situacao_candidatura_"))]:
        dep[c] = dep[c].replace({"#NULO": None, "#NE": None})
    dep[cols_dep].to_csv(PROC / "deputados.csv", index=False)
    municipios.to_csv(PROC / "municipios.csv", index=False, float_format="%.6g")
    din.to_csv(PROC / "arestas_dinheiro.csv", index=False, float_format="%.2f")
    voto26.to_csv(PROC / "arestas_voto_2026.csv", index=False, float_format="%.6g")
    voto22.to_csv(PROC / "arestas_voto_2022.csv", index=False, float_format="%.6g")
    (PROC / "relatorio_construcao.txt").write_text("\n".join(_LOG) + "\n")
    log("\nArquivos gravados em", PROC.relative_to(RAIZ))


if __name__ == "__main__":
    sys.exit(main())
