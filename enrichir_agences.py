#!/usr/bin/env python3
"""Enrichit agences.csv à partir des JSONL Maps (dossier maps/).

Correspondance : nom normalisé (titre du CSV == title du JSON).
Récupère uniquement : adresse, code_postal, ville, horaires.
- adresse / code_postal / ville : remplis seulement s'ils sont vides (--overwrite pour forcer)
- horaires : toujours remplis (colonne créée si absente)

Usage :
  python enrichir_agences.py --csv agences.csv --maps-dir maps --out agences_enrichies.csv
"""
import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
ABREV = {j: j[:3] for j in JOURS}
CP_RE = re.compile(r"\b(\d{5})\s+([^,]+?)\s*(?:,\s*France)?\s*$")


def norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s)


def parse_address(address, title):
    """'Nom, 5 Bd de Brosses, 21000 Dijon' -> ('5 Bd de Brosses', '21000', 'Dijon')"""
    if not address:
        return "", "", ""
    addr = address.strip()
    if title and addr.lower().startswith(title.strip().lower() + ","):
        addr = addr[len(title.strip()) + 1:].strip()
    m = CP_RE.search(addr)
    if not m:
        return addr, "", ""
    cp, ville = m.group(1), m.group(2).strip()
    rue = addr[: m.start()].strip().rstrip(",").strip()
    # Certaines adresses gardent "Nom, Complément, rue" : on conserve tel quel, sans le nom.
    return rue, cp, ville


def format_hours(hours):
    """Liste [{'day','hours'}] -> 'lun 09:00–12:00, 14:00–18:00 | mar ... | dim Fermé'"""
    if not hours:
        return ""
    par_jour = {h.get("day", "").lower(): h.get("hours") or [] for h in hours}
    parts = []
    for j in JOURS:
        if j in par_jour:
            parts.append(f"{ABREV[j]} {', '.join(par_jour[j]) or 'Fermé'}")
    return " | ".join(parts)


def load_maps(maps_dir):
    """norm(title) -> liste de lieux (le plus récent d'abord)."""
    index = {}
    for path in sorted(Path(maps_dir).glob("*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = norm(p.get("title"))
                if key:
                    index.setdefault(key, []).append(p)
    for lst in index.values():
        lst.sort(key=lambda p: p.get("_fetched_at") or "", reverse=True)
    return index


def pick(cands, row):
    """Plusieurs lieux de même nom : on départage par code postal, puis on prend le plus récent."""
    if len(cands) == 1:
        return cands[0]
    cp = (row.get("code_postal") or "").strip()
    if cp:
        same = [p for p in cands if cp in (p.get("address") or "")]
        if same:
            return same[0]
    return cands[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="agences.csv")
    ap.add_argument("--maps-dir", default="maps")
    ap.add_argument("--out", default="agences_enrichies.csv")
    ap.add_argument("--overwrite", action="store_true",
                    help="écrase adresse/code_postal/ville même s'ils existent")
    args = ap.parse_args()

    index = load_maps(args.maps_dir)
    print(f"{sum(len(v) for v in index.values())} lieux Maps indexés ({len(index)} noms distincts)")

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames)
        rows = list(reader)
    for col in ("adresse", "code_postal", "ville", "horaires"):
        if col not in fields:
            fields.append(col)

    stats = {"matchs": 0, "adresse": 0, "cp": 0, "ville": 0, "horaires": 0}
    for row in rows:
        cands = index.get(norm(row.get("titre")))
        row.setdefault("horaires", "")
        if not cands:
            continue
        p = pick(cands, row)
        stats["matchs"] += 1
        rue, cp, ville = parse_address(p.get("address"), p.get("title"))
        for col, val, key in (("adresse", rue, "adresse"), ("code_postal", cp, "cp"),
                              ("ville", ville, "ville")):
            if val and (args.overwrite or not (row.get(col) or "").strip()):
                row[col] = val
                stats[key] += 1
        h = format_hours(p.get("hours"))
        if h:
            row["horaires"] = h
            stats["horaires"] += 1

    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} agences | {stats['matchs']} trouvées par nom | "
          f"adresse +{stats['adresse']} | code postal +{stats['cp']} | "
          f"ville +{stats['ville']} | horaires +{stats['horaires']}")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
