#!/usr/bin/env python3
"""
Duplicatas visuais (fotos com o mesmo nome-base mas conteudo diferente -
ja sobreviveram a dedup exata por SHA256 - que sao, na pratica, a MESMA
foto salva duas vezes em qualidade/edicao diferente).

Usa um hash perceptual simples (dHash, so com Pillow) pra comparar pares
dentro do mesmo grupo de nome-base+pasta. Pares com distancia de Hamming
<= --threshold e dimensao minima --min-dim sao consideradas a mesma foto;
move (nao apaga) a versao menor/mais comprimida para --review-dir,
mantendo a maior no lugar.

So GERA UM RELATORIO se --apply nao for passado.
"""
import argparse
import csv
import os
import re
import shutil
from collections import defaultdict

from PIL import Image, UnidentifiedImageError

SUFFIX_PATTERNS = [
    re.compile(r"__[0-9a-f]{8}(_\d+)?$"),
    re.compile(r"\s?\(\d+\)$"),
]
IMAGE_EXTS = {"jpg", "jpeg", "png", "heic", "heif", "gif", "bmp", "tiff", "tif", "webp"}


def normalize_base(stem):
    changed = True
    while changed:
        changed = False
        for pat in SUFFIX_PATTERNS:
            new_stem = pat.sub("", stem)
            if new_stem != stem:
                stem, changed = new_stem, True
    return stem


def dhash(path, hash_size=8):
    try:
        with Image.open(path) as im:
            im = im.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
            pixels = list(im.getdata())
    except (UnidentifiedImageError, OSError):
        return None
    bits = 0
    for row in range(hash_size):
        row_start = row * (hash_size + 1)
        for col in range(hash_size):
            bits = (bits << 1) | (1 if pixels[row_start + col] > pixels[row_start + col + 1] else 0)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def find_groups(root):
    groups = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            stem, ext = os.path.splitext(name)
            ext = ext.lstrip(".").lower()
            if ext not in IMAGE_EXTS:
                continue
            base = normalize_base(stem)
            groups[(dirpath, base.lower())].append(os.path.join(dirpath, name))
    return {k: v for k, v in groups.items() if len(v) >= 2}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="pasta organizada por data (by-date) a analisar")
    ap.add_argument("--report", required=True, help="CSV de saida")
    ap.add_argument("--threshold", type=int, default=10, help="distancia de Hamming maxima pra considerar 'mesma foto' (default: 10)")
    ap.add_argument("--min-dim", type=int, default=200, help="dimensao minima (px) pra nao pegar icones/thumbnails (default: 200)")
    ap.add_argument("--apply", action="store_true", help="move a versao menor de cada par pra --review-dir (sem isso, so gera relatorio)")
    ap.add_argument("--review-dir", help="pasta de destino pros arquivos movidos (obrigatorio com --apply)")
    args = ap.parse_args()

    groups = find_groups(args.root)
    print(f"[dedupe-visual] grupos com nome-base compartilhado: {len(groups)}", flush=True)

    fields = ["dir", "base_name", "file_a", "file_b", "hamming_distance",
              "size_a", "size_b", "dims_a", "dims_b", "provavel_mesma_foto"]
    rows_out = []

    for (dirpath, base), paths in groups.items():
        hashes, dims, sizes = {}, {}, {}
        for p in paths:
            h = dhash(p)
            if h is None:
                continue
            hashes[p] = h
            sizes[p] = os.path.getsize(p)
            try:
                with Image.open(p) as im:
                    dims[p] = im.size
            except (UnidentifiedImageError, OSError):
                dims[p] = None

        plist = list(hashes.keys())
        for i in range(len(plist)):
            for j in range(i + 1, len(plist)):
                a, b = plist[i], plist[j]
                dist = hamming(hashes[a], hashes[b])
                da, db = dims[a], dims[b]
                ok_dim = da and db and da[0] >= args.min_dim and da[1] >= args.min_dim and db[0] >= args.min_dim and db[1] >= args.min_dim
                likely = dist <= args.threshold and ok_dim
                rows_out.append({"dir": dirpath, "base_name": base, "file_a": a, "file_b": b,
                                  "hamming_distance": dist, "size_a": sizes[a], "size_b": sizes[b],
                                  "dims_a": da, "dims_b": db, "provavel_mesma_foto": likely})

    with open(args.report, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows_out)

    likely_rows = [r for r in rows_out if r["provavel_mesma_foto"]]
    print(f"[dedupe-visual] pares comparados: {len(rows_out)} | provaveis mesma foto: {len(likely_rows)}")

    if not args.apply:
        print(f"[dedupe-visual] relatorio: {args.report} (rode de novo com --apply --review-dir para mover)")
        return

    if not args.review_dir:
        raise SystemExit("--apply requer --review-dir")

    keep_set, move_candidates = set(), {}
    for r in likely_rows:
        a, b = r["file_a"], r["file_b"]
        smaller, larger = (a, b) if r["size_a"] <= r["size_b"] else (b, a)
        keep_set.add(larger)
        move_candidates[smaller] = larger

    to_move = {p: keep for p, keep in move_candidates.items() if p not in keep_set}
    moved = 0
    for old_path, kept in to_move.items():
        if not os.path.exists(old_path):
            continue
        rel = os.path.relpath(old_path, args.root)
        new_path = os.path.join(args.review_dir, rel)
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        shutil.move(old_path, new_path)
        moved += 1

    print(f"[dedupe-visual] movidos para revisao: {moved}")


if __name__ == "__main__":
    main()
