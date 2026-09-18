#!/usr/bin/env python3
"""
Recupera fotos "de verdade" de dentro da pasta sem-data pra revisao, em
vez de deixar tudo largado como lixo generico. Foco em fotos que vieram
de mensageria (nome UUID, sem EXIF, sem data no nome) mas que pela
resolucao claramente sao fotos de camera reais, nao icone/thumbnail.

Prioriza por confianca do sinal disponivel:
1. GPS presente no proprio arquivo (mais confiavel, mesmo sem data)
2. Faixa de data ESTREITA e sem outlier entre os "vizinhos" (arquivos do
   MESMO zip de origem que tem data conhecida) - usa o INICIO da faixa
   como palpite aproximado
3. Sem nenhum sinal - fica separado mas sinalizado como tal

Duas licoes aprendidas na pratica que este script ja incorpora:
- Datas de EXIF anteriores a --min-plausible-year sao descartadas antes
  de calcular a faixa (uma unica foto com relogio de camera zerado/
  resetado pode arrastar a faixa inteira pra uma data absurda).
- Zips que representam a biblioteca INTEIRA fatiada arbitrariamente (ex:
  "iCloud Photos Part 4 of 23" - nao e uma sessao por tempo, e so um
  pedaco arbitrario da biblioteca toda) dao faixas larguissimas (anos de
  intervalo) que NAO sao um sinal util - useis por --max-range-months pra
  descartar faixas largas demais em vez de dar um palpite ruim.
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
from collections import defaultdict

from PIL import Image, UnidentifiedImageError


def find_candidates(root, min_megapixels):
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in ("jpg", "jpeg", "png"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with Image.open(path) as im:
                    w, h = im.size
            except (UnidentifiedImageError, OSError):
                continue
            if (w * h) / 1_000_000 >= min_megapixels:
                candidates.append(path)
    return candidates


def check_gps(paths):
    if not paths:
        return set()
    listfile = "/tmp/_recover_gps_paths.txt"
    with open(listfile, "w", encoding="utf-8") as f:
        f.write("\n".join(paths))
    result = subprocess.run(["exiftool", "-j", "-q", "-GPSLatitude", "-GPSLongitude", "-@", listfile],
                             capture_output=True, text=True)
    data = json.loads(result.stdout)
    return {d["SourceFile"] for d in data if "GPSLatitude" in d}


def compute_zip_ranges(manifest, organized_by_sha, min_plausible_year, max_range_months):
    dates_by_zip = defaultdict(list)
    for r in manifest:
        org = organized_by_sha.get(r["sha256"])
        if org and org["date_source"] in ("exif", "filename") and org["date_used"]:
            y, m = (int(x) for x in org["date_used"].split("-"))
            if y < min_plausible_year:
                continue
            dates_by_zip[r["source_zip_relpath"]].append((y, m))

    ranges = {}
    for zip_path, dates in dates_by_zip.items():
        dates.sort()
        n = len(dates)
        if n < 3:
            continue
        lo = dates[int(n * 0.02)]
        hi = dates[int(n * 0.98) - 1] if n > 1 else dates[-1]
        months = (hi[0] * 12 + hi[1]) - (lo[0] * 12 + lo[1])
        if months <= max_range_months:
            ranges[zip_path] = lo
    return ranges


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="pasta sem-data (ou qualquer pool de fotos sem data confiavel) a vasculhar")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--organized", required=True)
    ap.add_argument("--review-dir", required=True)
    ap.add_argument("--min-megapixels", type=float, default=1.0)
    ap.add_argument("--min-plausible-year", type=int, default=2005,
                     help="descarta datas de vizinhos anteriores a este ano ao calcular a faixa (default: 2005 - ajuste pra baixo se sua colecao for mais antiga)")
    ap.add_argument("--max-range-months", type=int, default=48,
                     help="faixa maxima (em meses) pra confiar na estimativa de epoca (default: 48 = 4 anos)")
    args = ap.parse_args()

    candidates = find_candidates(args.root, args.min_megapixels)
    print(f"[recover] candidatos (>= {args.min_megapixels}MP): {len(candidates)}", flush=True)
    if not candidates:
        return

    gps_paths = check_gps(candidates)
    print(f"[recover] com GPS: {len(gps_paths)}", flush=True)

    with open(args.manifest, newline="", encoding="utf-8") as f:
        manifest = list(csv.DictReader(f))
    with open(args.organized, newline="", encoding="utf-8") as f:
        organized_by_sha = {r["sha256"]: r for r in csv.DictReader(f)}

    basename_to_rows = defaultdict(list)
    for r in manifest:
        basename_to_rows[os.path.basename(r["entry_name"])].append(r)

    ranges = compute_zip_ranges(manifest, organized_by_sha, args.min_plausible_year, args.max_range_months)
    print(f"[recover] zips com faixa de data confiavel (<= {args.max_range_months} meses): {len(ranges)}", flush=True)

    count_gps = count_era = count_none = 0
    for path in candidates:
        basename = os.path.basename(path)
        has_gps = path in gps_paths

        zip_path = None
        for r in basename_to_rows.get(basename, []):
            if r["source_zip_relpath"] in ranges:
                zip_path = r["source_zip_relpath"]
                break

        if has_gps:
            dest_sub = "com-localizacao"
            count_gps += 1
        elif zip_path:
            year = ranges[zip_path][0]
            dest_sub = f"por-epoca-aproximada/{year}"
            count_era += 1
        else:
            dest_sub = "sem-pista"
            count_none += 1

        dest_dir = os.path.join(args.review_dir, dest_sub)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, basename)
        i = 2
        base, ext = os.path.splitext(basename)
        while os.path.exists(dest_path):
            dest_path = os.path.join(dest_dir, f"{base}__{i}{ext}")
            i += 1
        shutil.move(path, dest_path)

    print(f"[recover] com GPS: {count_gps} | epoca aproximada: {count_era} | sem pista: {count_none}")


if __name__ == "__main__":
    main()
