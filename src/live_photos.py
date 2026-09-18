#!/usr/bin/env python3
"""
Agrupa Live Photos (foto HEIC/JPG + video MOV com o mesmo nome-base que
estavam na MESMA PASTA no backup original - o sinal mais confiavel de que
sao a foto e o video companheiro de uma Live Photo da Apple).

Para cada par confirmado: se as duas pontas ja estao na mesma pasta
final, so registra o par. Se estao em pastas diferentes (normalmente
porque tiveram fontes de data diferentes), move a ponta de menor
confianca de data para a pasta da ponta mais confiavel, pra ficarem
juntas.

So renomeia arquivos dentro do proprio destino (mesma particao).
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import unique_dest_path  # noqa: E402

IMG_EXTS = {"heic", "jpg", "jpeg"}
VID_EXTS = {"mov"}
DATE_SOURCE_PRIORITY = {"exif": 0, "filename": 1, "sem-data": 2, "": 2}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--organized", required=True)
    ap.add_argument("--reports-dir", required=True)
    args = ap.parse_args()

    out_csv = os.path.join(args.reports_dir, "live-photos.csv")

    with open(args.manifest, newline="", encoding="utf-8") as f:
        manifest_rows = list(csv.DictReader(f))
    with open(args.organized, newline="", encoding="utf-8") as f:
        organized_rows = list(csv.DictReader(f))
    organized_by_sha = {r["sha256"]: r for r in organized_rows}

    def resolve(sha256):
        org = organized_by_sha.get(sha256)
        if not org:
            return None, None
        return (org["new_path"] if os.path.exists(org["new_path"]) else None), org

    groups = {}
    for r in manifest_rows:
        entry = r["entry_name"]
        dirname = os.path.dirname(entry)
        stem, ext = os.path.splitext(os.path.basename(entry))
        ext = ext.lstrip(".").lower()
        key = (r["source_root"], r["source_zip_relpath"], dirname, stem.lower())
        groups.setdefault(key, []).append((ext, r["sha256"]))

    pairs = []
    for key, items in groups.items():
        imgs = [sha for ext, sha in items if ext in IMG_EXTS]
        vids = [sha for ext, sha in items if ext in VID_EXTS]
        for img_sha in imgs:
            for vid_sha in vids:
                pairs.append((img_sha, vid_sha))

    print(f"[live-photos] pares candidatos: {len(pairs)}", flush=True)

    fields = ["photo_sha256", "video_sha256", "photo_path", "video_path", "action"]
    already_ok = moved = errors = 0

    with open(out_csv, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()

        for img_sha, vid_sha in pairs:
            img_path, img_org = resolve(img_sha)
            vid_path, vid_org = resolve(vid_sha)
            if not img_path or not vid_path:
                errors += 1
                writer.writerow({"photo_sha256": img_sha, "video_sha256": vid_sha,
                                  "photo_path": img_path or "", "video_path": vid_path or "",
                                  "action": "erro_arquivo_nao_encontrado"})
                continue

            img_dir, vid_dir = os.path.dirname(img_path), os.path.dirname(vid_path)
            if img_dir == vid_dir:
                already_ok += 1
                writer.writerow({"photo_sha256": img_sha, "video_sha256": vid_sha,
                                  "photo_path": img_path, "video_path": vid_path, "action": "ja_juntos"})
                continue

            img_prio = DATE_SOURCE_PRIORITY.get(img_org["date_source"], 2)
            vid_prio = DATE_SOURCE_PRIORITY.get(vid_org["date_source"], 2)
            if img_prio <= vid_prio:
                target_dir, move_path, move_sha, action = img_dir, vid_path, vid_sha, "movido_video"
            else:
                target_dir, move_path, move_sha, action = vid_dir, img_path, img_sha, "movido_foto"

            new_path = unique_dest_path(os.path.join(target_dir, os.path.basename(move_path)), move_sha[:8])
            try:
                os.rename(move_path, new_path)
            except OSError:
                errors += 1
                writer.writerow({"photo_sha256": img_sha, "video_sha256": vid_sha,
                                  "photo_path": img_path, "video_path": vid_path, "action": "erro_ao_mover"})
                continue

            moved += 1
            final_img = new_path if action == "movido_foto" else img_path
            final_vid = new_path if action == "movido_video" else vid_path
            writer.writerow({"photo_sha256": img_sha, "video_sha256": vid_sha,
                              "photo_path": final_img, "video_path": final_vid, "action": action})

    print(f"[live-photos] ja juntos: {already_ok} | movidos: {moved} | erros: {errors}")


if __name__ == "__main__":
    main()
