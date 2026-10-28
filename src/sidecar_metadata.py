#!/usr/bin/env python3
"""
Recupera data (e GPS/descricao, quando houver) de arquivos sem EXIF usando
os metadados que vieram JUNTO nas exportacoes, mas fora do arquivo:
  - Google Takeout: um .json por foto (photoTakenTime, geoData, description)
  - iCloud: "Photo Details*.csv" em cada zip "iCloud Photos Part N"
    (originalCreationDate, favorite)

Dois passos:
  scan   so le. Para cada arquivo de --target, acha onde ele estava nos
         zips de origem pelo sha256 (manifest.csv/duplicates.csv do
         extract.py) e procura o .json/.csv correspondente. Gera um CSV.
  apply  grava no arquivo o que foi achado (sem sobrescrever o que ele ja
         tiver) e move para DEST/AAAA/MM/. Sem --execute so simula.

Licoes aprendidas que este script ja incorpora:
- O Takeout reparte a exportacao em varios zips e o .json de uma foto
  muitas vezes esta em OUTRO zip que a propria foto: indexa os .json de
  todos os zips, pela pasta dentro do zip ("Takeout/Google Fotos/Photos
  from 2019/"), nao por zip.
- O nome do .json nem sempre e "<foto>.json": vem como
  "<foto>.supplemental-metadata.json", truncado em ~46 caracteres
  ("<foto>.supplemen.json", "<inicio-do-nome>.json"), e o "(1)" de
  duplicata vai para depois da extensao ("IMG_1234.jpg(1).json" e o
  .json de "IMG_1234(1).jpg").
- O CSV do iCloud so vale para o zip em que esta. Casar so pelo nome
  ("IMG_1234.JPG") em outro zip da datas de OUTRA foto: nomes se repetem
  entre aparelhos e anos (na pratica deu diferencas de 2 a 7 anos). Esse
  casamento so pelo nome so e usado quando nao ha nenhuma fonte confiavel,
  e fica marcado como "apple-so-nome".
- GPS no .json quase sempre e copia do proprio EXIF: em ~18 mil arquivos
  sem EXIF, so 28 tinham GPS. A data e o ganho real.
- Para midia salva de mensageiro (sem EXIF), o photoTakenTime e a data em
  que entrou no Google Fotos, nao a da foto original: mes aproximado.

Uso:
  python3 sidecar_metadata.py scan --root google=/mnt/b1 --root icloud=/mnt/b2 \\
      --manifest ../reports/manifest.csv --duplicates ../reports/duplicates.csv \\
      --target /mnt/photo-library/by-date/sem-data --report ../reports/sidecar-metadata.csv
  python3 sidecar_metadata.py apply --report ../reports/sidecar-metadata.csv \\
      --dest /mnt/photo-library/by-date --log ../reports/sidecar-apply.csv [--execute]
"""
import argparse
import collections
import csv
import datetime
import json
import os
import re
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from common import parse_roots, sha256_of  # noqa: E402

VIDEO = {".mov", ".mp4", ".m4v", ".3gp", ".avi", ".mts", ".mpg", ".wmv"}
XMP_ONLY = {".png", ".gif"}
NAO_SAO_FOTO = {"metadata.json", "print-subscriptions.json", "shared_album_comments.json",
                "user-generated-memory-titles.json"}
REPORT_FIELDS = ["status", "path", "sha256", "date", "source", "gps", "description", "favorite"]


# --- scan --------------------------------------------------------------------

def nucleo_json(nome):
    """nome do arquivo de midia a que um .json do Takeout se refere (pode vir truncado)."""
    s = re.sub(r"\.s(?:u[a-z-]*)?(\(\d+\))?$", lambda m: m.group(1) or "", nome[:-5])
    return s.rstrip(".")


def nucleos_possiveis(nome):
    """nucleos de .json que podem se referir a este arquivo (nome inteiro, sem extensao, (1), truncado)."""
    base = os.path.splitext(nome)[0]
    m = re.match(r"^(.*)(\(\d+\))(\.[^.]+)$", nome)
    alt = m.group(1) + m.group(3) + m.group(2) if m else None
    return {nome, base, alt} | {nome[:k] for k in range(30, len(nome))}


def data_icloud(s):
    t = datetime.datetime.strptime(s, "%A %B %d,%Y %I:%M %p GMT")
    return t.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)


