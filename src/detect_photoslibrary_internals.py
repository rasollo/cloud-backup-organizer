#!/usr/bin/env python3
"""
Detecta arquivos que sao artefatos INTERNOS de uma biblioteca do Photos.app
(macOS) - acontece quando alguem sincronizou/fez backup da pasta inteira
`*.photoslibrary` pro Drive/iCloud/OneDrive em vez de usar "Exportar" -
o backup entao contem a estrutura interna do pacote
(`resources/media/master/...`), com nomes que o Photos.app usa pra si
mesmo, nao pra voce:

- `jpegvideocomplement_XXXX.mov` - o video de uma Live Photo
- `fullsizeoutput_XXXX.jpeg` - a foto renderizada em tamanho real

Dentro da biblioteca, o Photos.app sabe qual video pertence a qual foto
atraves do BANCO DE DADOS interno dele (SQLite), nao pelo nome do
arquivo - os nomes sao gerados independentemente um do outro. Sem esse
banco (que normalmente nao faz parte de um backup avulso), NAO da pra
casar os dois com confianca so pelo nome.

Esse script tenta mesmo assim, usando o timestamp EXIF exato (mesma pasta
original + mesmo segundo) como sinal - documentado aqui pra transparencia,
mas na pratica costuma dar 0 pares confirmados (o timestamp do video
raramente bate com o da foto nessa estrutura). Move os videos pra uma
pasta separada de qualquer forma, casados ou nao; as fotos (fullsizeoutput)
ficam onde estao - sao conteudo legitimo, so o video que fica sem
identidade confirmada.
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import time

INTERNAL_VIDEO_PREFIXES = ("jpegvideocomplement",)
INTERNAL_PHOTO_PREFIXES = ("fullsizeoutput",)

DATE_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")
EXIF_TAGS = ["DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate"]


def best_ts_epoch(entry):
    for tag in EXIF_TAGS:
        if tag in entry:
            m = DATE_RE.match(entry[tag])
            if m:
                y, mo, d, h, mi, s = (int(x) for x in m.groups())
                try:
                    return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
                except ValueError:
                    return None
    return None


def read_exif_timestamps(paths):
    if not paths:
        return {}
    listfile = "/tmp/_photoslibrary_paths.txt"
    with open(listfile, "w", encoding="utf-8") as f:
        f.write("\n".join(paths))
    result = subprocess.run(
        ["exiftool", "-j", "-q", *[f"-{t}" for t in EXIF_TAGS], "-api", "QuickTimeUTC", "-@", listfile],
        capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    return {entry["SourceFile"]: best_ts_epoch(entry) for entry in data}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--organized", required=True)
    ap.add_argument("--review-dir", required=True, help="pasta de destino pros videos sem par confirmado")
    ap.add_argument("--report", required=True)
    ap.add_argument("--pairing-tolerance-secs", type=int, default=2,
                     help="tolerancia de diferenca de horario pra considerar par confirmado (default: 2s)")
    args = ap.parse_args()

    with open(args.manifest, newline="", encoding="utf-8") as f:
        manifest_rows = list(csv.DictReader(f))
    with open(args.organized, newline="", encoding="utf-8") as f:
        organized_by_sha = {r["sha256"]: r for r in csv.DictReader(f)}

    def current_path(sha256):
        org = organized_by_sha.get(sha256)
        if not org or not os.path.exists(org["new_path"]):
            return None
        return org["new_path"]

    videos = [r for r in manifest_rows if os.path.basename(r["entry_name"]).lower().startswith(INTERNAL_VIDEO_PREFIXES)]
    photos = [r for r in manifest_rows if os.path.basename(r["entry_name"]).lower().startswith(INTERNAL_PHOTO_PREFIXES)]
    print(f"[photoslibrary] arquivos de video interno encontrados: {len(videos)}", flush=True)
    print(f"[photoslibrary] arquivos de foto interna encontrados (fullsizeoutput, NAO mexidos): {len(photos)}", flush=True)

    if not videos:
        print("[photoslibrary] nada a fazer.")
        return

    video_entries = [(r, current_path(r["sha256"])) for r in videos]
    video_entries = [(r, p) for r, p in video_entries if p]
    photo_entries = [(r, current_path(r["sha256"])) for r in photos]
    photo_entries = [(r, p) for r, p in photo_entries if p]

    all_paths = [p for _, p in video_entries] + [p for _, p in photo_entries]
    print(f"[photoslibrary] lendo EXIF de {len(all_paths)} arquivos...", flush=True)
    ts_by_path = read_exif_timestamps(all_paths)

    photo_by_folder = {}
    for r, p in photo_entries:
        ts = ts_by_path.get(p)
        if ts is not None:
            photo_by_folder.setdefault(os.path.dirname(r["entry_name"]), []).append((p, ts))

    fields = ["video_path_original", "video_path_novo", "timestamp", "status", "matched_photo"]
    matched = unmatched = moved = 0

    os.makedirs(args.review_dir, exist_ok=True)
    with open(args.report, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()

        for r, path in video_entries:
            ts = ts_by_path.get(path)
            folder = os.path.dirname(r["entry_name"])
            match = None
            if ts is not None:
                for photo_path, photo_ts in photo_by_folder.get(folder, []):
                    if abs(photo_ts - ts) <= args.pairing_tolerance_secs:
                        match = photo_path
                        break
            status = "par_confirmado" if match else "sem_par"
            if match:
                matched += 1
            else:
                unmatched += 1

            rel = os.path.basename(path)
            dest_path = os.path.join(args.review_dir, rel)
            i = 2
            while os.path.exists(dest_path):
                base, ext = os.path.splitext(rel)
                dest_path = os.path.join(args.review_dir, f"{base}__{i}{ext}")
                i += 1
            shutil.move(path, dest_path)
            moved += 1

            writer.writerow({"video_path_original": path, "video_path_novo": dest_path,
                              "timestamp": ts or "", "status": status, "matched_photo": match or ""})

    print(f"[photoslibrary] movidos: {moved} | par confirmado: {matched} | sem par: {unmatched}")
    print(f"[photoslibrary] relatorio: {args.report}")


if __name__ == "__main__":
    main()
