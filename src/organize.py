#!/usr/bin/env python3
"""
Fase 3 - Organizar por data.

Move (rename local, nao recopia) cada arquivo do manifesto de extract.py
para DEST/<ano>/<mes>/, escolhendo a data na seguinte ordem de
preferencia:

1. EXIF do proprio arquivo (DateTimeOriginal > CreateDate >
   MediaCreateDate > TrackCreateDate > GPSDateTime), lido via exiftool -
   cobre fotos (inclusive HEIC) e a maioria dos videos.
2. Data extraida do NOME do arquivo (screenshots, WhatsApp, camera) -
   ver common.py pra a lista de formatos reconhecidos.
3. Sem data: vai para DEST/sem-data/<sessao>/<zip>/ pra revisao manual.

NAO usa a data interna do arquivo dentro do zip (ZipInfo.date_time) como
fallback - na pratica ela quase sempre reflete a data de EXPORTACAO do
backup (mesma data pra todo mundo dentro do mesmo zip), nao a data
original do arquivo. Usar isso da datas erradas em massa - ja caímos
nessa (ver README, secao "Licoes aprendidas").

Resumivel via reports/organized.csv.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from common import extract_datetime_from_filename, sanitize_component, unique_dest_path  # noqa: E402

EXIF_TAGS = ["DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate", "GPSDateTime"]
DATE_RE_FULL = __import__("re").compile(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}")


def parse_exif_date(value, max_year):
    m = DATE_RE_FULL.match(value)
    if not m:
        return None
    y, mo, d = int(value[0:4]), int(value[5:7]), int(value[8:10])
    if not (1990 <= y <= max_year and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return (y, mo)


def run_exiftool(paths, cache_path, max_year):
    if os.path.exists(cache_path):
        print(f"[organize] reaproveitando cache de EXIF: {cache_path}", flush=True)
        with open(cache_path, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        print(f"[organize] rodando exiftool em {len(paths)} arquivos...", flush=True)
        listfile = cache_path + ".paths.txt"
        with open(listfile, "w", encoding="utf-8") as f:
            f.write("\n".join(paths))
        t0 = time.time()
        result = subprocess.run(
            ["exiftool", "-j", "-q", *[f"-{t}" for t in EXIF_TAGS],
             "-api", "QuickTimeUTC", "-@", listfile],
            capture_output=True, text=True,
        )
        print(f"[organize] exiftool terminou em {time.time()-t0:.1f}s", flush=True)
        if result.returncode not in (0, 1):
            print(f"[organize] AVISO exiftool stderr: {result.stderr[-2000:]}", file=sys.stderr, flush=True)
        raw = json.loads(result.stdout)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(raw, f)

    dates = {}
    for entry in raw:
        path = entry.get("SourceFile")
        best = None
        for tag in EXIF_TAGS:
            if tag in entry:
                best = parse_exif_date(entry[tag], max_year)
                if best:
                    break
        dates[path] = best
    return dates


def load_done(organized_csv):
    done = set()
    if not os.path.exists(organized_csv):
        return done
    with open(organized_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add(row["sha256"])
    return done


def cleanup_empty_dirs(dest_root, by_date_root):
    removed = 0
    for entry in os.listdir(dest_root):
        full = os.path.join(dest_root, entry)
        if full == by_date_root or not os.path.isdir(full):
            continue
        for dirpath, dirnames, filenames in os.walk(full, topdown=False):
            if not dirnames and not filenames:
                try:
                    os.rmdir(dirpath)
                    removed += 1
                except OSError:
                    pass
    return removed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="manifest.csv produzido por extract.py")
    ap.add_argument("--dest", required=True, help="pasta onde os arquivos do manifesto estao (mesma de extract.py --dest)")
    ap.add_argument("--by-date-dir", default="by-date", help="nome da subpasta organizada por data dentro de --dest (default: by-date)")
    ap.add_argument("--reports-dir", required=True)
    ap.add_argument("--max-year", type=int, default=None, help="ano maximo plausivel (default: ano atual + 1)")
    args = ap.parse_args()

    max_year = args.max_year or (time.localtime().tm_year + 1)
    by_date_root = os.path.join(args.dest, args.by_date_dir)
    organized_csv = os.path.join(args.reports_dir, "organized.csv")
    exif_cache = os.path.join(args.reports_dir, "exif-cache.json")

    with open(args.manifest, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    done = load_done(organized_csv)
    pending = [r for r in rows if r["sha256"] not in done]
    print(f"[organize] total: {len(rows)} | ja organizados: {len(done)} | pendentes: {len(pending)}", flush=True)
    if not pending:
        print("[organize] nada pendente.", flush=True)
        return

    exif_dates = run_exiftool([r["output_path"] for r in pending], exif_cache, max_year)

    os.makedirs(args.reports_dir, exist_ok=True)
    mode = "a" if os.path.exists(organized_csv) else "w"
    with open(organized_csv, mode, newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=["sha256", "old_path", "new_path", "date_used", "date_source"])
        if mode == "w":
            writer.writeheader()

        t_start = time.time()
        moved = no_date = errors = 0
        for i, row in enumerate(pending, 1):
            old_path = row["output_path"]
            if not os.path.exists(old_path):
                errors += 1
                continue

            ymd = exif_dates.get(old_path)
            source = "exif"
            if not ymd:
                basename = os.path.basename(old_path)
                dt = extract_datetime_from_filename(basename, max_year)
                if dt:
                    ymd = (dt[0], dt[1])
                    source = "filename"

            basename = sanitize_component(os.path.basename(old_path))
            if ymd:
                y, mo = ymd
                dest_dir = os.path.join(by_date_root, f"{y:04d}", f"{mo:02d}")
            else:
                source = "sem-data"
                no_date += 1
                session = row["source_zip_relpath"].split("/")[0]
                zip_stem = sanitize_component(os.path.splitext(os.path.basename(row["source_zip_relpath"]))[0])
                dest_dir = os.path.join(by_date_root, "sem-data", session, zip_stem)

            os.makedirs(dest_dir, exist_ok=True)
            dest_path = unique_dest_path(os.path.join(dest_dir, basename), row["sha256"][:8])
            try:
                os.rename(old_path, dest_path)
            except OSError as e:
                print(f"[organize] ERRO movendo {old_path}: {e}", file=sys.stderr, flush=True)
                errors += 1
                continue

            writer.writerow({
                "sha256": row["sha256"], "old_path": old_path, "new_path": dest_path,
                "date_used": f"{ymd[0]:04d}-{ymd[1]:02d}" if ymd else "", "date_source": source,
            })
            out.flush()
            moved += 1

            if i % 2000 == 0 or i == len(pending):
                elapsed = time.time() - t_start
                print(f"[organize] ({i}/{len(pending)}) movidos={moved} sem_data={no_date} "
                      f"erros={errors} | {elapsed:.0f}s decorridos", flush=True)

    print(f"[organize] concluido. movidos={moved} sem_data={no_date} erros={errors}", flush=True)
    removed = cleanup_empty_dirs(args.dest, by_date_root)
    print(f"[organize] limpeza: {removed} diretorios vazios removidos.", flush=True)


if __name__ == "__main__":
    main()
