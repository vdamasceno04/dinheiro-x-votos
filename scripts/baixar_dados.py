"""Baixa todas as bases públicas do projeto "Emenda vira voto?".

Uso (a partir da raiz do repositório):
    python3 scripts/baixar_dados.py            # baixa só o que ainda não existe
    python3 scripts/baixar_dados.py --forcar   # baixa tudo de novo

Os arquivos vão para dados/brutos/ (fora do git, ~1,1 GB). Cada download fica
registrado em dados/manifesto_download.json (data, tamanho, sha256, Last-Modified),
que VAI para o git: assim fica registrado de qual versão dos dados saíram os números.

Só usa a biblioteca padrão do Python.
"""
import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BRUTOS = RAIZ / "dados" / "brutos"
MANIFESTO = RAIZ / "dados" / "manifesto_download.json"

TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele"
CAMARA = "https://dadosabertos.camara.leg.br"

# destino (relativo a dados/brutos) -> (url, descrição)
FONTES = {
    "emendas/EmendasParlamentares.zip": (
        "https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares/UNICO",
        "Portal da Transparência: emendas parlamentares (3 CSVs, inclui o PorFavorecido com o município)",
    ),
    "tse/votacao_candidato_munzona_2026.zip": (
        f"{TSE}/votacao_candidato_munzona/votacao_candidato_munzona_2026.zip",
        "TSE: votos de cada candidato por município e zona, eleição 2026",
    ),
    "tse/votacao_candidato_munzona_2022.zip": (
        f"{TSE}/votacao_candidato_munzona/votacao_candidato_munzona_2022.zip",
        "TSE: votos de cada candidato por município e zona, eleição 2022",
    ),
    "tse/consulta_cand_2026.zip": (
        f"{TSE}/consulta_cand/consulta_cand_2026.zip",
        "TSE: candidatos 2026 (partido, cargo, UF, situação)",
    ),
    "tse/consulta_cand_2022.zip": (
        f"{TSE}/consulta_cand/consulta_cand_2022.zip",
        "TSE: candidatos 2022 (partido, cargo, UF, situação)",
    ),
    "tse/municipio_tse_ibge.zip": (
        f"{TSE}/municipio_tse_ibge/municipio_tse_ibge.zip",
        "TSE: correspondência código de município TSE <-> IBGE",
    ),
    "camara/deputados.csv": (
        f"{CAMARA}/arquivos/deputados/csv/deputados.csv",
        "Câmara: todos os deputados da história (nome civil, nascimento, legislaturas)",
    ),
    "camara/deputados_leg57.json": (
        f"{CAMARA}/api/v2/deputados?idLegislatura=57&itens=1000&ordem=ASC&ordenarPor=nome",
        "Câmara: deputados que exerceram mandato na legislatura 57 (2023-2027)",
    ),
    "geo/municipios_coordenadas.csv": (
        "https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv",
        "Coordenadas (latitude/longitude da sede) de cada município, por código IBGE — para mapas",
    ),
    "senado/senadores_leg57.json": (
        "https://legis.senado.leg.br/dadosabertos/senador/lista/legislatura/57",
        "Senado: senadores da legislatura 57 (para conferir que os autores sem deputado são senadores)",
    ),
}

CABECALHOS = {"User-Agent": "Mozilla/5.0 (projeto academico)", "Accept": "application/json, */*"}


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar(url: str, destino: Path, tentativas: int = 3) -> dict:
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".part")
    for i in range(1, tentativas + 1):
        try:
            req = urllib.request.Request(url, headers=CABECALHOS)
            with urllib.request.urlopen(req, timeout=120) as resp, open(parcial, "wb") as f:
                total = int(resp.headers.get("Content-Length") or 0)
                feito, ultimo = 0, 0.0
                while bloco := resp.read(1 << 20):
                    f.write(bloco)
                    feito += len(bloco)
                    if time.time() - ultimo > 5:  # progresso a cada 5 s
                        pct = f"{100 * feito / total:5.1f}%" if total else ""
                        print(f"    {feito / 1e6:8.1f} MB {pct}", flush=True)
                        ultimo = time.time()
                info = {
                    "url_final": resp.geturl(),
                    "last_modified": resp.headers.get("Last-Modified"),
                }
            parcial.replace(destino)
            return info
        except Exception as e:  # rede instável: tenta de novo
            print(f"    falhou (tentativa {i}/{tentativas}): {e}", flush=True)
            time.sleep(5 * i)
    raise RuntimeError(f"não foi possível baixar {url}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forcar", action="store_true", help="baixa de novo mesmo se o arquivo já existir")
    args = ap.parse_args()

    livre = shutil.disk_usage(RAIZ).free / 1e9
    print(f"Espaço livre: {livre:.1f} GB (precisa de ~1,2 GB)")
    if livre < 2:
        sys.exit("Pouco espaço em disco.")

    manifesto = json.loads(MANIFESTO.read_text()) if MANIFESTO.exists() else {}
    for rel, (url, desc) in FONTES.items():
        destino = BRUTOS / rel
        if destino.exists() and not args.forcar:
            print(f"[ok] {rel} já existe ({destino.stat().st_size / 1e6:.1f} MB)")
            continue
        print(f"[baixando] {rel}\n    {url}", flush=True)
        info = baixar(url, destino)
        manifesto[rel] = {
            "descricao": desc,
            "url": url,
            **info,
            "baixado_em": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "bytes": destino.stat().st_size,
            "sha256": sha256(destino),
        }
        MANIFESTO.write_text(json.dumps(manifesto, indent=2, ensure_ascii=False) + "\n")
        print(f"    pronto: {destino.stat().st_size / 1e6:.1f} MB", flush=True)

    print("\nTudo baixado. Manifesto em", MANIFESTO.relative_to(RAIZ))


if __name__ == "__main__":
    main()