def indexar(roots):
    """google[pasta][nucleo] = [(data, extra)]; apple[(zip, pasta)][nome] = (data, favorito);
    apple_global[nome] = {datas}"""
    google = collections.defaultdict(lambda: collections.defaultdict(list))
    apple = collections.defaultdict(dict)
    apple_global = collections.defaultdict(set)

    def json_google(pasta, nome, ler):
        if nome in NAO_SAO_FOTO:
            return
        try:
            j = json.loads(ler())
            t = int(j["photoTakenTime"]["timestamp"])
        except (ValueError, KeyError, TypeError):
            return
        extra = {}
        for k in ("geoDataExif", "geoData"):
            g = j.get(k) or {}
            if g.get("latitude") or g.get("longitude"):
                extra["gps"] = f'{g["latitude"]},{g["longitude"]},{g.get("altitude") or 0}'
                break
        if (j.get("description") or "").strip():
            extra["description"] = j["description"].strip()
        if j.get("favorited"):
            extra["favorite"] = "yes"
        google[pasta][nucleo_json(nome)].append((datetime.datetime.fromtimestamp(t), extra))

    def csv_apple(chave, texto):
        for r in csv.DictReader(texto.splitlines()):
            try:
                d = data_icloud(r["originalCreationDate"])
            except (ValueError, KeyError):
                continue
            apple[chave][r["imgName"]] = (d, r.get("favorite") == "yes")
            apple_global[r["imgName"]].add(d)

    for raiz in roots.values():
        for d, _, fs in os.walk(raiz):
            for f in fs:
                p = os.path.join(d, f)
                if f.endswith(".json"):
                    json_google(os.path.relpath(d, raiz), f, lambda p=p: open(p, "rb").read())
                elif f.startswith("Photo Details") and f.endswith(".csv"):
                    csv_apple((raiz, os.path.relpath(d, raiz)), open(p, encoding="utf-8", errors="replace").read())
                elif f.lower().endswith(".zip"):
                    try:
                        with zipfile.ZipFile(p) as z:
                            for i in z.infolist():
                                pasta, nome = os.path.split(i.filename)
                                if nome.endswith(".json") and i.file_size < 1 << 20:
                                    json_google(pasta, nome, lambda i=i: z.read(i))
                                elif nome.startswith("Photo Details") and nome.endswith(".csv"):
                                    csv_apple((p, pasta), z.read(i).decode("utf-8", "replace"))
                    except (zipfile.BadZipFile, OSError):
                        print(f"   zip com erro, ignorado: {p}", file=sys.stderr)
    return google, apple, apple_global


