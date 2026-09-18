#!/usr/bin/env python3
"""
Duplicatas de video: mesmo nome-base (ignorando sufixo de colisao) NA
MESMA PASTA, MESMA EXTENSAO e DURACAO igual (dentro de --duration-tolerance
segundos) - so nesse caso e seguro dizer que e o mesmo video reexportado
em qualidade diferente (fontes diferentes, ex Google Takeout vs iCloud).

Extensoes diferentes ou duracao muito diferente ficam intocadas (podem
ser videos genuinamente diferentes que so coincidem no nome, ou o
video/foto companheiro de uma Live Photo).

Mantem o de maior tamanho (proxy de bitrate/qualidade) no lugar, move o
resto pra --review-dir.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict

VID_EXTS = {"mp4", "mov", "m4v", "3gp", "avi", "mkv"}
SUFFIX_RE = re.compile(r"__[0-9a-f]{8}(_\d+)?$")


def get_durations(paths):
    if not paths:
        return {}
    listfile = "/tmp/_dedupe_video_paths.txt"
    with open(listfile, "w", encoding="utf-8") as f:
        f.write("\n".join(paths))
    result = subprocess.run(["exiftool", "-j", "-q", "-Duration", "-@", listfile],
                             capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    out = {}
    for entry in data:
        d = entry.get("Duration")
        if isinstance(d, str):
            m = re.match(r"([\d.]+)", d)
            d = float(m.group(1)) if m else None
        out[entry["SourceFile"]] = d
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="pasta organizada por data (by-date) a analisar")
    ap.add_argument("--review-dir", required=True)
    ap.add_argument("--duration-tolerance", type=float, default=1.0, help="tolerancia de diferenca de duracao em segundos (default: 1.0)")
    args = ap.parse_args()

    groups = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(args.root):
        for name in filenames:
            stem, ext = os.path.splitext(name)
            ext = ext.lstrip(".").lower()
            if ext not in VID_EXTS:
                continue
            base = SUFFIX_RE.sub("", stem)
            groups[(dirpath, base.lower())].append((os.path.join(dirpath, name), ext))

    candidates = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"[dedupe-video] grupos candidatos: {len(candidates)}", flush=True)

    all_paths = [p for group in candidates.values() for p, ext in group]
    print(f"[dedupe-video] lendo duracao de {len(all_paths)} arquivos...", flush=True)
    durations = get_durations(all_paths)

    moved = kept_groups = skipped = 0
    for key, items in candidates.items():
        by_ext = defaultdict(list)
        for path, ext in items:
            by_ext[ext].append(path)

        for ext, paths in by_ext.items():
            if len(paths) < 2:
                continue
            durs = [(p, durations.get(p)) for p in paths]
            if any(d is None for _, d in durs):
                skipped += 1
                continue
            durs.sort(key=lambda x: x[1])
            if durs[-1][1] - durs[0][1] > args.duration_tolerance:
                skipped += 1
                continue

            sizes = sorted(((p, os.path.getsize(p)) for p in paths), key=lambda x: x[1], reverse=True)
            kept_groups += 1
            for p, _ in sizes[1:]:
                rel = os.path.relpath(p, args.root)
                dest = os.path.join(args.review_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(p, dest)
                moved += 1

    print(f"[dedupe-video] grupos resolvidos: {kept_groups} | arquivos movidos: {moved} | pulados: {skipped}")


if __name__ == "__main__":
    main()
