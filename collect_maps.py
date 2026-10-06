#!/usr/bin/env python3
"""Collecte Google Maps (Searlo /search/places) pour chaque agence du CSV.

- Requête : "<nom> <adresse>" (l'adresse contient déjà code postal + ville)
- Paramètres : limit=20, page=1, gl=fr, hl=fr
- Sortie : un fichier JSONL par agence (<siren>.jsonl), une ligne par résultat Maps
- Rotation de clés : chaque job (shard) démarre sur sa propre clé ; si elle est
  épuisée (402) ou invalide (401/403), il passe automatiquement à la suivante.
- Reprise : une agence dont le fichier existe déjà dans maps/ est ignorée.
  Un fichier vide = requête faite, aucun résultat.
"""
import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://api.searlo.tech/api/v1/search/places"
MAX_ATTEMPTS = 6


class AllKeysExhausted(Exception):
    pass


class PermanentError(Exception):
    pass


class KeyPool:
    """Rotation de clés thread-safe : on reste sur une clé jusqu'à ce qu'elle meure."""

    def __init__(self, keys, start):
        self.keys = keys
        self.dead = set()
        self.cur = start % len(keys)
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if len(self.dead) >= len(self.keys):
                raise AllKeysExhausted()
            while self.cur in self.dead:
                self.cur = (self.cur + 1) % len(self.keys)
            return self.cur

    def kill(self, idx, reason):
        with self.lock:
            if idx not in self.dead:
                self.dead.add(idx)
                print(f"[clé {idx + 1}] retirée de la rotation : {reason} "
                      f"({len(self.keys) - len(self.dead)} restantes)", flush=True)


def load_keys():
    raw = os.environ.get("SEARLO_KEYS", "")
    keys = [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
    if not keys:
        for i in range(1, 14):
            k = os.environ.get(f"SEARLO_KEY_{i}", "").strip()
            if k:
                keys.append(k)
    return keys


def fetch_places(session, pool, query):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        idx = pool.get()
        try:
            r = session.get(
                API_URL,
                params={"q": query, "limit": 20, "page": 1, "gl": "fr", "hl": "fr"},
                headers={"x-api-key": pool.keys[idx]},
                timeout=60,
            )
        except requests.RequestException as e:
            time.sleep(min(2 ** attempt, 30))
            if attempt == MAX_ATTEMPTS:
                raise PermanentError(f"réseau: {e}")
            continue

        s = r.status_code
        if s == 200:
            remaining = r.headers.get("X-Credits-Remaining")
            try:
                if remaining is not None and float(remaining) <= 0:
                    pool.kill(idx, "crédits épuisés")
            except ValueError:
                pass
            return r.json().get("places", []) or []
        if s == 402:
            pool.kill(idx, "crédits insuffisants (402)")
            continue
        if s in (401, 403):
            pool.kill(idx, f"clé refusée ({s})")
            continue
        if s == 429:
            try:
                wait = float(r.headers.get("Retry-After", 5))
            except ValueError:
                wait = 5
            time.sleep(min(max(wait, 1), 60) + 0.5)
            continue
        if s >= 500:
            time.sleep(min(2 ** attempt, 30))
            continue
        raise PermanentError(f"HTTP {s}: {r.text[:200]}")
    raise PermanentError("trop de tentatives")


def write_atomic(path: Path, lines):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="agences_tier_A_actives.csv")
    ap.add_argument("--maps-dir", default="maps", help="dossier des résultats déjà collectés (reprise)")
    ap.add_argument("--out-dir", default=None, help="dossier d'écriture (défaut: maps-dir)")
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SHARD", 0)))
    ap.add_argument("--num-shards", type=int, default=int(os.environ.get("NUM_SHARDS", 1)))
    ap.add_argument("--limit", type=int, default=0, help="max d'agences à traiter (0 = toutes)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-minutes", type=float, default=330)
    args = ap.parse_args()

    keys = load_keys()
    if not keys:
        sys.exit("Aucune clé : définir SEARLO_KEY_1..13 ou SEARLO_KEYS")
    maps_dir = Path(args.maps_dir)
    out_dir = Path(args.out_dir or args.maps_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    todo = []
    for i, row in enumerate(rows):
        if i % args.num_shards != args.shard:
            continue
        siren = row["siren"].strip().zfill(9)
        if (maps_dir / f"{siren}.jsonl").exists() or (out_dir / f"{siren}.jsonl").exists():
            continue
        todo.append((siren, row))
    if args.limit:
        todo = todo[: args.limit]

    print(f"Shard {args.shard + 1}/{args.num_shards} : {len(todo)} agences à traiter, "
          f"{len(keys)} clés, clé de départ n°{args.shard % len(keys) + 1}", flush=True)

    pool = KeyPool(keys, args.shard)
    deadline = time.time() + args.max_minutes * 60
    stats = {"ok": 0, "vides": 0, "erreurs": 0, "non_traitees": 0, "lieux": 0}
    lock = threading.Lock()
    errors_path = out_dir / f"_errors_shard{args.shard}.jsonl"
    local = threading.local()

    def work(item):
        siren, row = item
        if time.time() > deadline:
            with lock:
                stats["non_traitees"] += 1
            return
        if not hasattr(local, "s"):
            local.s = requests.Session()
        query = f"{row['nom'].strip()} {row['adresse'].strip()}"
        try:
            places = fetch_places(local.s, pool, query)
        except AllKeysExhausted:
            with lock:
                stats["non_traitees"] += 1
            return
        except PermanentError as e:
            with lock:
                stats["erreurs"] += 1
                with open(errors_path, "a", encoding="utf-8") as ef:
                    ef.write(json.dumps({"siren": siren, "requete": query, "erreur": str(e)},
                                        ensure_ascii=False) + "\n")
            return
        fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines = [
            {**p, "_siren": siren, "_siret_siege": row["siret_siege"].strip(),
             "_requete": query, "_page": 1, "_fetched_at": fetched}
            for p in places
        ]
        write_atomic(out_dir / f"{siren}.jsonl", lines)
        with lock:
            stats["ok"] += 1
            stats["lieux"] += len(lines)
            if not lines:
                stats["vides"] += 1
            if stats["ok"] % 100 == 0:
                print(f"  {stats['ok']} agences faites, {stats['erreurs']} erreurs", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))

    summary = (f"Shard {args.shard + 1}: {stats['ok']} agences collectées "
               f"({stats['lieux']} lieux, {stats['vides']} sans résultat), "
               f"{stats['erreurs']} erreurs, {stats['non_traitees']} non traitées, "
               f"clés retirées : {len(pool.dead)}")
    print(summary, flush=True)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(summary + "\n\n")


if __name__ == "__main__":
    main()
