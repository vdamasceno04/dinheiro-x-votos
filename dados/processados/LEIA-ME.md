# Dados processados — rede deputado–município

Gerados por `scripts/construir_rede.py` a partir das bases em `dados/brutos/` (versões e datas em
`dados/manifesto_download.json`). O passo a passo do cruzamento, com quanto casou e quanto ficou de fora,
está em [`relatorio_construcao.txt`](relatorio_construcao.txt).

**Chaves:** deputado = `id_camara` (id da Câmara), município = `cd_ibge` (7 dígitos). Leia as duas como **texto**:

```python
import pandas as pd
P = "dados/processados/"
ids = {"id_camara": str, "cd_ibge": str}
dep = pd.read_csv(P + "deputados.csv", dtype=str)
mun = pd.read_csv(P + "municipios.csv", dtype={"cd_ibge": str, "cd_tse": str})
din = pd.read_csv(P + "arestas_dinheiro.csv", dtype=ids)
v26 = pd.read_csv(P + "arestas_voto_2026.csv", dtype=ids)
v22 = pd.read_csv(P + "arestas_voto_2022.csv", dtype=ids)

# exemplo: camada dinheiro como grafo bipartido do networkx
import networkx as nx
G = nx.Graph()
G.add_nodes_from("d" + din.id_camara.unique(), bipartite=0)
G.add_nodes_from("m" + din.cd_ibge.unique(), bipartite=1)
G.add_weighted_edges_from(zip("d" + din.id_camara, "m" + din.cd_ibge, din.valor))
```

(prefixe `d`/`m` nos nós para os ids de deputado e município nunca colidirem)

## Tamanho

| Camada | Deputados | Municípios | Arestas | Peso |
|---|---|---|---|---|
| dinheiro (emendas 2024–26) | 545 | 5.531 | 32.786 | R$ 32,96 bi |
| voto 2026 | 490 | 5.555 | 148.469 | fatia da cidade |
| voto 2022 (linha de base) | 644 | 5.570 | 187.107 | fatia da cidade |

**426** deputados estão nas camadas dinheiro e voto 2026 (424 também em 2022).

## Arquivos

### `deputados.csv` — 648 deputados que exerceram mandato na 57ª legislatura (titulares e suplentes)

| coluna | significado |
|---|---|
| `id_camara` | id do deputado na Câmara |
| `nome`, `uf` | nome parlamentar e UF de eleição (Câmara) |
| `partidos_camara` | partidos pelos quais passou na legislatura, na ordem da API (ex.: `PL/UNIÃO`) |
| `sq_2026`, `sq_2022` | id do candidato no TSE (vazio = não concorreu a nada naquele ano) |
| `cargo_*` | cargo disputado (DEPUTADO FEDERAL, SENADOR, GOVERNADOR, …) |
| `uf_tse_*`, `partido_*`, `federacao_*`, `nome_urna_*` | dados da candidatura |
| `resultado_*` | ELEITO POR QP / ELEITO POR MÉDIA / SUPLENTE / NÃO ELEITO / `#NULO` (sem resultado) |
| `na_camada_dinheiro`, `na_camada_voto_2026`, `na_camada_voto_2022` | se o deputado tem aresta em cada camada |

### `municipios.csv` — 5.571 municípios

| coluna | significado |
|---|---|
| `cd_ibge`, `cd_tse`, `nome`, `uf` | identificação |
| `latitude`, `longitude` | coordenadas da sede do município (para mapas) |
| `votos_validos_<ano>` | votos nominais válidos para deputado federal no município (todos os candidatos) |
| `n_candidatos_<ano>` | quantos candidatos a deputado federal tiveram voto ali |

### `arestas_dinheiro.csv` — camada dinheiro

Uma linha por par deputado–município que recebeu emenda **individual** do deputado.

| coluna | significado |
|---|---|
| `valor` | R$ pagos ao município (prefeitura ou fundo municipal), soma de todas as emendas |
| `valor_pix` | parte via transferência especial ("emenda Pix") |
| `valor_finalidade` | parte via transferência com finalidade definida |
| `valor_pago_2024/2025/2026` | `valor` separado pelo **ano do pagamento** |
| `n_emendas` | quantas emendas diferentes do deputado chegaram ao município |
| `uf_municipio`, `uf_deputado`, `mesma_uf` | 0,53% do valor vai para fora da UF do deputado |

Regras: emendas individuais com **ano de emenda 2024–2026** (as de 2023 foram escritas pela legislatura anterior);
**pagamentos até 09/2026** (o 1º turno foi em 04/10/2026); só pagamentos a **entes municipais**
(natureza jurídica "Município" ou "Fundo Público da Administração Direta Municipal"); só pares com **valor líquido
maior que zero** (pagamentos menos estornos).

### `arestas_voto_2026.csv` e `arestas_voto_2022.csv` — camada voto

Uma linha por par deputado–município com **pelo menos 1 voto válido** (só deputados da 57ª legislatura que
disputaram deputado federal naquele ano).

| coluna | significado |
|---|---|
| `votos` | votos nominais válidos do deputado no município (soma das zonas) |
| `fatia_cidade` | `votos` ÷ votos nominais válidos de **todos** os candidatos a dep. federal no município → **peso principal** |
| `fatia_candidato` | `votos` ÷ total de votos do deputado no estado (quanto da votação dele veio dali) |

## Limitações dos dados

- **Amapá fora de 2026.** Na versão do arquivo do TSE usada aqui (ver `dados/manifesto_download.json`), o AP não
  tem nenhum voto de deputado federal e os 91 candidatos estão sem resultado. Os 16 municípios do AP e os 11 deputados
  de lá ficam fora da camada voto 2026. Em 2022 o AP está completo.
- **Deputados do DF.** O DF tem um município só (Brasília), e o dinheiro que vai para o governo do DF não conta
  como ente municipal. Por isso o dinheiro "municipal" desses deputados vai para cidades de outros estados
  (sobretudo GO; alguns mandaram mais para o RS), onde eles não recebem voto.
- **Senadores ficam de fora por definição.** 81 autores de emenda (R$ 9,7 bi) não são deputados, e todos foram
  conferidos contra a lista oficial do Senado.
- **Dinheiro sem município.** 31% do valor das emendas individuais vai para estados, bancos e entidades privadas.
  Um exemplo é a parte da saúde paga via Banco do Brasil. Esse valor não entra na rede.
- **Camada voto quase completa.** Um deputado tem voto em quase todos os municípios do estado, então quase todo
  par deputado–município do estado tem aresta; o que muda de um par para outro é o peso (`fatia_cidade`).
- **Emendas "EX-PARLAMENTAR".** Quando o titular sai, a emenda passa ao sucessor (ex.: "JOAO CURY NETO
  (EX-PARLAMENTAR ALBERTO MOURAO…)"). Ela fica com o deputado que aparece como autor.
- **8 deputados disputaram em 2026 sem nenhum voto válido** (candidatura que não foi à urna ou foi anulada).
  Eles estão em `deputados.csv`, mas sem arestas de voto 2026.
