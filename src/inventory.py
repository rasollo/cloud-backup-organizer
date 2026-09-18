#!/usr/bin/env python3
"""
Fase 1 - Inventario (somente leitura).

Percorre uma ou mais raizes de disco, lista arquivos que casam com um
padrao (por padrao, *.zip - os exports do iCloud/Google Takeout/OneDrive)
e calcula o SHA256 de cada um, gravando em um CSV de forma incremental e
resumivel. Isso permite depois identificar backups duplicados entre
discos (mesmo hash, caminhos diferentes) antes de decidir o que extrair.

Nao apaga, move ou modifica nenhum arquivo original.

Uso:
    python3 inventory.py --root backup=/data/backup --root nas=/mnt/nas \\
        --output reports/inventory.csv

    # pra hashear qualquer arquivo, nao so zip:
    python3 inventory.py --root backup=/data/backup --pattern "*" \\
        --output reports/inventory-full.csv
"""
import argparse
import csv
import fnmatch
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from common import parse_roots, sha256_of  # noqa: E402

FIELDS = ["root_label", "root_path", "relative_path", "size_bytes", "mtime", "sha256", "hash_seconds"]
SKIP_DIR_NAMES = {"lost+found", "__pycache__", ".Trash-1000", ".Trashes",
                   ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100"}


def find_files(root_label, root_path, pattern):
    for dirpath, dirnames, filenames in os.walk(root_path, onerror=lambda e: print(f"WARN: {e}", file=sys.stderr)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for name in filenames:
            if not fnmatch.fnmatch(name.lower(), pattern.lower()):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root_path)
            yield root_label, root_path, full, rel


def load_done(csv_path):
    done = {}
    if not os.path.exists(csv_path):
        return done
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["root_label"], row["relative_path"], row["size_bytes"], row["mtime"])
            done[key] = row["sha256"]
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", required=True, metavar="LABEL=PATH",
                     help="raiz a inventariar, ex: backup=/data/backup (repita pra varias raizes)")
    ap.add_argument("--pattern", default="*.zip", help="padrao glob de nome de arquivo (default: *.zip)")
    ap.add_argument("--output", required=True, help="CSV de saida (resumivel)")
    ap.add_argument("--heartbeat-secs", type=int, default=30, help="segundos entre logs de progresso dentro de um arquivo grande")
    args = ap.parse_args()

    roots = parse_roots(args.root)
    done = load_done(args.output)
    print(f"[inventory] ja concluidos anteriormente: {len(done)} arquivos", flush=True)

    print("[inventory] listando arquivos em todas as raizes...", flush=True)
    all_files = []
    for label, path in roots.items():
        if not os.path.isdir(path):
            print(f"[inventory] AVISO: raiz nao encontrada, pulando: {path}", flush=True)
            continue
        before = len(all_files)
        all_files.extend(find_files(label, path, args.pattern))
        print(f"[inventory]   {label} ({path}): {len(all_files) - before} arquivos", flush=True)

    pending = []
    total_bytes = 0
    for label, root_path, full, rel in all_files:
        try:
            st = os.stat(full)
        except OSError as e:
            print(f"[inventory] ERRO stat {full}: {e}", file=sys.stderr, flush=True)
            continue
        size = st.st_size
        mtime = str(int(st.st_mtime))
        key = (label, rel, str(size), mtime)
        if key in done:
            continue
        pending.append((label, root_path, full, rel, size, mtime))
        total_bytes += size

    print(f"[inventory] total: {len(all_files)} | pendentes: {len(pending)} ({total_bytes / 1024**3:.1f} GB)", flush=True)

    mode = "a" if os.path.exists(args.output) else "w"
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, mode, newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        if mode == "w":
            writer.writeheader()

        done_bytes = 0
        t_start = time.time()
        for i, (label, root_path, full, rel, size, mtime) in enumerate(pending, 1):
            t0 = time.time()
            print(f"[inventory] ({i}/{len(pending)}) {label}/{rel} ({size/1024**2:.0f}MB)...", flush=True)
            try:
                digest = sha256_of(full)
            except OSError as e:
                print(f"[inventory] ERRO lendo {full}: {e}", file=sys.stderr, flush=True)
                continue
            dt = time.time() - t0
            writer.writerow({
                "root_label": label, "root_path": root_path, "relative_path": rel,
                "size_bytes": size, "mtime": mtime, "sha256": digest, "hash_seconds": f"{dt:.1f}",
            })
            out.flush()
            done_bytes += size
            elapsed = time.time() - t_start
            speed = (done_bytes / 1024**2) / elapsed if elapsed > 0 else 0
            pct = 100 * done_bytes / total_bytes if total_bytes else 100
            print(f"[inventory] ({i}/{len(pending)}) {pct:5.1f}% {label}/{rel} {size/1024**2:.0f}MB "
                  f"em {dt:.1f}s | media {speed:.1f}MB/s | sha256={digest[:12]}...", flush=True)

    print("[inventory] concluido.", flush=True)


if __name__ == "__main__":
    main()
