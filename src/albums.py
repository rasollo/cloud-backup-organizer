#!/usr/bin/env python3
"""
Gera um manifesto de pertencimento a album (nao pastas fisicas com
symlink - exFAT nao suporta symlink/hardlink, e se o destino final for
uma ferramenta como o Immich, ela gerencia albuns no proprio banco sem
precisar de pastas fisicas).

Deteta albuns a partir da estrutura do Google Takeout:
"<album-root>/<Nome do Album>/arquivo" - por padrao excluindo pastas que
batem com o padrao de ano ("Photos from 2019", "Fotos de 2019" etc, que e
so o ano, nao um album de verdade).
"""
import argparse
import csv
import os
import re

DEFAULT_ALBUM_ROOTS = ["Takeout/Google Photos", "Takeout/Google Fotos"]
YEAR_FOLDER_RE = re.compile(r"^(Photos from|Fotos de|Fotos from)\s+\d{4}$", re.I)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--organized", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--album-root", action="append", dest="album_roots",
                     help="prefixo de caminho (dentro do zip) onde procurar pastas de album (repita pra varios; default: Takeout/Google Photos e variantes)")
    args = ap.parse_args()

    album_roots = args.album_roots or DEFAULT_ALBUM_ROOTS

    with open(args.organized, newline="", encoding="utf-8") as f:
        organized_by_sha = {r["sha256"]: r["new_path"] for r in csv.DictReader(f)}

    with open(args.manifest, newline="", encoding="utf-8") as f:
        manifest_rows = list(csv.DictReader(f))

    memberships = []
    albums_seen = set()
    for r in manifest_rows:
        entry = r["entry_name"]
        for root in album_roots:
            prefix = root.rstrip("/") + "/"
            if not entry.startswith(prefix):
                continue
            rest = entry[len(prefix):]
            parts = rest.split("/")
            if len(parts) < 2:
                continue
            album = parts[0]
            if YEAR_FOLDER_RE.match(album):
                continue
            path = organized_by_sha.get(r["sha256"])
            if not path or not os.path.exists(path):
                continue
            memberships.append((album, r["sha256"], path, entry))
            albums_seen.add(album)
            break

    with open(args.report, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["album_name", "sha256", "current_path", "original_entry_name"])
        writer.writerows(memberships)

    print(f"[albums] distinct albums: {len(albums_seen)}")
    print(f"[albums] photo-album links: {len(memberships)}")
    print(f"[albums] unique photos involved: {len(set(m[1] for m in memberships))}")


if __name__ == "__main__":
    main()
