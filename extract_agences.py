#!/usr/bin/env python3
"""Extrait les agences immobilières UNIQUES avec site web depuis maps/*.jsonl.

Filtre : `category` exactement « Agence immobilière » (voir AGENCE_CATEGORIES dans
stats_maps.py : les deux scripts restent cohérents) + site web renseigné.
Dédoublonnage par placeId.

Sorties (par défaut) : agences_uniques_site_web.csv  (Excel, UTF-8 avec BOM)
                       agences_uniques_site_web.jsonl (tous les champs Maps)

Usage : python extract_agences.py [dossier=maps] [--tous] [--out agences_uniques_site_web]
  --tous : garde aussi les agences sans site web
"""
import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path

from stats_maps import is_agence, load_reseaux, networks_in, place_cp, place_key

COLUMNS = ["placeId", "titre", "categorie", "adresse", "code_postal", "ville", "telephone",
           "site_web", "domaine", "note", "nb_avis", "latitude", "longitude", "reseau",
           "nb_recherches"]


def split_address(p):
    """Adresse sans le titre Maps en tête, puis (rue, code postal, ville)."""
    addr = p.get("address") or ""
    title = p.get("title") or ""
    if title and addr.startswith(title):
        addr = addr[len(title):].lstrip(", ")
    cp = place_cp(addr)
    ville = addr.rsplit(cp, 1)[-1].strip(" ,") if cp else ""
    return addr, cp or "", ville


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default="maps")
    ap.add_argument("--out", default="agences_uniques_site_web")
    ap.add_argument("--reseaux", default="reseaux.txt")
    ap.add_argument("--tous", action="store_true", help="inclure les agences sans site web")
    args = ap.parse_args()

    files = sorted(p for p in Path(args.dir).glob("*.jsonl") if not p.name.startswith("_"))
    best = {}                 # placeId -> fiche (celle qui a un site web si possible)
    nb_recherches = Counter()
    lignes = illisibles = 0

    for fp in files:
        seen = set()
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                except json.JSONDecodeError:
                    illisibles += 1
                    continue
                lignes += 1
                if not is_agence(p):
                    continue
                k = place_key(p)
                if k not in seen:
                    seen.add(k)
                    nb_recherches[k] += 1
                has_site = bool((p.get("website") or "").strip())
                old = best.get(k)
                if old is None or (has_site and not (old.get("website") or "").strip()):
                    best[k] = p

    cmap, rmap = load_reseaux(args.reseaux)
    kept = {k: p for k, p in best.items()
            if args.tous or (p.get("website") or "").strip()}

    out_csv = Path(args.out + ".csv")
    out_jsonl = Path(args.out + ".jsonl")
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as fc, \
            open(out_jsonl, "w", encoding="utf-8") as fj:
        w = csv.writer(fc)
        w.writerow(COLUMNS)
        for k, p in sorted(kept.items(), key=lambda kv: (kv[1].get("address") or "")):
            addr, cp, ville = split_address(p)
            reseau = " | ".join(sorted(networks_in(p.get("title") or "", cmap, rmap)))
            w.writerow([
                p.get("placeId") or "", p.get("title") or "", p.get("category") or "", addr, cp,
                ville, p.get("phoneNumber") or "", p.get("website") or "", p.get("domain") or "",
                p.get("rating") if p.get("rating") is not None else "",
                p.get("ratingCount") if p.get("ratingCount") is not None else "",
                p.get("latitude") or "", p.get("longitude") or "", reseau, nb_recherches[k],
            ])
            fj.write(json.dumps({**{c: p.get(c) for c in p if not c.startswith("_")},
                                 "nb_recherches": nb_recherches[k]}, ensure_ascii=False) + "\n")

    avec_site = sum(1 for p in best.values() if (p.get("website") or "").strip())
    msg = (f"{len(kept)} agences exportées ({'toutes' if args.tous else 'avec site web'}) "
           f"· agences uniques : {len(best)} dont {avec_site} avec site web "
           f"· {lignes} lignes lues"
           + (f" · {illisibles} illisibles" if illisibles else ""))
    print(msg)
    print(f"-> {out_csv}  {out_jsonl}")
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(f"## Export agences\n\n- {msg}\n")


if __name__ == "__main__":
    main()
