# cloud-backup-organizer

Turn a messy pile of iCloud, Google Takeout, and Microsoft/OneDrive export
zips — scattered across several disks, often overlapping, sometimes
partially corrupted — into a single deduplicated, date-organized photo and
video library ready to be pointed at [Immich](https://immich.app) (or any
other photo manager, or just Finder/Explorer).

Built out of a real, very messy migration: ~700 export zips across four
drives, a couple of TB of iCloud/Google/OneDrive history going back to the
2000s, and every date-naming edge case those exports throw at you. Every
script here reflects a real bug we hit and fixed — see
[Lessons learned](#lessons-learned) before you trust any of this blindly.

## What it does

```
export zips (iCloud / Google Takeout / OneDrive, on N drives)
        │
        ▼
 1. inventory.py     hash every archive, find duplicate backup sessions
        │
        ▼
 2. extract.py       pull out photos/videos only, dedup by content hash
        │
        ▼
 3. organize.py      sort into <year>/<month>/ using EXIF, then filename
        │
        ▼
 4. fix_mtime.py      sync filesystem mtime so tools without EXIF-reading
        │             (some viewers) show the right date
        ▼
 4b. write_exif_dates.py  write the filename-derived date as REAL EXIF
        │             into files that never had any - makes the date
        │             permanent, immune to a viewer re-scanning the file
        ▼
 5. live_photos.py    keep HEIC+MOV Live Photo pairs together
 6. dedupe_visual.py  catch near-duplicate photos (same shot, re-compressed
                       by a different export pipeline)
 7. dedupe_video.py   same idea, for videos
 8. resolve_conflicts.py  same photo from two sources at the exact same
                           EXIF timestamp → keep the higher-resolution one
 9. albums.py         (optional) build an album-membership manifest from
                       Google Takeout's folder structure, for later import
                       into a tool that manages albums itself
10. detect_photoslibrary_internals.py  (optional) if a source export is
                       actually a raw sync of a macOS *.photoslibrary
                       package rather than a proper export, separate out
                       its unpairable internal Live Photo video files
        │
        ▼
   by-date/YYYY/MM/... ──────► point Immich's External Library at this

 Two more passes worth running over whatever landed in by-date/sem-data/
 (files with no EXIF and no usable date in the filename):

 11. recover_undated_photos.py  pull out the photos that are actually
                       worth keeping among the "no date" pile, using GPS
                       and sibling dates as weak signals (see below)
 12. dedupe_visual_global.py  catch near-duplicates that don't share a
                       filename at all — the same photo forwarded several
                       times through a messaging app, each copy renamed
                       to a fresh UUID (dedupe_visual.py can't catch these:
                       it only ever compares files that already share a
                       base name)
```

Every step is:
- **read-only on your original exports** — nothing is ever deleted or
  modified at the source; steps that would otherwise delete something
  *move it to a review folder* instead.
- **resumable** — each step writes a CSV as it goes and picks up where it
  left off if interrupted.
- **idempotent-ish** — re-running a step after a partial run only
  processes what's left to do.

## Requirements

- Python 3.9+
- [Pillow](https://pypi.org/project/Pillow/) (`pip install Pillow` or your
  distro's `python3-pil`)
- [`exiftool`](https://exiftool.org/) on your `PATH` (`apt install
  libimage-exiftool-perl`, `brew install exiftool`, …)

No other dependencies. Everything else is the standard library.

## Getting your export data

- **iCloud**: [privacy.apple.com](https://privacy.apple.com) → "Get a copy
  of your data" → select Photos (and anything else you want). Apple splits
  large libraries into several zips (`iCloud Photos Part N of M.zip`).
- **Google**: [takeout.google.com](https://takeout.google.com) → select
  "Google Photos" (and Drive if you want that too) → export. Google also
  splits into numbered zips.
- **Microsoft/OneDrive**: [account.microsoft.com/privacy](https://account.microsoft.com/privacy)
  → "Download your data", or just select everything in OneDrive and
  "Download" as zip from the web UI for smaller libraries.

Download everything into your source drive(s) — you don't need to unzip
anything, the tool reads zips directly.

## Quickstart

```bash
cd src

# 1. Inventory every export zip across all your drives.
#    Repeat --root for each drive/mount you have exports scattered across.
python3 inventory.py \
  --root icloud=/mnt/backup1 \
  --root google=/mnt/backup2 \
  --root onedrive=/mnt/backup3 \
  --output ../reports/inventory.csv

# 2. Extract photos/videos, deduplicated by content hash, into DEST.
python3 extract.py \
  --inventory ../reports/inventory.csv \
  --dest /mnt/photo-library \
  --reports-dir ../reports \
  --prefer icloud --prefer google   # tie-break order when two sources
                                     # disagree and there's no integrity data

# 3. Organize into DEST/by-date/YYYY/MM/.
python3 organize.py \
  --manifest ../reports/manifest.csv \
  --dest /mnt/photo-library \
  --reports-dir ../reports

# 4. Sync filesystem mtime for files dated from their filename
#    (harmless but recommended - see "Why mtime matters" below).
python3 fix_mtime.py --organized ../reports/organized.csv

# 4b. Make it permanent: write the same date as real EXIF into files
#     that never had any (see "Why mtime matters" for why this matters
#     beyond what fix_mtime.py already does).
python3 write_exif_dates.py --manifest ../reports/manifest.csv --organized ../reports/organized.csv

# 5. Optional cleanup passes, in any order you like:
python3 live_photos.py --manifest ../reports/manifest.csv --organized ../reports/organized.csv --reports-dir ../reports
python3 dedupe_visual.py --root /mnt/photo-library/by-date --report ../reports/near-duplicates.csv --apply --review-dir /mnt/photo-library/review-visual-dupes
python3 dedupe_video.py --root /mnt/photo-library/by-date --review-dir /mnt/photo-library/review-video-dupes
python3 resolve_conflicts.py --root /mnt/photo-library/by-date --exif-cache ../reports/exif-cache.json --organized ../reports/organized.csv --report ../reports/conflicts.csv --review-dir /mnt/photo-library/review-conflicts

# Only if a source export turned out to be a raw *.photoslibrary sync
# (see "Lessons learned" below):
python3 detect_photoslibrary_internals.py --manifest ../reports/manifest.csv --organized ../reports/organized.csv --review-dir /mnt/photo-library/review-unpaired-live-videos --report ../reports/photoslibrary-internals.csv

# 6. Sanity-check a random sample before you trust the result.
#    --organized makes it check the CURRENT (post-organize.py) path
#    instead of the pre-organize extraction path.
python3 validate_sample.py --manifest ../reports/manifest.csv --organized ../reports/organized.csv --sample-size 100

# 7. Optional: dig through by-date/sem-data/ for real photos worth
#    recovering (see "Finding real photos in the undated pile" below),
#    then check the recovered pile itself for hidden duplication.
python3 recover_undated_photos.py \
  --root /mnt/photo-library/by-date/sem-data \
  --manifest ../reports/manifest.csv --organized ../reports/organized.csv \
  --review-dir /mnt/photo-library/review-recovered
python3 dedupe_visual_global.py \
  --root /mnt/photo-library/review-recovered \
  --review-dir /mnt/photo-library/review-recovered/_duplicatas-internas
```

Then point Immich at `/mnt/photo-library/by-date` as a **read-only External
Library**. Keep the mount read-only (`:ro` in your `docker-compose.yml`
bind mount) — Immich's own uploads (from the mobile app etc.) should go to
a *separate*, writable location, not into this archive. See
[Wiring it into Immich](#wiring-it-into-immich) below.

## Why mtime matters

Files that get their date from **EXIF** are fine no matter what — any
viewer worth using reads the capture date straight from the embedded
metadata, filesystem timestamps be damned.

Files that get their date from **filename** (screenshots, WhatsApp media,
anything with no EXIF) are a different story: after `organize.py` moves
them, their filesystem `mtime` is just "whenever the script touched the
file" — today, not 2017. Immich (and some other tools) fall back to mtime
for exactly these EXIF-less files. Run `fix_mtime.py` or you'll see today's
date on every screenshot you own.

`fix_mtime.py` alone isn't the end of the story, though: an **external
library** gets periodically re-scanned, and on each scan Immich re-derives
each asset's date from the file. For a file with real EXIF that's a
no-op — it just reads the same value again. For a file with no EXIF,
there's nothing stable to re-derive, so anything you edited by hand in
Immich's own UI for that asset gets silently reverted on the next scan
(we watched this happen: a manual date edit reverted the instant we
tried it, no scan needed to trigger it — Immich just doesn't persist an
edit for an asset it doesn't consider itself to own). `mtime` isn't part
of what gets re-derived and displayed the same way EXIF is, so it's a
half-measure. `write_exif_dates.py` is the real fix: it writes the
filename-derived date as actual EXIF tags into the file, at which point
it behaves exactly like a native EXIF file — permanent, and immune to
every future re-scan.

## Finding real photos in the undated pile

`organize.py` dumps anything with no EXIF and no parseable filename date
into `by-date/sem-data/`. In a messy real-world library that pile is mostly
junk (icons, stickers, screenshots), but it's not *all* junk — messaging
apps strip EXIF from everything, so real photos shared over WhatsApp/
Telegram end up in there too, indistinguishable from the junk by filename
alone (usually a random UUID).

`recover_undated_photos.py` pulls the worthwhile files out using three
weak signals, from most to least trustworthy:

1. **GPS is still present.** Some messaging apps strip the capture date
   but leave GPS tags alone. A photo with coordinates is real, full stop
   — goes straight to a `com-localizacao/` review folder.
2. **A narrow, trustworthy date range among "siblings."** Files that came
   out of the *same source zip* as an undated file often include some
   photos that *did* have a real EXIF or filename date. If those dates
   cluster into a narrow span (a real photo session, not decades), that's
   a reasonable era estimate for the undated file too — filed into
   `por-epoca-aproximada/<year>/`.
3. **Nothing.** Filed into `sem-pista/` for manual review, still separated
   from the pure junk by the resolution floor (`--min-megapixels`, default
   1.0) so at least you're not scrolling through icons.

This is deliberately conservative — it recovers candidates for a human to
confirm, not a final answer. In one real run it separated ~1,900 likely
real photos out of a pile of several thousand.

## Lessons learned

A few non-obvious things this project got wrong on the first try, in case
you're tempted to skip a step:

- **Never trust a zip's internal per-entry timestamp** (`ZipInfo.date_time`
  in Python). We initially used it as a fallback when EXIF was missing. It
  turned out every entry inside a given export zip has the *same*
  timestamp — the date the export was *packaged*, not the date the photo
  was taken. This silently gave ~120,000 files the same wrong date. Deleted
  that fallback entirely; `organize.py` never uses it.
- **Filename date parsing has more edge cases than you'd think**: Catalan
  "a les" instead of "at" (7 chars, not 4 — don't hardcode a short prefix
  tolerance), single-digit hours ("9.45.19", not "09.45.19"), AM/PM
  suffixes that need 12→24h conversion, WhatsApp's `-WA0054` sequence
  number that looks exactly like a glued-on time but isn't, and
  `YYYYMMDD_HHMMSSmmm` timestamps with no separator at all between date and
  time. `common.py`'s `extract_datetime_from_filename()` handles all of
  these — extend the regexes there, not in five different scripts.
- **A collision-suffixed pair (`name.jpg` / `name__abcd1234.jpg`) is
  *never* an exact duplicate** — if the content hash matched, `extract.py`
  would already have deduplicated it before either file got a suffix. So
  these pairs are guaranteed to differ in content; what's usually going on
  is the *same photo* re-exported at different quality by two different
  pipelines (Google's JPEG vs. Apple's HEIC, for instance) — that's what
  `dedupe_visual.py` (images) and `dedupe_video.py` (video) are for.
- **Don't compare videos across a Live Photo pair.** A HEIC and its
  companion MOV legitimately share the exact same capture timestamp — if
  your "same timestamp = duplicate" logic doesn't exclude video-vs-photo
  comparisons, you'll flag every Live Photo as a conflict. Run
  `live_photos.py` before `resolve_conflicts.py`, and note that
  `resolve_conflicts.py` only ever compares images against images.
- **exFAT can't host a database.** If your destination drive is
  exFAT/FAT32 (common for a portable/shared drive), it's fine as a
  read-only media store, but don't point anything like Postgres or Immich's
  upload location at it — no POSIX permissions, no hard links, chown fails
  outright. Keep app data on a native filesystem (ext4, APFS, NTFS) and use
  exFAT only for the read-only photo library itself.
- **A synced `.photoslibrary` folder isn't an export.** If someone backed
  up the raw `Pictures/*.photoslibrary` package (e.g. via a generic Drive
  or OneDrive folder sync) instead of using Photos.app's "Export", you'll
  find files like `jpegvideocomplement_7c18.mov` and
  `fullsizeoutput_7cff.jpeg` — Photos.app's *internal* names for a Live
  Photo's video half and its rendered still. These are legitimate content,
  but the two only correlate through Photos.app's internal SQLite
  database, which isn't part of the backup — filenames don't match, and in
  practice neither do timestamps (we found videos and their likely photo
  in the same folder off by 8+ minutes, not the same second). Don't expect
  `live_photos.py`-style filename pairing to work here;
  `detect_photoslibrary_internals.py` at least tries a timestamp match and
  is honest about it usually finding nothing, separating the orphaned
  videos out so they don't clutter browsing the real library.
- **exiftool's `-@` argfile needs `-execute` between files, or every file
  gets the LAST one's values.** Writing different metadata to different
  files in one `exiftool -@ argfile` call looks like it should work if you
  just repeat `-TAG=value ... file` blocks one after another — it doesn't.
  Without an `-execute` line between blocks, exiftool parses the *entire*
  argfile as one command: every `-TAG=value` setting accumulates (the last
  one for a given tag wins), and *that* final set of values gets applied
  to every file argument in the file, regardless of which block it came
  from. We caught this because a live run got killed partway through and
  every file touched so far had the same (wrong) date - the date meant for
  whichever file was last in the batch at that point. `write_exif_dates.py`
  puts `-execute` after every file's block for exactly this reason. If
  you're scripting exiftool with per-file arguments some other way, test
  it on three throwaway files first and check all three came out
  different, not just that the command exited 0.
- **A "review" folder beats a `rm`.** Every step that would otherwise
  delete something instead moves it to a review directory you choose.
  Disk is cheap; a wrongly-deleted-and-recompressed-away photo isn't
  coming back.
- **Filename-based dedup misses forwarded messaging photos entirely.**
  `dedupe_visual.py` only compares files that already share a base name
  (`IMG_5353.jpg` vs `IMG_5353(1).jpg`) — cheap, and correct for exports,
  where a re-encoded duplicate always keeps some trace of the original
  name. But a photo forwarded through WhatsApp/Telegram gets a *fresh
  random UUID every single hop*, so there's no shared name to group by,
  even though the image itself is unchanged or barely re-compressed.
  `dedupe_visual_global.py` compares every image in a folder against
  every other one instead of relying on names — O(n²), but a perceptual
  hash comparison is just a XOR and a popcount, so it's still fast (~4s
  for 5,600 files, 15.7M comparisons) unless you're into six figures of
  files, at which point run it per-subfolder instead of on the whole tree.
  On a real pile of ~5,600 messaging-app photos this found ~1,700
  duplicates — one in three files — that the filename-based pass would
  never have caught.
- **A single corrupted camera-clock date can wreck a whole date-range
  estimate.** When guessing an undated photo's era from its "siblings" in
  the same source zip (`recover_undated_photos.py`), a naive min/max over
  their dates gets dragged to whatever garbage date one mis-set camera
  clock produced (we saw a stray "2001" in an otherwise-2016 batch).
  Trim the extremes (2nd–98th percentile) before taking the range. Even
  trimmed, some zips (Apple's `iCloud Photos Part N of M.zip`) are just an
  arbitrary slice of the *entire* library, not a single session — their
  "siblings" span decades and any range from them is meaningless. Add a
  sanity cap on the range width itself (`--max-range-months`, default 48)
  and discard the estimate entirely if the range is wider than that,
  rather than reporting a multi-decade guess as if it meant something.

## Wiring it into Immich

1. Add a **read-only** bind mount in `docker-compose.yml` for the
   `immich-server` service:
   ```yaml
   volumes:
     - /mnt/photo-library/by-date:/mnt/by-date:ro
   ```
2. Keep Immich's own `UPLOAD_LOCATION` (where the mobile app's backups
   land) on a *different*, writable, native-filesystem path — don't point
   it at the same tree as your read-only archive, and don't put it on
   exFAT (see [Lessons learned](#lessons-learned)).
3. In Immich: **Administration → Libraries** (`/admin/library-management`
   in newer versions — the UI has moved around between releases, check
   both "Administration" and your account settings if you don't see it) →
   create an External Library → import path `/mnt/by-date` → Scan.
4. First scan of a large library generates thumbnails / runs face & object
   recognition for every asset — expect it to take hours, not minutes, and
   for exiftool/ffmpeg worker processes to peg the CPU for a while. That's
   normal.
5. If you later fix dates (`fix_mtime.py`, or manually), trigger a re-scan
   from the same Libraries page so Immich re-reads the updated metadata —
   it won't pick up filesystem changes automatically.

## Testing it yourself

`test-data/generate_dataset.py` builds a small synthetic dataset (a
handful of tiny JPGs, two fake export zips) covering every edge case this
README mentions — EXIF dates, filename-only dates in several formats,
a cross-source content duplicate, a same-timestamp conflict at two
resolutions, a Live Photo pair, and a real Google Photos album folder.
Good for a quick end-to-end smoke test after changing anything:

```bash
cd test-data && python3 generate_dataset.py && cd ../src
python3 inventory.py --root a=../test-data/source-a --root b=../test-data/source-b --output ../test-data/reports/inventory.csv
python3 extract.py --inventory ../test-data/reports/inventory.csv --dest ../test-data/dest --reports-dir ../test-data/reports
python3 organize.py --manifest ../test-data/reports/manifest.csv --dest ../test-data/dest --reports-dir ../test-data/reports
# ...and so on, same as the Quickstart above but pointed at test-data/.
```

## Reports directory

Everything is CSV, nothing is a database, so you can always inspect what
happened with a text editor or `csvkit`/`pandas`:

| File | Written by | What's in it |
|---|---|---|
| `inventory.csv` | `inventory.py` | every source zip + its hash |
| `manifest.csv` | `extract.py` | every extracted file: hash, source zip, destination |
| `duplicates.csv` | `extract.py` | exact-hash duplicates that were *not* extracted (with a pointer to the copy that was kept) |
| `oversized.csv` | `extract.py` | anything over 4GB (exFAT's limit) that got skipped |
| `organized.csv` | `organize.py` | final path + which method supplied the date (`exif` / `filename` / `sem-data`) |
| `exif-cache.json` | `organize.py` | raw exiftool output, reused on re-runs |
| `live-photos.csv` | `live_photos.py` | HEIC/MOV pairs and what was done to keep them together |
| `near-duplicates.csv` | `dedupe_visual.py` | every compared pair + perceptual hash distance |
| *(review-dir tree)* | `dedupe_visual_global.py` | no CSV — the smaller file(s) of each duplicate cluster are moved into `<review-dir>`, mirroring their original relative path |
| *(review-dir tree)* | `recover_undated_photos.py` | no CSV — recovered files are moved into `<review-dir>/com-localizacao`, `por-epoca-aproximada/<year>`, or `sem-pista` |
| `conflicts.csv` | `resolve_conflicts.py` | same-timestamp groups and which copy was kept |
| `albums.csv` | `albums.py` | photo → album membership, for later import |
| `photoslibrary-internals.csv` | `detect_photoslibrary_internals.py` | orphaned Live Photo videos from a raw `.photoslibrary` sync, and whether a timestamp match was found |

## License

MIT — do whatever you want with it.
