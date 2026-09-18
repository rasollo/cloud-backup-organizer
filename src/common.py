"""
Utilidades compartilhadas pelos scripts do cloud-backup-organizer:
sanitizacao de nomes de arquivo, resolucao de colisoes, e o extrator de
data a partir de nome de arquivo (com todos os casos especiais
descobertos na pratica: WhatsApp, AM/PM, timestamps colados, idiomas
diferentes de screenshot).
"""
import hashlib
import os
import re
import time

CHUNK = 4 * 1024 * 1024

CURRENT_YEAR = time.localtime().tm_year

INVALID_CHARS_RE = re.compile(r'[<>:"\\|?*\x00-\x1f]')

# --- extracao de data a partir do nome do arquivo -------------------------

DATE_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")

# ate 12 chars de separador entre data e hora, pra cobrir "at", "alle"
# (italiano), "a les" (catalao), "as"/"às" (portugues/espanhol) etc.
TIME_RE = re.compile(r"^[^0-9]{0,12}(\d{1,2})[.:h_-](\d{2})(?:[.:h_-]?(\d{2}))?")

# "20140503_230717000_iOS.jpg": hora colada sem separador (HHMMSS +
# milissegundos opcionais).
TIME_GLUED_RE = re.compile(r"^_(\d{2})(\d{2})(\d{2})\d{0,3}(?!\d)")

# "IMG-20170912-WA0054.jpg": o "WA0054" e um contador sequencial do
# WhatsApp, NAO uma hora - precisa ser excluido da extracao de horario.
WA_SEQ_RE = re.compile(r"^[-_]?WA\d+", re.I)

AMPM_RE = re.compile(r"^\s?(AM|PM)", re.I)


def extract_datetime_from_filename(name, max_year=None):
    """Extrai (ano, mes, dia, hora, min, seg) do nome de um arquivo, ou
    None se nao houver data plausivel. Cobre os formatos mais comuns de
    export de foto/video: YYYYMMDD, YYYY-MM-DD, com ou sem hora, em varios
    idiomas e convencoes (WhatsApp, screenshots, camera)."""
    max_year = max_year or (CURRENT_YEAR + 1)
    m = DATE_RE.search(name)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1990 <= y <= max_year and 1 <= mo <= 12 and 1 <= d <= 31):
        return None

    rest = name[m.end():]
    h, mi, s = 0, 0, 0

    glued = TIME_GLUED_RE.match(rest)
    if glued:
        hh, mm, ss = int(glued.group(1)), int(glued.group(2)), int(glued.group(3))
        if 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59:
            h, mi, s = hh, mm, ss
        return (y, mo, d, h, mi, s)

    if WA_SEQ_RE.match(rest):
        return (y, mo, d, h, mi, s)

    tm = TIME_RE.match(rest)
    if tm:
        hh, mm, ss = int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0)
        if 1 <= hh <= 12 and 0 <= mm <= 59 and 0 <= ss <= 59:
            ampm = AMPM_RE.match(rest[tm.end():])
            if ampm:
                period = ampm.group(1).upper()
                if period == "PM" and hh != 12:
                    hh += 12
                elif period == "AM" and hh == 12:
                    hh = 0
            h, mi, s = hh, mm, ss
        elif 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59:
            h, mi, s = hh, mm, ss

    return (y, mo, d, h, mi, s)


def datetime_to_epoch(dt_tuple):
    y, mo, d, h, mi, s = dt_tuple
    try:
        return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    except ValueError:
        return None


# --- nomes de arquivo / caminhos -------------------------------------------

def sanitize_component(name):
    """Remove caracteres invalidos em exFAT/NTFS/FAT de um componente de
    caminho (nao um caminho inteiro - nao mexe em '/')."""
    name = INVALID_CHARS_RE.sub("_", name)
    name = name.rstrip(" .")
    return name or "_"


def sanitize_relpath(entry_name):
    parts = [p for p in entry_name.split("/") if p not in ("", ".", "..")]
    return "/".join(sanitize_component(p) for p in parts)


def unique_dest_path(dest_path, disambiguator):
    """Se dest_path ja existe, gera um caminho alternativo usando
    `disambiguator` (ex: os 8 primeiros chars de um sha256) como sufixo."""
    if not os.path.exists(dest_path):
        return dest_path
    base, ext = os.path.splitext(dest_path)
    candidate = f"{base}__{disambiguator}{ext}"
    if not os.path.exists(candidate):
        return candidate
    i = 2
    while True:
        candidate = f"{base}__{disambiguator}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


# --- hashing -----------------------------------------------------------

def sha256_of(path, chunk_size=CHUNK):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# --- discos/raizes de origem, formato comum de config -----------------------

def parse_roots(root_args):
    """Converte uma lista de argumentos "label=/caminho" (como vem do
    CLI, repetido com --root) num dict {label: path}."""
    roots = {}
    for arg in root_args:
        if "=" not in arg:
            raise ValueError(f"--root deve ser no formato label=/caminho, recebido: {arg!r}")
        label, path = arg.split("=", 1)
        roots[label] = path
    return roots
