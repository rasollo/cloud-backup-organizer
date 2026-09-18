#!/usr/bin/env python3
"""
Fase 2 - Extracao seletiva com deduplicacao por conteudo.

Le o inventario (produzido por inventory.py), abre cada arquivo (zip) e
extrai somente as entradas de midia (foto/video), deduplicando pelo
SHA256 do CONTEUDO de cada arquivo extraido - nao do zip inteiro. Isso
evita duplicar a mesma foto quando ela existe em varios exports (ex:
iCloud E Google Takeout tem a mesma foto).

Se dois arquivos de origem tem o MESMO caminho relativo mas hash
diferente (indicando corrupcao ou uma copia mais completa), da
preferencia a ordem definida em --prefer.

Nao apaga, move ou modifica nenhum arquivo original.
"""
import argparse
import csv
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from common import sanitize_component, sanitize_relpath, sha256_of, unique_dest_path  # noqa: E402

PHOTO_EXTS = {
    "jpg", "jpeg", "png", "heic", "heif", "gif", "bmp", "tiff", "tif",
    "webp", "dng", "cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "psd",
}
VIDEO_EXTS = {
    "mp4", "mov", "avi", "m4v", "3gp", "3g2", "mkv", "wmv", "mts", "m2ts",
    "mpg", "mpeg",
}
MEDIA_EXTS = PHOTO_EXTS | VIDEO_EXTS

EXFAT_MAX_FILE_SIZE = 2**32 - 1  # 4GB - 1: limite de arquivo unico em exFAT
CHUNK = 4 * 1024 * 1024

MANIFEST_FIELDS = ["sha256", "output_path", "source_root", "source_zip_relpath",
                    "entry_name", "size_bytes"]
DONE_ZIPS_FIELDS = ["root_label", "relative_path", "entries_seen", "media_extracted",
                     "media_duplicate", "oversized_skipped"]
DUPLICATES_FIELDS = ["sha256", "source_root", "source_zip_relpath", "entry_name",
                      "canonical_output_path"]
OVERSIZED_FIELDS = ["source_root", "source_zip_relpath", "entry_name", "size_bytes"]


