#!/usr/bin/env python3
"""
Gera um mini dataset sintetico pra testar o pipeline ponta a ponta:
- 2 "discos" de origem (source-a, source-b) com zips imitando exports do
  Google Takeout e do iCloud
- fotos com EXIF real (via exiftool), fotos so com data no nome (varios
  formatos: WhatsApp, catalao, timestamp colado), fotos sem nenhuma data
- uma foto duplicada por CONTEUDO entre as duas fontes (testa dedup)
- duas fotos com nomes de arquivo IGUAIS mas conteudo diferente (testa
  colisao/sufixo)
- duas fotos com o MESMO timestamp EXIF mas resolucao diferente (testa
  resolve_conflicts.py)
- um par HEIC+MOV com o mesmo nome-base na mesma pasta (testa
  live_photos.py - usa .jpg no lugar de .heic de verdade, e um MP4/MOV
  minimo sem duracao legivel, ja que nao ha ffmpeg disponivel aqui)
- uma pasta de album do Google Takeout (testa albums.py)

Roda com: python3 generate_dataset.py
"""
import os
import shutil
import subprocess
import zipfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_A = os.path.join(HERE, "source-a")
SOURCE_B = os.path.join(HERE, "source-b")


def make_image(path, color, size=(400, 300)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color=color).save(path, "JPEG", quality=85)


def set_exif_date(path, date_str):
    subprocess.run(
        ["exiftool", "-overwrite_original", "-q", f"-DateTimeOriginal={date_str}",
         f"-CreateDate={date_str}", path],
        check=True,
    )


def make_fake_video(path):
    """Um MP4 minimo (so o box ftyp) - passa em checagens de assinatura
    mas NAO tem duracao legivel (exiftool -Duration vai dar vazio). Serve
    pra testar o agrupamento de live_photos.py, nao dedupe_video.py."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ftyp = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 8
    with open(path, "wb") as f:
        f.write(ftyp)


def zip_dir(src_dir, zip_path):
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(src_dir):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, src_dir)
                zf.write(full, rel)


def main():
    for d in (SOURCE_A, SOURCE_B):
        if os.path.isdir(d):
            shutil.rmtree(d)

    staging_a = os.path.join(HERE, "_staging_a")
    staging_b = os.path.join(HERE, "_staging_b")
    for d in (staging_a, staging_b):
        if os.path.isdir(d):
            shutil.rmtree(d)

    # --- source A: Google Takeout style ---
    # foto com EXIF real
    p1 = os.path.join(staging_a, "Takeout/Google Fotos/Fotos de 2019/photo_exif.jpg")
    make_image(p1, "red")
    set_exif_date(p1, "2019:05:01 10:00:00")

    # foto num album de verdade (nao pasta de ano) - testa albums.py
    p2 = os.path.join(staging_a, "Takeout/Google Fotos/Viagem para Roma/roma1.jpg")
    make_image(p2, "blue")
    set_exif_date(p2, "2021:03:15 14:30:00")

    # foto so com data no nome (padrao camera), sem EXIF
    make_image(os.path.join(staging_a, "Takeout/Google Fotos/Fotos de 2017/2017-06-01_07-19-48.jpg"), "green")

    # foto WhatsApp com sequencial WA (nao e hora!), sem EXIF
    make_image(os.path.join(staging_a, "Takeout/Google Fotos/Fotos de 2016/IMG-20161127-WA0033.jpg"), "yellow")

    # essa vai ser DUPLICADA por conteudo com uma foto do source B
    p_dup_a = os.path.join(staging_a, "Takeout/Google Fotos/Fotos de 2020/shared_moment.jpg")
    make_image(p_dup_a, "purple")
    set_exif_date(p_dup_a, "2020:08:20 09:00:00")

    # conflito: mesmo timestamp EXIF de uma foto do source B, mas
    # resolucao MENOR (deve perder no resolve_conflicts.py)
    p_conflict_a = os.path.join(staging_a, "Takeout/Google Fotos/Fotos de 2022/conflict_smaller.jpg")
    make_image(p_conflict_a, "orange", size=(200, 150))
    set_exif_date(p_conflict_a, "2022:02:02 12:00:00")

    zip_dir(staging_a, os.path.join(SOURCE_A, "google-export/takeout-20260101-001.zip"))

    # --- source B: iCloud style ---
    p3 = os.path.join(staging_b, "Photos/IMG_1001.jpg")
    make_image(p3, "cyan")
    set_exif_date(p3, "2018:12:25 08:00:00")

    # Live Photo: mesmo nome-base, HEIC(jpg)+MOV na mesma pasta
    make_image(os.path.join(staging_b, "Photos/IMG_5432.jpg"), "magenta")
    make_fake_video(os.path.join(staging_b, "Photos/IMG_5432.mov"))

    # sem data nenhuma - vai pra sem-data/
    make_image(os.path.join(staging_b, "Photos/random_name_no_date.jpg"), "gray")

    # screenshot em catalao - testa parsing de horario com prefixo longo
    make_image(os.path.join(staging_b, "Photos/Captura de pantalla 2018-10-18 a les 9.45.19.jpg"), "brown")

    # timestamp colado sem separador (estilo Android)
    make_image(os.path.join(staging_b, "Photos/20140503_230717000_iOS.jpg"), "pink")

    # a duplicata por conteudo do source A (MESMO conteudo, nome diferente)
    p_dup_b = os.path.join(staging_b, "Photos/IMG_9999.jpg")
    shutil.copy(p_dup_a, p_dup_b)

    # conflito: mesmo timestamp EXIF do source A, resolucao MAIOR (deve
    # vencer no resolve_conflicts.py)
    p_conflict_b = os.path.join(staging_b, "Photos/conflict_bigger.jpg")
    make_image(p_conflict_b, "teal", size=(1200, 900))
    set_exif_date(p_conflict_b, "2022:02:02 12:00:00")

    # duas fotos com o MESMO NOME mas conteudo diferente - testa colisao
    make_image(os.path.join(staging_b, "Photos/samename.jpg"), "black")

    zip_dir(staging_b, os.path.join(SOURCE_B, "icloud-export/iCloud Photos Part 1 of 1.zip"))

    # segundo zip do source B com um arquivo "samename.jpg" de conteudo
    # diferente NA MESMA pasta relativa - testa colisao no destino
    staging_b2 = os.path.join(HERE, "_staging_b2")
    if os.path.isdir(staging_b2):
        shutil.rmtree(staging_b2)
    make_image(os.path.join(staging_b2, "Photos/samename.jpg"), "white")
    set_exif_date(os.path.join(staging_b2, "Photos/samename.jpg"), "2019:01:01 00:00:00")
    zip_dir(staging_b2, os.path.join(SOURCE_B, "icloud-export/iCloud Photos Part 2 of 2.zip"))

    for d in (staging_a, staging_b, staging_b2):
        shutil.rmtree(d)

    print("Dataset gerado em:")
    print(" ", SOURCE_A)
    print(" ", SOURCE_B)


if __name__ == "__main__":
    main()
