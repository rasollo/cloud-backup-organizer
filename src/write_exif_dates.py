#!/usr/bin/env python3
"""
Grava a data real (extraida do nome do arquivo) como EXIF de verdade nos
arquivos com date_source == 'filename' em organized.csv.

Por que isso importa (alem do que fix_mtime.py ja resolve): ferramentas
como o Immich, ao reescanear uma biblioteca externa (somente leitura),
RELEEM o metadado do proprio arquivo e podem sobrescrever qualquer data
que so exista no banco delas (por exemplo se voce editou a data
manualmente pela interface) - se o arquivo nao tem EXIF nenhum, nao ha
nada estavel pra "preservar", e a data mostrada fica instavel a cada
scan. Gravar a data como EXIF de verdade no arquivo resolve isso de vez:
a partir daí a data e uma propriedade do proprio arquivo, nao um palpite
externo que pode ser perdido.

Usa uma unica chamada ao exiftool com um "argfile" (grupo de tags +
caminho por arquivo) para escrever milhares de arquivos de uma vez, bem
mais rapido que um processo por arquivo.
"""
import argparse
import csv
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import extract_datetime_from_filename  # noqa: E402

ARGFILE = "/tmp/_write_exif_dates_argfile.txt"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--organized", required=True)
    ap.add_argument("--dry-run", action="store_true", help="so mostra quantos arquivos seriam afetados, nao grava nada")
    args = ap.parse_args()

    with open(args.manifest, newline="", encoding="utf-8") as f:
        manifest_by_sha = {r["sha256"]: r for r in csv.DictReader(f)}
    with open(args.organized, newline="", encoding="utf-8") as f:
        organized_rows = list(csv.DictReader(f))

    targets = [r for r in organized_rows if r["date_source"] == "filename"]
    print(f"[write-exif] arquivos com date_source=filename: {len(targets)}", flush=True)

    resolved = missing = no_date = 0
    argfile_lines = []

    for row in targets:
        mrow = manifest_by_sha.get(row["sha256"])
        if not mrow:
            missing += 1
            continue
        original_basename = os.path.basename(mrow["entry_name"])

        path = row["new_path"]
        if not os.path.exists(path):
            missing += 1
            continue

        dt = extract_datetime_from_filename(original_basename)
        if not dt:
            no_date += 1
            continue
        y, mo, d, h, mi, s = dt
        ts = f"{y:04d}:{mo:02d}:{d:02d} {h:02d}:{mi:02d}:{s:02d}"

        for tag in ("DateTimeOriginal", "CreateDate", "ModifyDate", "QuickTime:CreateDate",
                    "QuickTime:ModifyDate", "TrackCreateDate", "TrackModifyDate",
                    "MediaCreateDate", "MediaModifyDate"):
            argfile_lines.append(f"-{tag}={ts}")
        argfile_lines.append("-overwrite_original")
        argfile_lines.append(path)
        argfile_lines.append("-execute")
        resolved += 1

    print(f"[write-exif] resolvidos: {resolved} | caminho nao encontrado: {missing} | sem data extraivel do nome: {no_date}", flush=True)

    if args.dry_run:
        print("[write-exif] --dry-run: nada foi gravado.")
        return
    if not resolved:
        print("[write-exif] nada a gravar.")
        return

    with open(ARGFILE, "w", encoding="utf-8") as f:
        f.write("\n".join(argfile_lines))

    print(f"[write-exif] rodando exiftool em {resolved} arquivos...", flush=True)
    result = subprocess.run(["exiftool", "-@", ARGFILE], capture_output=True, text=True)
    print(result.stdout[-3000:])
    if result.returncode not in (0, 1):
        print(result.stderr[-3000:], file=sys.stderr)


if __name__ == "__main__":
    main()
