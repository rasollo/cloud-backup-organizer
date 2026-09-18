#!/usr/bin/env python3
"""
Valida uma amostra aleatoria dos arquivos organizados:
- confere se o arquivo existe e se o tamanho bate com o manifesto
- recalcula o SHA256 e compara com o registrado
- valida a estrutura do arquivo de acordo com o tipo (Pillow pra
  JPEG/PNG/GIF/BMP/TIFF/WEBP; assinatura ftyp pra HEIC/HEIF/MP4/MOV;
  RIFF/AVI; EBML/MKV)

Nao apaga nem modifica nada, so leitura.
"""
import argparse
import csv
import hashlib
import os
import random

from PIL import Image, UnidentifiedImageError

CHUNK = 4 * 1024 * 1024
PIL_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp"}
FTYP_EXTS = {"heic", "heif", "mp4", "mov", "m4v", "3gp", "3g2"}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def validate_structure(path, ext):
    ext = ext.lower()
    try:
        if ext in PIL_EXTS:
            with Image.open(path) as im:
                im.verify()
            return True, "PIL verify OK"
        if ext in FTYP_EXTS:
            with open(path, "rb") as f:
                head = f.read(64)
            return (b"ftyp" in head[:32]), "ftyp signature"
        if ext == "avi":
            with open(path, "rb") as f:
                head = f.read(16)
            return (head[:4] == b"RIFF" and head[8:12] == b"AVI "), "RIFF/AVI signature"
        if ext == "mkv":
            with open(path, "rb") as f:
                head = f.read(4)
            return (head == b"\x1a\x45\xdf\xa3"), "EBML signature"
        return None, "no structural validator for this extension"
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        return False, f"error validating: {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--sample-size", type=int, default=60)
    args = ap.parse_args()

    with open(args.manifest, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    sample = random.sample(rows, min(args.sample_size, len(rows)))
    ok = fail = 0
    failures = []

    for row in sample:
        path = row["output_path"] if "output_path" in row else row["new_path"]
        expected_size = int(row["size_bytes"]) if "size_bytes" in row else None
        expected_sha = row["sha256"]
        ext = path.rsplit(".", 1)[-1] if "." in path else ""

        if not os.path.exists(path):
            fail += 1
            failures.append((path, "file does not exist"))
            continue
        if expected_size is not None and os.path.getsize(path) != expected_size:
            fail += 1
            failures.append((path, "size mismatch"))
            continue
        if sha256_of(path) != expected_sha:
            fail += 1
            failures.append((path, "sha256 mismatch"))
            continue
        struct_ok, detail = validate_structure(path, ext)
        if struct_ok is False:
            fail += 1
            failures.append((path, f"invalid structure: {detail}"))
        else:
            ok += 1

    print(f"[validate] sample: {len(sample)} | ok: {ok} | failures: {fail}")
    for path, reason in failures:
        print(f"  - {path}\n    {reason}")


if __name__ == "__main__":
    main()