def load_inventory(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_issues(path):
    issues = {}
    if not path or not os.path.exists(path):
        return issues
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            issues[row["relative_path"]] = row
    return issues


def pick_zips_to_process(rows, issues, preferred_order):
    order = {r: i for i, r in enumerate(preferred_order)}
    by_path = {}
    for row in rows:
        by_path.setdefault(row["relative_path"], []).append(row)

    chosen = []
    for rel, group in by_path.items():
        if len(group) == 1:
            chosen.append(group[0])
            continue
        shas = {r["sha256"] for r in group}
        if len(shas) == 1:
            group.sort(key=lambda r: order.get(r["root_label"], 99))
            chosen.append(group[0])
            continue
        issue = issues.get(rel)
        good_row = None
        if issue:
            good_root = issue["good_root"]
            good_row = next((r for r in group if r["root_label"] == good_root), None)
        if good_row:
            chosen.append(good_row)
            continue
        group.sort(key=lambda r: order.get(r["root_label"], 99))
        chosen.append(group[0])
        print(f"[extract] AVISO: {rel} tem hashes diferentes entre "
              f"{[r['root_label'] for r in group]} sem registro de qual e a boa "
              f"- usando {chosen[-1]['root_label']} por ordem de preferencia.", flush=True)
    return chosen


def load_manifest(path):
    seen = {}
    if not os.path.exists(path):
        return seen
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            seen[row["sha256"]] = row["output_path"]
    return seen


def load_done_zips(path):
    done = set()
    if not os.path.exists(path):
        return done
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add((row["root_label"], row["relative_path"]))
    return done


def append_row(path, fields, row):
    mode = "a" if os.path.exists(path) else "w"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if mode == "w":
            writer.writeheader()
        writer.writerow(row)


def process_zip(row, dest_root, seen_hashes, manifest_fh, manifest_writer, oversized_csv, duplicates_csv):
    root_label = row["root_label"]
    rel = row["relative_path"]
    zip_path = os.path.join(row["root_path"], rel)
    session = rel.split("/")[0]
    zip_stem = sanitize_component(os.path.splitext(os.path.basename(rel))[0])

    entries_seen = media_extracted = media_duplicate = oversized_skipped = 0

    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        print(f"[extract] ERRO abrindo {root_label}/{rel}: {e}", file=sys.stderr, flush=True)
        return None

    with zf:
        for info in zf.infolist():
            entries_seen += 1
            name = info.filename
            base = os.path.basename(name.rstrip("/"))
            if not base or info.is_dir():
                continue
            if base.startswith("._") or base in (".DS_Store", "Thumbs.db"):
                continue
            if "__MACOSX/" in name:
                continue
            ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
            if ext not in MEDIA_EXTS:
                continue

            if info.file_size > EXFAT_MAX_FILE_SIZE:
                oversized_skipped += 1
                append_row(oversized_csv, OVERSIZED_FIELDS, {
                    "source_root": root_label, "source_zip_relpath": rel,
                    "entry_name": name, "size_bytes": info.file_size,
                })
                continue

            out_rel = sanitize_relpath(name)
            out_path = os.path.join(dest_root, session, zip_stem, out_rel)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            tmp_path = out_path + f".part-{os.getpid()}"

            import hashlib
            h = hashlib.sha256()
            try:
                with zf.open(info) as src, open(tmp_path, "wb") as dst:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        h.update(chunk)
                        dst.write(chunk)
            except (zipfile.BadZipFile, OSError, RuntimeError) as e:
                print(f"[extract] ERRO lendo entrada {root_label}/{rel}!{name}: {e}", file=sys.stderr, flush=True)
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                continue
            digest = h.hexdigest()

            if digest in seen_hashes:
                media_duplicate += 1
                os.remove(tmp_path)
                append_row(duplicates_csv, DUPLICATES_FIELDS, {
                    "sha256": digest, "source_root": root_label,
                    "source_zip_relpath": rel, "entry_name": name,
                    "canonical_output_path": seen_hashes[digest],
                })
                continue

            out_path = unique_dest_path(out_path, digest[:8])
            os.rename(tmp_path, out_path)
            seen_hashes[digest] = out_path
            media_extracted += 1
            manifest_writer.writerow({
                "sha256": digest, "output_path": out_path,
                "source_root": root_label, "source_zip_relpath": rel,
                "entry_name": name, "size_bytes": info.file_size,
            })
            manifest_fh.flush()

    return {"root_label": root_label, "relative_path": rel, "entries_seen": entries_seen,
            "media_extracted": media_extracted, "media_duplicate": media_duplicate,
            "oversized_skipped": oversized_skipped}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory", required=True, help="CSV produzido por inventory.py")
    ap.add_argument("--issues", help="CSV de problemas de integridade (relative_path,corrupted_root,good_root,...) - opcional")
    ap.add_argument("--dest", required=True, help="pasta de destino da extracao")
    ap.add_argument("--reports-dir", required=True, help="pasta onde gravar manifest/duplicates/oversized/done-zips CSVs")
    ap.add_argument("--prefer", action="append", default=[],
                     help="ordem de preferencia de root_label quando ha conflito sem --issues (repita, ex: --prefer sdc1 --prefer backup)")
    args = ap.parse_args()

    manifest_csv = os.path.join(args.reports_dir, "manifest.csv")
    done_zips_csv = os.path.join(args.reports_dir, "done-zips.csv")
    duplicates_csv = os.path.join(args.reports_dir, "duplicates.csv")
    oversized_csv = os.path.join(args.reports_dir, "oversized.csv")

    if not os.path.isdir(args.dest):
        print(f"[extract] ERRO: destino nao encontrado: {args.dest}", file=sys.stderr)
        sys.exit(1)

    rows = load_inventory(args.inventory)
    issues = load_issues(args.issues)
    to_process = pick_zips_to_process(rows, issues, args.prefer)
    done_zips = load_done_zips(done_zips_csv)
    seen_hashes = load_manifest(manifest_csv)

    pending = [r for r in to_process if (r["root_label"], r["relative_path"]) not in done_zips]
    print(f"[extract] zips unicos: {len(to_process)} | ja processados: {len(done_zips)} | pendentes: {len(pending)}", flush=True)
    print(f"[extract] arquivos ja no manifesto: {len(seen_hashes)}", flush=True)

    os.makedirs(args.reports_dir, exist_ok=True)
    manifest_mode = "a" if os.path.exists(manifest_csv) else "w"
    with open(manifest_csv, manifest_mode, newline="", encoding="utf-8") as manifest_fh:
        manifest_writer = csv.DictWriter(manifest_fh, fieldnames=MANIFEST_FIELDS)
        if manifest_mode == "w":
            manifest_writer.writeheader()

        for i, row in enumerate(pending, 1):
            t0 = time.time()
            print(f"[extract] ({i}/{len(pending)}) processando {row['root_label']}/{row['relative_path']} ...", flush=True)
            result = process_zip(row, args.dest, seen_hashes, manifest_fh, manifest_writer, oversized_csv, duplicates_csv)
            dt = time.time() - t0
            if result is None:
                continue
            append_row(done_zips_csv, DONE_ZIPS_FIELDS, result)
            print(f"[extract] ({i}/{len(pending)}) OK em {dt:.1f}s | entradas={result['entries_seen']} "
                  f"extraidas={result['media_extracted']} duplicadas={result['media_duplicate']} "
                  f"grandes_demais={result['oversized_skipped']}", flush=True)

    print("[extract] concluido.", flush=True)


if __name__ == "__main__":
    main()
