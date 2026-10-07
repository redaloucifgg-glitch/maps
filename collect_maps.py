#!/usr/bin/env python3
"""Compteurs sur les résultats collectés dans maps/*.jsonl.

Répond à 5 questions :
  1. combien de résultats Maps sont des agences immobilières
  2. combien de doublons
  3. combien de fiches correspondent vraiment à une agence du CSV
  4. combien d'agences uniques (sans doublons)
  5. combien d'agences uniques ont un site web

Une fiche est une "agence immobilière" si `category` (catégorie principale) ou
une des `categories` (liste complète) contient un mot de AGENCE_KEYWORDS.
Une agence est "du CSV" si, dans le même code postal, son adresse OU son nom
correspond à celui d'une agence du CSV (voir match_*).

Usage : python stats_maps.py [dossier=maps] [--csv agences_tier_A_actives.csv]
Si $GITHUB_STEP_SUMMARY existe, le rapport y est aussi écrit.
"""
import argparse
import csv
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

# --- À adapter si besoin : mots (sans accents, minuscules) qui désignent une agence
AGENCE_KEYWORDS = (
    "agence immobili",
    "agent immobilier",
    "agence de location immobili",
    "agence de location d'appartements",
    "agence de location de maisons",
)

GENERIC_NAME = {
    "immobilier", "immobiliere", "immobiliers", "immobilieres", "immo", "agence", "agences",
    "sarl", "sas", "sasu", "eurl", "sci", "sa", "societe", "cabinet", "groupe", "gestion",
    "transaction", "transactions", "conseil", "et", "de", "la", "le", "les", "des", "du",
    "en", "au", "aux", "sur", "sous", "the",
}
STREET_TYPES = {
    "rue", "avenue", "ave", "av", "boulevard", "bd", "bld", "blvd", "place", "pl", "allee",
    "all", "chemin", "che", "impasse", "imp", "route", "rte", "cours", "quai", "square", "sq",
    "passage", "pass", "esplanade", "residence", "res", "zone", "zi", "za", "lotissement",
    "lot", "batiment", "bat", "galerie", "centre", "ccial", "commercial", "des", "les", "du",
    "de", "la", "le", "sur", "sous", "bis", "ter",
}
WORD = re.compile(r"[a-z0-9]+")
CP = re.compile(r"\b(\d{5})\b")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace("’", "'")


def words(s):
    return WORD.findall(norm(s).replace("&", " "))


def place_key(p):
    return (p.get("placeId") or p.get("cid") or p.get("featureId")
            or f"{p.get('title')}|{p.get('address')}")


def is_agence(p):
    cats = [p.get("category")] + list(p.get("categories") or [])
    return any(k in norm(c) for c in cats if c for k in AGENCE_KEYWORDS)


def place_cp(addr):
    found = CP.findall(addr or "")
    return found[-1] if found else None


def street_part(addr, title=None):
    """Partie 'numéro + rue' d'une adresse (sans titre Maps, sans code postal ni ville)."""
    a = addr or ""
    if title and a.startswith(title):
        a = a[len(title):].lstrip(", ")
    m = list(CP.finditer(a))
    if m:
        a = a[: m[-1].start()]
    return a


def addr_sig(addr, title=None):
    s = street_part(addr, title)
    nums = set(re.findall(r"\d+", s))
    street = {w for w in words(s) if w.isalpha() and len(w) >= 3 and w not in STREET_TYPES}
    return nums, street


def name_tokens(*texts):
    return {w for t in texts for w in words(t) if len(w) >= 2 and not w.isdigit()
            and w not in GENERIC_NAME}


def match_address(csv_sig, place_sig):
    (n1, s1), (n2, s2) = csv_sig, place_sig
    return bool(n1 & n2) and bool(s1 & s2)


def name_variants(nom, enseigne):
    """Nom du CSV découpé : nom principal, chaque alias entre parenthèses, enseigne."""
    segs = [s for s in re.split(r"[()]", nom or "") if s.strip()]
    if (enseigne or "").strip():
        segs.append(enseigne)
    return [t for t in (name_tokens(s) for s in segs) if t]


def name_score(variants, title, city):
    """Meilleure ressemblance (0 à 1) entre le titre Maps et un nom du CSV.
    Les mots de la ville sont ignorés (« Dijon » ne prouve rien)."""
    t = name_tokens(title) - city
    best = 0.0
    for v in variants:
        v = v - city
        common = v & t
        if v and common and any(len(w) >= 3 for w in common):
            best = max(best, len(common) / len(v | t))
    return best


