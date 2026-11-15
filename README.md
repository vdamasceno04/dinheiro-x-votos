# Emenda vira voto?

Rede bipartida **deputado federal – município** com duas camadas: **dinheiro** (emendas parlamentares
individuais 2024–26) e **voto** (eleição de 2026, com 2022 como linha de base). A pergunta: a emenda traz o voto,
ou o deputado manda dinheiro para onde já tem voto?

## Como rodar

```bash
pip install -r requirements.txt

# só se quiser refazer a rede do zero (os resultados já estão em dados/processados/):
python3 scripts/baixar_dados.py      # ~1,1 GB em dados/brutos/ (fora do git)
python3 scripts/construir_rede.py    # ~2 min; regrava dados/processados/
```

Para usar a rede, **não precisa baixar nada**: as tabelas estão em `dados/processados/`, que está no git.

## TODO

- [ ] **Buscar os votos de 2026 do Amapá.** O arquivo de votação de 2026 do TSE ainda não traz os votos para
  deputado federal do AP, então os 16 municípios do AP estão fora da camada voto 2026. Quando o TSE publicar:
  apagar `dados/brutos/tse/votacao_candidato_munzona_2026.zip`, `dados/brutos/tse/consulta_cand_2026.zip` e
  `dados/intermediarios/votos_depfed_2026.csv.gz`, e rodar `baixar_dados.py` e `construir_rede.py` de novo.

## Estrutura

```
dados/
  manifesto_download.json   de onde e quando cada base foi baixada (sha256)
  brutos/                   bases originais (fora do git)
  intermediarios/           cache da agregação dos votos (fora do git)
  processados/              tabelas da rede — ver dados/processados/LEIA-ME.md
scripts/                    download dos dados e construção da rede
```

## Fontes (todas públicas, sem login)

| Base | Fonte |
|---|---|
| Emendas parlamentares (pagamentos por favorecido) | Portal da Transparência — CGU |
| Votação por candidato, município e zona — 2022 e 2026 | TSE — Portal de Dados Abertos |
| Candidatos 2022 e 2026 | TSE — Portal de Dados Abertos |
| Correspondência de municípios TSE ↔ IBGE | TSE — Portal de Dados Abertos |
| Deputados da 57ª legislatura | Dados Abertos da Câmara dos Deputados |
| Senadores da 57ª legislatura (conferência dos autores) | Dados Abertos do Senado Federal |
| Coordenadas dos municípios (para mapas) | repositório `kelvins/municipios-brasileiros` (GitHub) |
