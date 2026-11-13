"""Capture genuine pre-tip v0.5 research forecasts and grade later.

Writes only data/v05_shadow_ledger.jsonl and research report.
The champion ledger, model and live site remain unchanged.
"""
import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from cbb import ACTIVE_LIVE_VERSION, dt, eligible, iso, predict, read_csv, utcnow
from team_directory import load_directory
from v05_transition_challenger import (
    VARIANTS, fit_variant, final_game, frozen_v04, metrics,
    model_observation,
)

NAME_PREFIX = "CBB_V05_SHADOW_"
MIN_PROSPECTIVE = 150


def model_name(name):
    if name not in VARIANTS:
        raise ValueError("Unrecognized challenger")
    return NAME_PREFIX + name.upper()


def validated_candidate(p, g, now):
    try:
        return (
            p.get("model") in {model_name(n) for n in VARIANTS}
            and p.get("game_id") == str(g["game_id"])
            and str(p.get("home_id")) == str(g["home_id"])
            and str(p.get("away_id")) == str(g["away_id"])
            and p.get("tip_utc") == g["tip_utc"]
            and dt(p["captured_utc"]) < dt(g["tip_utc"])
            and dt(g["tip_utc"]) - dt(p["captured_utc"]) <= timedelta(hours=72)
            and dt(p["captured_utc"]) <= now
            and abs(float(p["pred_margin"]) -
                    (float(p["pred_home"]) - float(p["pred_away"]))) <= 0.02
            and abs(float(p["pred_total"]) -
                    (float(p["pred_home"]) + float(p["pred_away"]))) <= 0.02
            and dt(p["training_latest_tip"]) < dt(p["captured_utc"])
        )
    except (TypeError, ValueError, KeyError, OverflowError):
        return False


def freeze(games, directory, ledger, now=None, year=None):
    now = now or utcnow()
    year = year or now.year + int(now.month >= 7)
    verified = {str(v) for v in directory}
    if not verified:
        return {"status": "PASS_NO_VERIFIED_D1_DIRECTORY", "captured": 0}
    targets = [g for g in games if int(g["season"]) == year
               and str(g.get("score_conflict", "0")) not in ("1", "True", "true")
               and str(g.get("identity_conflict", "0")) not in ("1", "True", "true")
               and str(g["home_id"]) in verified and str(g["away_id"]) in verified
               and now < dt(g["tip_utc"]) <= now + timedelta(hours=72)]
    if not targets:
        return {"status": "NO_FUTURE_D1_GAMES", "captured": 0}
    path = Path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                seen.add((str(rec.get("game_id")), rec.get("model"),
                          rec.get("captured_utc")))
    captured = []
    for name in VARIANTS:
        try:
            fitted = fit_variant(games, now, year, verified, name,
                                 retrospective=False)
        except ValueError:
            continue
        for g in targets:
            rec = predict(fitted, g)
            if rec is None:
                continue
            rec.update({
                "model": model_name(name), "variant": name,
                "captured_utc": iso(now), "research_only": True,
                "champion_version": ACTIVE_LIVE_VERSION,
                "model_alpha": fitted["ridge_alpha"],
                "prior_season_weight_per_game": fitted["previous_season_weight"],
                "verified_d1_training": True,
                "market_edge_calibrated": False,
            })
            key = (str(g["game_id"]), rec["model"], rec["captured_utc"])
            if key not in seen and validated_candidate(rec, g, now):
                captured.append(rec)
                seen.add(key)
    if captured:
        with path.open("a", encoding="utf-8") as f:
            for rec in captured:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
    return {"status": "FROZEN_PRETIP_RESEARCH_ONLY",
            "captured": len(captured),
            "games": len({r["game_id"] for r in captured}),
            "model_versions": sorted({r["model"] for r in captured})}


def grade(games, champion_ledger, challenger_ledger, verified,
          now=None, year=None):
    now = now or utcnow()
    year = year or now.year + int(now.month >= 7)
    verified = {str(v) for v in verified}
    champion = frozen_v04(games, champion_ledger, now, verified, year)
    by_id = {str(g["game_id"]): g for g in games}
    path = Path(challenger_ledger)
    last = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            p = json.loads(line)
            gid = str(p.get("game_id"))
            g = by_id.get(gid)
            if g is None or gid not in champion or not final_game(g, year, verified):
                continue
            if not eligible(g, now, retrospective=False) or not validated_candidate(p, g, now):
                continue
            key = gid, p["model"]
            if key not in last or last[key]["captured_utc"] < p["captured_utc"]:
                last[key] = p
    result = {}
    for name in VARIANTS:
        version = model_name(name)
        base, candidate = [], []
        for (gid, observed_version), p in last.items():
            if observed_version != version:
                continue
            g = by_id[gid]
            base.append(model_observation(g, champion[gid], 12.0))
            candidate.append(model_observation(g, p, 12.0))
        if len(base) != len(candidate):
            raise AssertionError("Unequal prospective comparison denominators")
        result[name] = {"v04": metrics(base), "v05": metrics(candidate)}
    n = min(result[name]["v05"]["n"] for name in VARIANTS)
    return {
        "status": "PROSPECTIVE_RESEARCH_ONLY", "as_of_utc": iso(now),
        "genuinely_frozen_v05_games_graded": n,
        "genuinely_frozen_v05_games_graded_available": n > 0,
        "minimum_paired_games_each_variant": MIN_PROSPECTIVE,
        "paired_games_each_variant_min": n,
        "promotion_gate": (
            "READY_FOR_INDEPENDENT_REVIEW" if n >= MIN_PROSPECTIVE
            else "INSUFFICIENT_PROSPECTIVE_HOLDOUT"),
        "active_model_unchanged": True, "variants": result,
        "warning": (
            "No automatic promotion or verified sportsbook edge. "
            "Compare across multiple independent weeks; avoid selecting a "
            "winner solely from the same prospective test period."),
    }


def run(root="data", now=None):
    root = Path(root)
    now = now or utcnow()
    try:
        directory = load_directory(root / "team_directory.json")
    except (OSError, ValueError):
        directory = {}
    games = read_csv(root / "games.csv")
    capture = freeze(games, directory, root / "v05_shadow_ledger.jsonl", now)
    graded = grade(games, root / "forecast_ledger.jsonl",
                   root / "v05_shadow_ledger.jsonl", directory, now)
    target = root / "reports" / "v05_shadow_prospective.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(graded, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return {"freeze": capture, "grade": graded}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data")
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2))