def locais(manifests, roots, shas):
    """sha256 -> [(caminho do zip, entrada)] de manifest.csv/duplicates.csv do extract.py."""
    onde = collections.defaultdict(list)
    for m in manifests:
        with open(m, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["sha256"] in shas and r["source_root"] in roots:
                    onde[r["sha256"]].append((os.path.join(roots[r["source_root"]], r["source_zip_relpath"]),
                                              r["entry_name"]))
    return onde


def scan(args):
    roots = parse_roots(args.root)
    print("1/3 sha256 dos arquivos de --target...", flush=True)
    arquivos = [os.path.join(d, f) for d, sub, fs in os.walk(args.target)
                for f in fs if not f.startswith(".")]
    arquivos = [(p, sha256_of(p)) for p in arquivos]
    onde = locais([args.manifest] + ([args.duplicates] if args.duplicates else []), roots, {s for _, s in arquivos})
    print(f"   {len(arquivos)} arquivos, {sum(1 for _, s in arquivos if s in onde)} achados nos zips de origem")
    print("2/3 indexando .json do Google e CSV do iCloud...", flush=True)
    google, apple, apple_global = indexar(roots)
    print(f"   google: {sum(len(v) for g in google.values() for v in g.values())} json | "
          f"icloud: {sum(len(v) for v in apple.values())} linhas")
    print("3/3 cruzando...", flush=True)
    linhas = []
    for p, s in arquivos:
        datas, extra = {}, {}
        for zp, entrada in onde.get(s, []):
            d, nome = os.path.split(entrada)
            por_nucleo = google.get(d, {})
            for nuc in nucleos_possiveis(nome):
                for t, e in por_nucleo.get(nuc, ()):
                    datas.setdefault(t, set()).add("google")
                    for k, v in e.items():
                        extra.setdefault(k, v)
            t, fav = apple.get((zp, d), {}).get(nome, (None, False))
            if t is not None:
                datas.setdefault(t, set()).add("apple")
                if fav:
                    extra.setdefault("favorite", "yes")
            elif len(apple_global.get(nome, ())) == 1:
                datas.setdefault(next(iter(apple_global[nome])), set()).add("apple-so-nome")
        if s not in onde:
            status, data, fonte = "not-in-sources", "", ""
        elif not datas:
            status, data, fonte = "no-metadata", "", ""
        else:
            # data so pelo nome (outra foto com o mesmo IMG_xxxx) so vale quando nao ha fonte confiavel
            datas = {t: f for t, f in datas.items() if f - {"apple-so-nome"}} or datas
            # mesma foto pode ter datas a poucos minutos (fuso/arredondamento): conflito so se o dia muda
            dias = {t.date() for t in datas}
            fonte = "+".join(sorted(set().union(*datas.values())))
            status = fonte if len(dias) == 1 else "conflict"
            data = (min(datas).isoformat(sep=" ") if len(dias) == 1
                    else " | ".join(sorted(x.isoformat(sep=" ") for x in datas)))
        linhas.append([status, p, s, data, fonte, extra.get("gps", ""), extra.get("description", ""),
                       extra.get("favorite", "")])
    with open(args.report, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(REPORT_FIELDS)
        w.writerows(linhas)
    for st, n in collections.Counter(l[0] for l in linhas).most_common():
        print(f"   {st:28} {n}")
    print(f"   com gps: {sum(1 for l in linhas if l[5])} | com descricao: {sum(1 for l in linhas if l[6])}")
    print(f"-> {args.report}")


# --- apply -------------------------------------------------------------------

def ja_tem(paths):
    """{caminho: {"date", "gps", "description"} que o arquivo ja tem}, numa chamada so do exiftool."""
    if not paths:
        return {}
    out = subprocess.run(["exiftool", "-j", "-n", "-q", "-m", "-DateTimeOriginal", "-CreateDate", "-GPSLatitude",
                          "-GPSCoordinates", "-ImageDescription", "-Description", "-@", "-"],
                         input="\n".join(paths), capture_output=True, text=True).stdout
    res = {}
    for e in json.loads(out or "[]"):
        tem = set()
        if any(str(e.get(k) or "").strip("0: ") for k in ("DateTimeOriginal", "CreateDate")):
            tem.add("date")
        if e.get("GPSLatitude") or e.get("GPSCoordinates"):
            tem.add("gps")
        if str(e.get("ImageDescription") or e.get("Description") or "").strip():
            tem.add("description")
        res[e["SourceFile"]] = tem
    return res


def tags(ext, quando, gps, descricao, tem):
    """argumentos do exiftool para o que o arquivo ainda nao tem."""
    a = []
    off = quando.astimezone().strftime("%z")
    off = off[:3] + ":" + off[3:]
    local = quando.strftime("%Y:%m:%d %H:%M:%S")
    if "date" not in tem:
        if ext in VIDEO:
            # com -api QuickTimeUTC o exiftool converte para UTC sozinho: passar a hora local com fuso.
            # Passar a hora ja em UTC sem fuso faz ele converter de novo (ficava 3 h adiantado no Brasil)
            a += [f"-QuickTime:CreateDate={local}{off}", f"-QuickTime:ModifyDate={local}{off}",
                  f"-Keys:CreationDate={local}{off}"]
        elif ext in XMP_ONLY:
            a += [f"-XMP-exif:DateTimeOriginal={local}{off}", f"-XMP-xmp:CreateDate={local}{off}"]
        else:
            a += [f"-EXIF:DateTimeOriginal={local}", f"-EXIF:CreateDate={local}", f"-EXIF:OffsetTimeOriginal={off}"]
    if gps and "gps" not in tem:
        lat, lon, alt = (float(x) for x in gps.split(","))
        if ext in VIDEO:
            a += [f"-Keys:GPSCoordinates={lat}, {lon}, {alt}", f"-UserData:GPSCoordinates={lat}, {lon}, {alt}"]
        else:
            g = "XMP-exif" if ext in XMP_ONLY else "EXIF"
            a += [f"-{g}:GPSLatitude={abs(lat)}", f"-{g}:GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
                  f"-{g}:GPSLongitude={abs(lon)}", f"-{g}:GPSLongitudeRef={'E' if lon >= 0 else 'W'}"]
            if alt:
                a += [f"-{g}:GPSAltitude={abs(alt)}", f"-{g}:GPSAltitudeRef={0 if alt >= 0 else 1}"]
    if descricao and "description" not in tem:
        descricao = " ".join(descricao.split())  # quebra de linha no argfile do exiftool viraria outro argumento
        a += [f"-XMP-dc:Description={descricao}"] + ([] if ext in XMP_ONLY | VIDEO else [f"-EXIF:ImageDescription={descricao}"])
    return a


def destino(dest, nome, quando, sha, reservados):
    pasta = os.path.join(dest, f"{quando:%Y}", f"{quando:%m}")
    base, ext = os.path.splitext(nome)
    for i in range(1000):
        alvo = os.path.join(pasta, nome if i == 0 else f"{base}_{i}{ext}")
        if alvo in reservados:
            continue
        if not os.path.exists(alvo):
            reservados.add(alvo)
            return alvo, False
        if sha256_of(alvo) == sha:
            return alvo, True
    raise RuntimeError(f"nomes esgotados para {nome} em {pasta}")


def apply(args):
    with open(args.report, newline="", encoding="utf-8") as f:
        itens = [r for r in csv.DictReader(f) if r["status"] not in ("no-metadata", "not-in-sources")
                 and os.path.exists(r["path"])]
    print(f"lendo metadados de {len(itens)} arquivos...", flush=True)
    tem = ja_tem([r["path"] for r in itens])
    reservados, plano = set(), []
    for r in itens:
        datas = [datetime.datetime.fromisoformat(x) for x in r["date"].split(" | ")]
        if len(datas) > args.max_conflicting_dates:
            plano.append(("skipped: too many dates", r, "", []))
            continue
        quando = min(datas)  # conflito: a mais antiga, que costuma ser a da foto
        alvo, igual = destino(args.dest, os.path.basename(r["path"]), quando, r["sha256"], reservados)
        if igual:
            plano.append(("already-in-dest", r, alvo, []))
            continue
        plano.append((quando, r, alvo, tags(os.path.splitext(r["path"])[1].lower(), quando, r["gps"],
                                            r["description"], tem.get(r["path"], set()))))
    mover = [x for x in plano if isinstance(x[0], datetime.datetime)]
    print(f"mover: {len(mover)} | ja no destino: {sum(1 for x in plano if x[0] == 'already-in-dest')} | "
          f"datas demais: {sum(1 for x in plano if str(x[0]).startswith('skipped'))}")
    print(f"gravar data: {sum(1 for x in mover if any('Date' in t for t in x[3]))} | "
          f"gps: {sum(1 for x in mover if any('GPS' in t for t in x[3]))} | "
          f"descricao: {sum(1 for x in mover if any('Description' in t for t in x[3]))}")
    print("por ano:", dict(sorted(collections.Counter(f"{x[0]:%Y}" for x in mover).items())))
    if not args.execute:
        print("(simulacao; use --execute)")
        return
    # registro gravado linha a linha ANTES de mover: se cair no meio, da para desfazer o que ja foi
    with open(args.log, "a", newline="", encoding="utf-8") as logf:
        log = csv.writer(logf)
        if logf.tell() == 0:
            log.writerow(["action", "from", "to", "tags"])
        for quando, r, alvo, t in plano:
            if not isinstance(quando, datetime.datetime):
                log.writerow([quando, r["path"], alvo, ""])
                continue
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            log.writerow(["moved", r["path"], alvo, " ".join(x.split("=")[0] for x in t)])
            logf.flush()
            os.rename(r["path"], alvo)
        # um exiftool so para tudo: -execute entre os blocos, senao o ultimo valor de cada tag vale para todos
        linhas = []
        for quando, r, alvo, t in mover:
            if t:
                linhas += t + [alvo, "-execute"]
        if linhas:
            # opcoes antes de -@ so valem para o 1o bloco; -common_args repete em todos
            subprocess.run(["exiftool", "-@", "-", "-common_args", "-q", "-m", "-overwrite_original", "-api", "QuickTimeUTC"],
                           input="\n".join(linhas), text=True)
    # o exiftool recusa gravar quando a extensao nao bate com o conteudo (JPEG salvo como .PNG,
    # MP4 como .mov) e so avisa: conferir o que ficou sem data depois de gravar
    depois = ja_tem([alvo for _, _, alvo, t in mover if t])
    sem = [alvo for _, _, alvo, t in mover if t and any("Date" in x for x in t) and "date" not in depois.get(alvo, set())]
    if sem:
        print(f"ATENCAO: {len(sem)} arquivos continuam sem data (extensao errada?), ex.: {sem[:3]}")
    for quando, r, alvo, t in mover:
        # mtime = data achada: reserva para quem nao ler o metadado gravado
        os.utime(alvo, (quando.timestamp(), quando.timestamp()))
    print(f"registro: {args.log}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="so le: acha data/GPS/descricao nos .json do Google e CSV do iCloud")
    s.add_argument("--root", action="append", required=True, help="label=/caminho, igual ao inventory.py (repetivel)")
    s.add_argument("--manifest", required=True, help="manifest.csv do extract.py")
    s.add_argument("--duplicates", help="duplicates.csv do extract.py (outras copias do mesmo arquivo)")
    s.add_argument("--target", required=True, help="pasta com os arquivos sem data (ex.: by-date/sem-data)")
    s.add_argument("--report", required=True)
    a = sub.add_parser("apply", help="grava os metadados achados e move para DEST/AAAA/MM")
    a.add_argument("--report", required=True, help="CSV gerado pelo scan")
    a.add_argument("--dest", required=True, help="raiz by-date")
    a.add_argument("--log", required=True, help="registro de/para, para desfazer")
    a.add_argument("--max-conflicting-dates", type=int, default=3,
                   help="com mais datas diferentes que isso o arquivo fica onde esta (padrao 3)")
    a.add_argument("--execute", action="store_true", help="sem isso so simula")
    args = ap.parse_args()
    scan(args) if args.cmd == "scan" else apply(args)


if __name__ == "__main__":
    main()
