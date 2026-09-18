#!/usr/bin/env python3
"""
Sincroniza o mtime (data de modificacao no sistema de arquivos) dos
arquivos cuja data veio do NOME do arquivo (date_source == 'filename' em
organized.csv) para bater com a data real.

Por que isso importa: ferramentas como o Immich usam o mtime como
fallback de "data da foto" quando o arquivo nao tem EXIF - e e exatamente
por nao ter EXIF que esses arquivos foram parar em date_source=filename.
Sem essa sincronizacao, a ferramenta mostra a data em que voce RODOU o
pipeline, nao a data real da foto.

Arquivos com date_source == 'exif' nao precisam disso: qualquer leitor de
EXIF (Immich incluso) pega a data direto do metadado embutido,
independente do mtime.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import datetime_to_epoch, extract_datetime_from_filename  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--organized", required=True, help="organized.csv produzido por organize.py")
    args = ap.parse_args()

    with open(args.organized, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    fixed = no_date = missing = 0
    for row in rows:
        if row["date_source"] != "filename":
            continue
        path = row["new_path"]
        if not os.path.exists(path):
            missing += 1
            continue
        dt = extract_datetime_from_filename(os.path.basename(path))
        if not dt:
            no_date += 1
            continue
        ts = datetime_to_epoch(dt)
        if ts is None:
            no_date += 1
            continue
        os.utime(path, (ts, ts))
        fixed += 1

    print(f"[fix-mtime] corrigidos: {fixed}")
    print(f"[fix-mtime] sem data extraivel do nome: {no_date}")
    print(f"[fix-mtime] arquivo nao encontrado: {missing}")


if __name__ == "__main__":
    main()
