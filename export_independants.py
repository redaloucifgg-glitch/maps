#!/usr/bin/env python3
"""Exporte les agences immobilières INDÉPENDANTES (hors réseaux de reseaux.txt)
qui ont un SITE WEB.

Mêmes définitions que stats_maps.py, donc mêmes chiffres :
  - agence = lieu unique (placeId) dont une catégorie est une agence immobilière
  - indépendante = aucun réseau de reseaux.txt reconnu dans le nom de la fiche
  - seules les fiches avec un site web sont exportées

Écrit dans exports/ :
  independants.csv        les indépendantes avec site web (colonne `exact` =
                          catégorie exactement "Agence immobilière", rien d'autre)
  independants_exact.csv  seulement celles dont category == "Agence immobilière"
                          ET categories == ["Agence immobilière"]

Usage : python export_independants.py [--dir maps] [--out exports] [--reseaux reseaux.txt]
"""
import argparse
import csv
import json
import os
from pathlib import Path

from stats_maps import is_agence, load_reseaux, networks_in, place_cp, place_key

EXACT = "Agence immobilière"
COLONNES = ["placeId", "titre", "adresse", "code_postal", "telephone", "site_web", "domaine",
            "note", "nb_avis", "categorie", "categories", "exact", "latitude", "longitude",
            "knowledgeGraphId", "nb_recherches"]


def is_exact(p):
    return p.get("category") == EXACT and list(p.get("categories") or []) == [EXACT]


def to_row(p):
    return {
        "placeId": p.get("placeId") or "",
        "titre": p.get("title") or "",
        "adresse": p.get("address") or "",
        "code_postal": place_cp(p.get("address")) or "",
        "telephone": p.get("phoneNumber") or "",
        "site_web": p.get("website") or "",
        "domaine": p.get("domain") or "",
        "note": p.get("rating") if p.get("rating") is not None else "",
        "nb_avis": p.get("ratingCount") if p.get("ratingCount") is not None else "",
        "categorie": p.get("category") or "",
        "categories": " | ".join(p.get("categories") or []),
        "exact": is_exact(p),
        "latitude": p.get("latitude") if p.get("latitude") is not None else "",
        "longitude": p.get("longitude") if p.get("longitude") is not None else "",
        "knowledgeGraphId": p.get("knowledgeGraphId") or "",
    }


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:   # utf-8-sig : accents OK dans Excel
        w = csv.DictWriter(f, fieldnames=COLONNES)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="maps")
    ap.add_argument("--out", default="exports")
    ap.add_argument("--reseaux", default="reseaux.txt")
    args = ap.parse_args()

    cmap, rmap = load_reseaux(args.reseaux)
    if not (cmap or rmap):
        raise SystemExit(f"Liste de réseaux introuvable ou vide : {args.reseaux}")

    agences = {}   # clé de lieu -> ligne d'export
    for fp in sorted(p for p in Path(args.dir).glob("*.jsonl") if not p.name.startswith("_")):
        seen = set()
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    p = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not is_agence(p):
                    continue
                k = place_key(p)
                if k not in agences:
                    agences[k] = {**to_row(p), "nb_recherches": 0}
                elif not agences[k]["site_web"] and (p.get("website") or "").strip():
                    nb = agences[k]["nb_recherches"]
                    agences[k] = {**to_row(p), "nb_recherches": nb}
                if k not in seen:
                    seen.add(k)
                    agences[k]["nb_recherches"] += 1

    toutes_independantes = [r for r in agences.values()
                            if not networks_in(r["titre"], cmap, rmap)]
    independants = [r for r in toutes_independantes if r["site_web"]]   # uniquement avec site web
    independants.sort(key=lambda r: (r["code_postal"], r["titre"].lower()))
    exacts = [r for r in independants if r["exact"]]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "independants.csv", independants)
    write_csv(out / "independants_exact.csv", exacts)

    summary = "\n".join([
        "## Export indépendants (avec site web uniquement)",
        f"- Agences immobilières uniques : {len(agences)}",
        f"- Indépendantes (hors réseaux) : {len(toutes_independantes)}",
        f"- Indépendantes avec site web (exportées) : **{len(independants)}**",
        f"- Parmi elles, catégorie exactement « {EXACT} » : **{len(exacts)}**",
    ])
    print(summary)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(summary + "\n")


if __name__ == "__main__":
    main()
