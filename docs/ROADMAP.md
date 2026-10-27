# Roteiro: de scripts para um software

Este repositório nasceu de uma organização real (~190 mil fotos e vídeos de
iCloud, Google Takeout, OneDrive, HDs antigos e imagens Ghost, servidos pelo
Immich). Os scripts de `src/` resolvem a parte genérica. Outra parte do
trabalho ainda vive em scripts de uso único, presos a caminhos de uma
máquina. Este documento serve de lembrete para repetir o processo quando
chegar um backup novo e de base para transformar tudo num software.

## Checklist para um backup ou disco novo

1. **Inventário e extração** (`inventory.py`, `extract.py`): deduplica por
   sha256. Nada é apagado da origem.
2. **Organização por data** (`organize.py`, `fix_mtime.py`,
   `write_exif_dates.py`): EXIF, depois data no nome, depois `sem-data/`.
3. **Metadados de fora do arquivo** (`sidecar_metadata.py`): `.json` do
   Takeout e `Photo Details.csv` do iCloud. Rode antes de dar algo como
   "sem data".
4. **Limpeza** (`live_photos.py`, `dedupe_*`, `resolve_conflicts.py`) e
   `recover_undated_photos.py` para o que sobrar sem data.
5. **Conferir antes de apagar a origem**: veja abaixo, ainda não há
   ferramenta genérica.
6. **Revisão humana** de tudo que não tiver prova forte: veja abaixo.
7. Só então apagar backups, **um por vez**, com registro do que foi apagado.

## Ainda fora de `src/` (candidatos a módulo)

### Conferência de backups antigos antes de apagar
Para cada mídia de um backup (inclusive dentro de zips), procurar prova de
que ela já está na biblioteca. Use as provas da mais forte para a mais
fraca e pare na primeira que encontrar:

| Status | Prova |
|---|---|
| `no-by-date` / `em-outra-pasta` | mesmo sha256 |
| `descartado-na-organizacao` | sha256 numa pasta de descartados (decisão já tomada) |
| `apagado-pelo-usuario` | mesmo tamanho + extensão de algo apagado pela lixeira do Immich |
| `site-ou-icone` | caminho de site (`wp-content`, `public_html`...) ou < 20 KB |
| `data+imagem` | mesma data (±2 s, aceitando fuso de horas inteiras) e miniatura 24×24 parecida, testando rotações |
| `data+duracao` | vídeo com mesma data e duração ±1 s |
| `imagem` | miniatura parecida em toda a biblioteca, confirmada por comparação fina 128×128; **nunca** para prints de app, porque telas do mesmo app se parecem |
| `falta` | nenhuma das anteriores: vai para revisão |

Lições:
- O sha256 das entradas de zip já calculado na extração serve de cache.
  Recalcular 1 TB de zips leva horas.
- Fotos recomprimidas pelo Google ou mídia de WhatsApp sem EXIF não casam
  por data e nem sempre por imagem. Mostre ao lado o arquivo de mesmo nome
  na biblioteca, para o humano decidir rápido.

### Página de revisão (só na rede local)
Padrão que funcionou bem nas três revisões feitas:
- servidor HTTP mínimo, ouvindo só na LAN, **nunca** publicado (são fotos
  pessoais);
- miniaturas geradas por ffmpeg e cacheadas; entradas de zip extraídas para
  um temporário; para HEIC, usar o preview do Immich, porque o ffmpeg só
  decodifica o primeiro tile;
- cards agrupados (mês ou pasta de origem), alternar com um clique, botões
  "todos/nenhum" que agem **só nos cards visíveis**, filtros e zoom;
- "Salvar" grava um JSON com a decisão. A página não move nem apaga nada:
  outro script lê o JSON, simula e só executa com `--executar`.

### Lixeira do Immich com biblioteca externa somente leitura
Apagar no Immich não apaga o arquivo, porque a montagem é `:ro`. A lixeira
se esvazia em 30 dias e o próximo scan reimporta o arquivo. Fluxo adotado:
mover os arquivos da lixeira para uma quarentena no mesmo sistema de
arquivos (`os.rename`, instantâneo), verificar duplicatas, revisar na
página e só então apagar de vez, devolvendo os marcados como "manter".

### Copiar o que faltava de volta para o by-date
Ordem da data: EXIF, `.json` do Takeout ao lado, CSV do iCloud no mesmo
zip, data no nome do arquivo ou da pasta (`Masters/AAAA/MM/DD/AAAAMMDD-HHMMSS/`
da biblioteca do Fotos do Mac) e, só para uploads do Immich, o mtime.
Conferir o sha256 da cópia. Nunca sobrescrever: se o nome já existir com
outro conteúdo, usar o sufixo `_1`.

## Lições de operação

- **Docker antes dos discos**: depois de uma queda de energia, o Docker
  subiu antes da montagem de `/data/...` e o Immich abriu um banco vazio.
  Resolva com `RequiresMountsFor=` num drop-in do `docker.service`.
- **Rodar desacoplado** (`nohup`/systemd) qualquer coisa que leve mais de
  alguns minutos. Um reinício da sessão de terminal matou rodadas longas
  duas vezes.
- **Registro de desfazer gravado linha a linha, antes de cada ação.** Um
  registro gravado só no fim se perde se o processo cair no meio.
- **Simular por padrão**: `--executar`/`--execute` explícito, com a
  contagem mostrada antes.
- **SMART** antes de confiar num disco antigo para backup (`smartctl -x`):
  setores realocados e pendentes, e `Load_Cycle_Count` em HD de notebook.

## Ideias para o software

- **Catálogo único em SQLite** no lugar dos CSVs: arquivo (sha256,
  tamanho, data, fonte da data, GPS), locais (disco, zip, entrada),
  decisões (manter, descartar, revisar, com quem e quando) e operações
  (de, para, desfazer).
- **Fontes como plugins**: Takeout, iCloud, OneDrive, HD solto, imagem
  Ghost, upload-library do Immich. Cada uma sabe listar, ler e achar
  metadados de fora do arquivo.
- **Fila de tarefas retomável**: hash, miniatura, EXIF e comparação, com
  progresso e tempo estimado. Sobrevive a reinício.
- **Interface web local** para todas as revisões, com o padrão de página
  descrito acima.
- **Integração com o Immich pela API**: disparar scan, ler a lixeira, achar
  o preview de um asset, em vez de rodar SQL dentro do container.
- **Nada destrutivo sem decisão registrada**: apagar exige uma decisão
  salva para aquele arquivo e uma simulação mostrada antes.
