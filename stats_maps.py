#!/usr/bin/env python3
"""Compteurs sur les résultats collectés dans maps/*.jsonl :
agences, résultats Maps, doublons, sites web.

Usage : python stats_maps.py [dossier=maps] [--csv agences_tier_A_actives.csv]
Si $GITHUB_STEP_SUMMARY existe, le rapport y est aussi écrit.
"""
import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


def place_key(p):
    return (p.get("placeId") or p.get("cid") or p.get("featureId")
            or f"{p.get('title')}|{p.get('address')}")


def domain_of(p):
    d = (p.get("domain") or "").strip().lower()
    if not d and p.get("website"):
        d = urlparse(p["website"]).netloc.lower()
    return d.removeprefix("www.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default="maps")
    ap.add_argument("--csv", default="agences_tier_A_actives.csv")
    args = ap.parse_args()

    files = sorted(p for p in Path(args.dir).glob("*.jsonl") if not p.name.startswith("_"))
    agences_vides = 0
    lignes = 0
    lignes_web = 0
    key_files = {}          # clé de lieu -> nb d'agences (fichiers) où il apparaît
    key_has_web = {}        # clé de lieu -> a un site web
    key_domain = {}
    top1_total = 0
    top1_web = 0
    dup_intra = 0           # même lieu 2 fois dans un même fichier

    for fp in files:
        seen = set()
        n = 0
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                p = json.loads(line)
                n += 1
                lignes += 1
                k = place_key(p)
                web = bool((p.get("website") or "").strip())
                lignes_web += web
                if k in seen:
                    dup_intra += 1
                else:
                    seen.add(k)
                    key_files[k] = key_files.get(k, 0) + 1
                key_has_web[k] = key_has_web.get(k, False) or web
                if web:
                    key_domain[k] = domain_of(p)
                if n == 1:
                    top1_total += 1
                    top1_web += web
        if n == 0:
            agences_vides += 1

    uniques = len(key_files)
    partages = [k for k, c in key_files.items() if c > 1]
    lignes_en_trop = sum(c - 1 for c in key_files.values() if c > 1)
    uniq_web = sum(1 for v in key_has_web.values() if v)
    domaines = Counter(d for d in key_domain.values() if d)
    domaines_multi = sum(1 for c in domaines.values() if c > 1)

    out = ["## Compteurs Maps", ""]
    out.append(f"- Agences interrogées (fichiers) : **{len(files)}**")
    out.append(f"  - avec au moins 1 résultat : {len(files) - agences_vides}")
    out.append(f"  - sans résultat : {agences_vides}")
    out.append(f"- Résultats Maps (lignes) : **{lignes}**")
    out.append(f"- Lieux uniques (par placeId) : **{uniques}**")
    out.append("")
    out.append("### Doublons")
    out.append(f"- Lieux présents dans plusieurs agences : **{len(partages)}** "
               f"({lignes_en_trop} lignes en trop)")
    out.append(f"- Doublons à l'intérieur d'un même fichier : {dup_intra}")
    out.append("")
    out.append("### Sites web")
    out.append(f"- Résultats avec site web : **{lignes_web}** / {lignes}")
    out.append(f"- Lieux uniques avec site web : **{uniq_web}** / {uniques}")
    out.append(f"- Domaines uniques : **{len(domaines)}** "
               f"(dont {domaines_multi} partagés par plusieurs lieux)")
    out.append(f"- 1er résultat de chaque agence avec site web : "
               f"**{top1_web}** / {top1_total}")

    if Path(args.csv).exists():
        with open(args.csv, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        sirens = Counter(r["siren"].strip().zfill(9) for r in rows)
        nom_adr = Counter((r["nom"].strip().upper(), r["adresse"].strip().upper()) for r in rows)
        out.append("")
        out.append("### CSV source")
        out.append(f"- Agences dans le CSV : **{len(rows)}** "
                   f"({len(rows) - len(files)} restantes à collecter)")
        out.append(f"- SIREN en double : {sum(1 for c in sirens.values() if c > 1)}")
        out.append(f"- Même nom + même adresse : {sum(1 for c in nom_adr.values() if c > 1)}")

    report = "\n".join(out)
    print(report)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