TLD = re.compile(r"\.(com|fr)$")


def load_reseaux(path):
    """Lit reseaux.txt. Renvoie ({forme compacte: nom}, {motif brut: nom}).
    Forme compacte = mots collés, sans accents : « Meg Agence » = « megAgence »."""
    compact_map, raw_map = {}, {}
    p = Path(path)
    if not p.exists():
        return compact_map, raw_map
    for line in p.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        base = TLD.sub("", norm(name))
        compact = "".join(words(base))
        if len(compact) >= 3:
            compact_map.setdefault(compact, name)
        elif base.strip():
            raw_map.setdefault(base.strip(), name)   # ex. « 3%.com » -> « 3% »
    return compact_map, raw_map


def networks_in(text, compact_map, raw_map, max_words=5):
    """Réseaux dont le nom apparaît dans `text` (mots entiers, 1 à 5 mots collés)."""
    ws = words(text)
    found = set()
    for i in range(len(ws)):
        acc = ""
        for j in range(i, min(i + max_words, len(ws))):
            acc += ws[j]
            if acc in compact_map:
                found.add(compact_map[acc])
    if raw_map:
        t = norm(text)
        found.update(name for raw, name in raw_map.items() if raw in t)
    return found


def pct(a, b):
    return f"{100 * a / b:.0f} %" if b else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default="maps")
    ap.add_argument("--csv", default="agences_tier_A_actives.csv")
    ap.add_argument("--reseaux", default="reseaux.txt")
    args = ap.parse_args()

    files = sorted(p for p in Path(args.dir).glob("*.jsonl") if not p.name.startswith("_"))

    lignes = 0
    lignes_agence = 0
    vides = 0
    illisibles = 0
    places = {}              # clé de lieu -> infos utiles
    nb_fichiers = Counter()  # clé de lieu -> nb d'agences (fichiers) où il apparaît

    for fp in files:
        seen = set()
        n = 0
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
                n += 1
                lignes += 1
                k = place_key(p)
                ag = is_agence(p)
                lignes_agence += ag
                web = bool((p.get("website") or "").strip())
                if k not in places:
                    places[k] = {"title": p.get("title") or "", "address": p.get("address") or "",
                                 "web": web, "agence": ag, "category": p.get("category") or "?"}
                elif web:
                    places[k]["web"] = True
                if k not in seen:
                    seen.add(k)
                    nb_fichiers[k] += 1
        if n == 0:
            vides += 1

    agences = {k: v for k, v in places.items() if v["agence"]}
    ag_web = sum(1 for v in agences.values() if v["web"])
    ag_multi = sum(1 for k in agences if nb_fichiers[k] > 1)

    out = ["## Compteurs Maps", ""]
    out.append("### Vue d'ensemble")
    out.append(f"- Agences du CSV interrogées : **{len(files)}** ({vides} sans aucun résultat)")
    out.append(f"- Résultats Maps (lignes) : **{lignes}**")
    out.append(f"- Lieux uniques (tous types) : **{len(places)}**")
    if illisibles:
        out.append(f"- Lignes illisibles ignorées : {illisibles}")
    out.append("")
    out.append("### 1. Agences immobilières")
    out.append(f"- Résultats qui sont des agences immobilières : **{lignes_agence}** / {lignes} "
               f"({pct(lignes_agence, lignes)})")
    out.append(f"- Autres lieux (pas des agences, ex. siège social) : "
               f"{len(places) - len(agences)} lieux uniques")
    out.append("")
    out.append("### 2. Doublons")
    out.append(f"- Lignes en trop sur les agences : **{lignes_agence - len(agences)}** "
               f"({lignes_agence} lignes pour {len(agences)} agences)")
    out.append(f"- Agences qui apparaissent dans plusieurs recherches : **{ag_multi}**")
    out.append("")
    out.append("### 4. Agences uniques (sans doublons)")
    out.append(f"- **{len(agences)}** agences immobilières uniques")
    out.append("")
    out.append("### 5. Agences uniques avec site web")
    out.append(f"- **{ag_web}** / {len(agences)} ({pct(ag_web, len(agences))})")

    # --- 3. correspondance avec le CSV
    rows = []
    if Path(args.csv).exists():
        with open(args.csv, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        by_cp = defaultdict(list)
        for r in rows:
            cp = (r.get("code_postal") or "").strip().zfill(5) or place_cp(r["adresse"])
            by_cp[cp].append((
                r["siren"].strip().zfill(9),
                addr_sig(r["adresse"]),
                name_variants(r["nom"], r.get("enseigne") or ""),
                set(words(r.get("ville") or "")),
            ))

        matched_places = 0
        matched_web = 0
        by_type = Counter()
        sirens = set()
        for v in agences.values():
            cands = by_cp.get(place_cp(v["address"]), [])
            if not cands:
                continue
            psig = addr_sig(v["address"], v["title"])
            pcity = set(words(v["address"].rsplit(place_cp(v["address"]) or "\0", 1)[-1]))
            best = None   # (rang, score, siren, adresse?, nom?) : 1 seule agence du CSV par fiche
            for siren, csig, variants, ccity in cands:
                a = match_address(csig, psig)
                sc = name_score(variants, v["title"], pcity | ccity)
                nm = sc >= 0.5
                if a or nm:
                    cand = (2 * a + nm, sc, siren, a, nm)
                    if best is None or cand[:2] > best[:2]:
                        best = cand
            if best:
                _, _, siren, hit_a, hit_n = best
                sirens.add(siren)
                matched_places += 1
                matched_web += v["web"]
                by_type["adresse + nom" if hit_a and hit_n else "adresse seule" if hit_a else "nom seul"] += 1

        out.append("")
        out.append("### 3. Fiches qui correspondent au CSV")
        out.append(f"- Fiches (agences uniques) qui correspondent à une agence du CSV : "
                   f"**{matched_places}** / {len(agences)} ({pct(matched_places, len(agences))})")
        out.append(f"  - dont avec site web : {matched_web}")
        out.append(f"  - par adresse + nom : {by_type['adresse + nom']} · "
                   f"adresse seule : {by_type['adresse seule']} · nom seul : {by_type['nom seul']}")
        out.append(f"- Agences du CSV retrouvées sur Maps : **{len(sirens)}** / {len(rows)} "
                   f"({pct(len(sirens), len(rows))})")
        out.append(f"- Fiches qui ne sont pas dans le CSV (nouvelles agences) : "
                   f"**{len(agences) - matched_places}**")

    # --- 6. réseaux (via les noms)
    cmap, rmap = load_reseaux(args.reseaux)
    if cmap or rmap:
        all_names = list(cmap.values()) + list(rmap.values())
        maps_c, csv_c = Counter(), Counter()
        n_maps = n_csv = 0
        for v in agences.values():
            hit = networks_in(v["title"], cmap, rmap)
            n_maps += bool(hit)
            maps_c.update(hit)
        for r in rows:
            hit = networks_in(f'{r["nom"]} {r.get("enseigne") or ""}', cmap, rmap)
            n_csv += bool(hit)
            csv_c.update(hit)
        out.append("")
        out.append("### 6. Réseaux (liste reseaux.txt, via les noms)")
        out.append(f"- Réseaux dans la liste : **{len(all_names)}**")
        out.append(f"- Agences Maps uniques dans un réseau : **{n_maps}** / {len(agences)} "
                   f"({pct(n_maps, len(agences))}) · indépendantes : {len(agences) - n_maps}")
        out.append(f"  - réseaux retrouvés sur Maps : **{len(maps_c)}** / {len(all_names)}")
        if rows:
            out.append(f"- Agences du CSV dans un réseau (nom ou enseigne) : **{n_csv}** / {len(rows)} "
                       f"({pct(n_csv, len(rows))}) · réseaux retrouvés : {len(csv_c)}")
        out.append("- Top 15 réseaux (fiches Maps · agences CSV) :")
        for name, c in maps_c.most_common(15):
            out.append(f"  - {name} : {c} · {csv_c.get(name, 0)}")
        absents = [n for n in all_names if n not in maps_c]
        if absents:
            out.append(f"- Réseaux sans aucune fiche Maps ({len(absents)}) : "
                       + ", ".join(absents[:40]) + (" …" if len(absents) > 40 else ""))

    cats = Counter(v["category"] for v in places.values()).most_common(8)
    out.append("")
    out.append("### Catégories principales (lieux uniques)")
    for c, n in cats:
        out.append(f"- {c} : {n}")

    report = "\n".join(out)
    print(report)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
              
