#!/usr/bin/env python3
"""
Conflitos entre fontes: acha fotos DIFERENTES (sha256 diferente, ja
sobreviveram a dedup exata e a dedupe_visual.py) que tem o EXATO MESMO
TIMESTAMP EXIF (ate o segundo) - sinal forte de que sao a MESMA foto
exportada por pipelines diferentes (ex: Google Fotos gerou um JPEG a
partir da mesma HEIC original da Apple).

So compara IMAGENS entre si (exclui video, pra nao confundir com o
companheiro de Live Photo, que legitimamente compartilha o mesmo
timestamp).

Score = resolucao (largura x altura) como criterio principal, tamanho do
arquivo como desempate. O de menor score de cada grupo e movido (nao
apagado) para --review-dir.
"""
import argparse
import csv
import json
import os
import re
from collections import defaultdict

from PIL import Image, UnidentifiedImageError

IMG_EXTS = {"heic", "heif", "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp"}
DATE_TAGS = ["DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate", "GPSDateTime"]
DATE_RE = re.compile(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}")


def best_timestamp(entry):
    for tag in DATE_TAGS:
        if tag in entry:
            m = DATE_RE.match(entry[tag])
            if m:
                return m.group(0)
    return None


def unique_dest_path(dest_path, disambiguator):
    if not os.path.exists(dest_path):
        return dest_path
    base, ext = os.path.splitext(dest_path)
    candidate = f"{base}__{disambiguator}{ext}"
    if not os.path.exists(candidate):
        return candidate
    i = 2
    while True:
        candidate = f"{base}__{disambiguator}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="pasta organizada por data (by-date)")
    ap.add_argument("--exif-cache", required=True, help="exif-cache.json produzido por organize.py")
    ap.add_argument("--organized", required=True, help="organized.csv produzido por organize.py")
    ap.add_argument("--report", required=True)
    ap.add_argument("--review-dir", required=True)
    args = ap.parse_args()

    with open(args.organized, newline="", encoding="utf-8") as f:
        organized_rows = list(csv.DictReader(f))
    organized_by_sha = {r["sha256"]: r for r in organized_rows}
    old_path_to_sha = {r["old_path"]: r["sha256"] for r in organized_rows}

    def resolve(sha256):
        org = organized_by_sha.get(sha256)
        if not org or not os.path.exists(org["new_path"]):
            return None
        return org["new_path"]

    with open(args.exif_cache, encoding="utf-8") as f:
        exif_raw = json.load(f)

    ts_groups = defaultdict(set)
    for entry in exif_raw:
        old_path = entry.get("SourceFile")
        sha = old_path_to_sha.get(old_path)
        if not sha:
            continue
        ext = old_path.rsplit(".", 1)[-1].lower() if "." in old_path else ""
        if ext not in IMG_EXTS:
            continue
        ts = best_timestamp(entry)
        if not ts:
            continue
        ts_groups[ts].add(sha)

    conflict_groups = {ts: shas for ts, shas in ts_groups.items() if len(shas) >= 2}
    print(f"[resolve-conflicts] grupos com timestamp identico: {len(conflict_groups)}", flush=True)

    fields = ["timestamp", "sha256", "path", "width", "height", "pixels", "size_bytes", "kept"]
    total_moved = total_groups = 0

    with open(args.report, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()

        for ts, shas in conflict_groups.items():
            candidates = []
            for sha in shas:
                path = resolve(sha)
                if not path:
                    continue
                try:
                    with Image.open(path) as im:
                        w, h = im.size
                except (UnidentifiedImageError, OSError):
                    continue
                candidates.append((sha, path, w, h, w * h, os.path.getsize(path)))

            if len(candidates) < 2:
                continue
            candidates.sort(key=lambda c: (c[4], c[5]), reverse=True)
            total_groups += 1

            for i, (sha, path, w, h, pixels, size) in enumerate(candidates):
                kept = i == 0
                if not kept:
                    rel = os.path.relpath(path, args.root)
                    new_path = unique_dest_path(os.path.join(args.review_dir, rel), sha[:8])
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    os.rename(path, new_path)
                    total_moved += 1
                    path = new_path
                writer.writerow({"timestamp": ts, "sha256": sha, "path": path, "width": w,
                                  "height": h, "pixels": pixels, "size_bytes": size, "kept": kept})

    print(f"[resolve-conflicts] grupos processados: {total_groups} | movidos: {total_moved}")


if __name__ == "__main__":
    main()
