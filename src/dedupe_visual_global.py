#!/usr/bin/env python3
"""
Duplicatas visuais SEM depender de nome de arquivo em comum - diferente
de dedupe_visual.py (que so compara arquivos que compartilham um
nome-base, ex: "IMG_5353.jpg" e "IMG_5353(1).jpg").

Isso importa pra fotos vindas de mensageria (WhatsApp/Telegram): a mesma
imagem reencaminhada varias vezes ganha um nome novo (UUID) a cada vez,
entao nao ha nome em comum pra agrupar - mas a imagem e visualmente
identica ou quase identica. Testado numa biblioteca real: achou ~1.700
duplicatas (quase 1 em cada 3 arquivos) num lote de ~5.600 fotos de
mensageria que dedupe_visual.py nao teria pego de jeito nenhum.

Compara TODAS as imagens da pasta entre si (dHash + Hamming distance) e
agrupa clusters inteiros (nao so pares) via Union-Find - importante
porque a mesma foto pode ter sido reencaminhada 3-4 vezes, nao so 2.
Mantem a maior de cada cluster, move o resto pra --review-dir.

Custo: O(n^2) comparacoes. Na pratica, rapido mesmo em Python puro (~4s
pra 5.600 arquivos = ~15.7 milhoes de comparacoes) porque cada comparacao
e so um XOR + contagem de bits. Pra bibliotecas muito maiores (dezenas de
milhares+), considere rodar por sub-pastas separadas em vez da pasta
inteira de uma vez.
"""
import argparse
import os
import shutil

from PIL import Image, UnidentifiedImageError

IMAGE_EXTS = {"jpg", "jpeg", "png", "heic", "heif", "gif", "bmp", "tiff", "tif", "webp"}


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


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="pasta a analisar (recursivo)")
    ap.add_argument("--review-dir", required=True, help="pasta de destino pras copias menores de cada grupo")
    ap.add_argument("--threshold", type=int, default=6, help="distancia de Hamming maxima pra considerar duplicata (default: 6, mais estrito que dedupe_visual.py porque nao ha contexto de nome em comum)")
    ap.add_argument("--exclude-dir", action="append", default=[], help="nome de subpasta a ignorar (repita pra varias, ex: --exclude-dir _duplicatas-internas)")
    args = ap.parse_args()

    files = []
    for dirpath, dirnames, filenames in os.walk(args.root):
        dirnames[:] = [d for d in dirnames if d not in args.exclude_dir]
        for name in filenames:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in IMAGE_EXTS:
                files.append(os.path.join(dirpath, name))

    print(f"[dedupe-global] total de arquivos: {len(files)}", flush=True)

    hashes = []
    valid_files = []
    for path in files:
        h = dhash(path)
        if h is not None:
            hashes.append(h)
            valid_files.append(path)

    n = len(hashes)
    print(f"[dedupe-global] arquivos com hash valido: {n}", flush=True)

    uf = UnionFind(n)
    for i in range(n):
        hi = hashes[i]
        for j in range(i + 1, n):
            if bin(hi ^ hashes[j]).count("1") <= args.threshold:
                uf.union(i, j)

    clusters = {}
    for i in range(n):
        root = uf.find(i)
        clusters.setdefault(root, []).append(i)

    dup_clusters = {k: v for k, v in clusters.items() if len(v) >= 2}
    print(f"[dedupe-global] grupos com duplicata: {len(dup_clusters)}", flush=True)

    moved = 0
    for indices in dup_clusters.values():
        sized = sorted(((valid_files[i], os.path.getsize(valid_files[i])) for i in indices),
                        key=lambda x: x[1], reverse=True)
        for path, _ in sized[1:]:
            rel = os.path.relpath(path, args.root)
            dest_path = os.path.join(args.review_dir, rel)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.move(path, dest_path)
            moved += 1

    print(f"[dedupe-global] movidos para revisao: {moved}")


if __name__ == "__main__":
    main()
