"""Research-only v0.5 season-transition challengers against frozen v0.4.

Three predeclared variants. December/January 2025-26 selects a candidate,
February 2026 is a retrospective holdout, and actually frozen 2026-27 v0.4
pre-tip games are a separate source-time-aware *counterfactual* comparison.
Never promote automatically; the v0.5 candidate must later freeze its own
genuine pre-tip predictions and beat v0.4 prospectively.
"""
import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scipy.stats import norm

from cbb import (ACTIVE_LIVE_VERSION, dt, eligible, fit, iso, predict,
                 read_csv, utcnow)
from team_directory import load_directory

VERSION = "CBB_SEASON_TRANSITION_CHALLENGER_V0_5_RESEARCH"
V04 = "v04"
VARIANTS = ("roster_fade", "lighter_carryover", "less_shrinkage")
MIN_VALIDATION = 200
MIN_HOLDOUT = 200
MIN_TRULY_FROZEN_COMPARISONS = 150
UTC = timezone.utc


def parameters(name, cutoff):
    """Predeclared parameter families, no results-dependent tuning."""
    if name == V04:
        return {"prior_weight": 0.30, "alpha": 1.5}
    if name == "roster_fade":
        # Carryover fades as the new season matures, but is not zeroed out.
        nov1 = datetime(cutoff.year if cutoff.month >= 7 else cutoff.year - 1,
                        11, 1, tzinfo=UTC)
        days = max(0, (cutoff - nov1).total_seconds() / 86400)
        return {"prior_weight": max(0.06, 0.30 * math.exp(-days / 65.0)),
                "alpha": 1.5}
    if name == "lighter_carryover":
        return {"prior_weight": 0.15, "alpha": 1.5}
    if name == "less_shrinkage":
        return {"prior_weight": 0.30, "alpha": 0.75}
    raise ValueError("Unknown CBB variant: " + str(name))


def fit_variant(games, cutoff, year, d1, name, retrospective):
    return fit(games, cutoff, test_season=year, retrospective=retrospective,
               verified_ids=d1, **parameters(name, cutoff))


def final_game(g, year, verified):
    return (int(g["season"]) == year
            and str(g.get("completed")) in ("1", "True", "true")
            and str(g.get("score_conflict", "0")) not in ("1", "True", "true")
            and str(g.get("identity_conflict", "0")) not in ("1", "True", "true")
            and g.get("home_score") not in (None, "")
            and g.get("away_score") not in (None, "")
            and str(g["home_id"]) in verified and str(g["away_id"]) in verified)


def model_observation(g, prediction, margin_sd):
    actual_margin = float(g["home_score"]) - float(g["away_score"])
    actual_total = float(g["home_score"]) + float(g["away_score"])
    m, t = float(prediction["pred_margin"]), float(prediction["pred_total"])
    p = float(norm.cdf(m / max(8.0, margin_sd)))
    return {"game_id": str(g["game_id"]), "tip_utc": g["tip_utc"],
            "pred_margin": m, "pred_total": t,
            "actual_margin": actual_margin, "actual_total": actual_total,
            "correct": int((m > 0) == (actual_margin > 0)),
            "brier": (p - int(actual_margin > 0)) ** 2,
            "current_games_min": min(int(prediction["home_current_games"]),
                                     int(prediction["away_current_games"])),
            "big_favorite": abs(m) >= 12}


def metrics(rows):
    if not rows:
        return {"n": 0, "su_accuracy": None, "margin_mae": None,
                "total_mae": None, "margin_bias": None, "brier": None}
    n = len(rows)
    return {"n": n,
            "su_accuracy": round(sum(r["correct"] for r in rows)/n, 4),
            "margin_mae": round(sum(abs(r["pred_margin"]-r["actual_margin"])
                                    for r in rows)/n, 3),
            "total_mae": round(sum(abs(r["pred_total"]-r["actual_total"])
                                   for r in rows)/n, 3),
            "margin_bias": round(sum(r["pred_margin"]-r["actual_margin"]
                                     for r in rows)/n, 3),
            "brier": round(sum(r["brier"] for r in rows)/n, 4)}


def paired_metrics(observations):
    if not observations:
        return {name: metrics([]) for name in (V04, *VARIANTS)}
    n = len(observations[V04])
    if any(len(observations[name]) != n for name in (V04, *VARIANTS)):
        raise AssertionError("Different denominators for model comparison")
    return {name: metrics(observations[name]) for name in (V04, *VARIANTS)}


def retrospective(games, verified, year=2026, start="2025-11-01",
                  end="2026-02-28"):
    """Historical final-score replay only; not an authenticated pre-tip capture."""
    if not verified:
        return {"status": "BLOCKED_NO_D1_DIRECTORY"}
    groups = defaultdict(list)
    for g in games:
        if final_game(g, year, verified) and start <= g["tip_utc"][:10] <= end:
            groups[g["tip_utc"][:10]].append(g)
    partitions = {
        "november": defaultdict(list),
        "december_january_selection": defaultdict(list),
        "february_retro_holdout": defaultdict(list),
    }
    skipped = 0
    for day in sorted(groups):
        cutoff = dt(day + "T00:00:00Z")
        models = {}
        for name in (V04, *VARIANTS):
            try:
                models[name] = fit_variant(
                    games, cutoff, year, verified, name, retrospective=True)
                if dt(models[name]["training_latest_tip"]) >= cutoff:
                    raise AssertionError("Historical replay leaked future scores")
            except ValueError:
                models[name] = None
        for g in groups[day]:
            if any(m is None for m in models.values()):
                skipped += 1
                continue
            predictions = {name: predict(model, g)
                           for name, model in models.items()}
            if any(p is None for p in predictions.values()):
                skipped += 1
                continue
            split = ("november" if day[:7] == f"{year-1}-11"
                     else "february_retro_holdout" if day[:7] == f"{year}-02"
                     else "december_january_selection")
            for name, pred in predictions.items():
                partitions[split][name].append(
                    model_observation(g, pred, models[name]["margin_sd"]))
    results = {}
    for split, data in partitions.items():
        # A subgroup is defined once by champion predictions, not by each
        # candidate's changing spread. Otherwise the denominators differ and
        # the favorite-performance comparison is biased.
        champion_big_favorite_ids = {
            x["game_id"] for x in data[V04] if x["big_favorite"]}
        results[split] = {
            "comparison": paired_metrics(data),
            "early_sample_under5": {
                name: metrics([x for x in data[name]
                               if x["current_games_min"] < 5])
                for name in (V04, *VARIANTS)},
            "v04_favorites_12plus": {
                name: metrics([x for x in data[name]
                               if x["game_id"] in champion_big_favorite_ids])
                for name in (V04, *VARIANTS)},
        }
    selection = results["december_january_selection"]["comparison"]
    champion = selection[V04]
    if champion["n"] < MIN_VALIDATION:
        chosen = None
    else:
        chosen = min(VARIANTS, key=lambda key: (
            selection[key]["margin_mae"] + 0.5*selection[key]["total_mae"],
            key))
    heldout = results["february_retro_holdout"]["comparison"]
    return {"status": "RETROSPECTIVE_RESEARCH_ONLY", "source_timing_authenticated": False,
            "split_dates": {"november": [f"{year-1}-11-01", f"{year-1}-11-30"],
                            "selection": [f"{year-1}-12-01", f"{year}-01-31"],
                            "heldout": [f"{year}-02-01", f"{year}-02-28"]},
            "selected_before_february": chosen,
            "skipped": skipped, "results": results,
            "heldout_sufficient": heldout[V04]["n"] >= MIN_HOLDOUT}


def frozen_v04(games, ledger_path, now, verified, year=2027):
    """Match the most recent genuine pre-tip v0.4 freeze for each final game."""
    by_id = {str(g["game_id"]): g for g in games}
    best = {}
    path = Path(ledger_path)
    if not path.exists():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("model") != ACTIVE_LIVE_VERSION:
            continue
        gid = str(rec.get("game_id"))
        g = by_id.get(gid)
        if g is None or not final_game(g, year, verified) or not eligible(
                g, now, retrospective=False):
            continue
        try:
            capture, tip = dt(rec["captured_utc"]), dt(g["tip_utc"])
            if (not capture < tip or (tip - capture).total_seconds() > 72*3600
                    or rec.get("tip_utc") != g["tip_utc"]
                    or str(rec.get("home_id")) != str(g["home_id"])
                    or str(rec.get("away_id")) != str(g["away_id"])
                    or float(rec["pred_margin"]) != float(rec["pred_home"])-float(rec["pred_away"])):
                # Floating point comparisons require a rounding tolerance.
                if (not capture < tip or (tip - capture).total_seconds() > 72*3600
                        or rec.get("tip_utc") != g["tip_utc"]
                        or str(rec.get("home_id")) != str(g["home_id"])
                        or str(rec.get("away_id")) != str(g["away_id"])
                        or abs(float(rec["pred_margin"]) -
                               (float(rec["pred_home"])-float(rec["pred_away"]))) > .02):
                    continue
            if (gid not in best or best[gid]["captured_utc"] < rec["captured_utc"]):
                best[gid] = rec
        except (TypeError, KeyError, ValueError, OverflowError):
            continue
    return best


def prospective_counterfactual(games, ledger_path, verified, now,
                                year=2027):
    """Retrospectively compute v0.5 at genuine v0.4 capture times.

    Uses only **observed** pre-tip training finals, never retrospective=True.
    But v0.5 was not frozen then, so it is a counterfactual, not a live
    prospective v0.5 performance claim.
    """
    frozen = frozen_v04(games, ledger_path, now, verified, year=year)
    by_id = {str(g["game_id"]): g for g in games}
    buckets = defaultdict(list)
    for gid, rec in frozen.items():
        buckets[rec["captured_utc"]].append((by_id[gid], rec))
    matched = defaultdict(list)
    skipped = 0
    for captured, pairs in sorted(buckets.items()):
        cutoff = dt(captured)
        models = {}
        for name in VARIANTS:
            try:
                models[name] = fit_variant(
                    games, cutoff, year, verified, name, retrospective=False)
                if dt(models[name]["training_latest_tip"]) >= cutoff:
                    raise AssertionError("Prospective counterfactual leakage")
            except ValueError:
                models[name] = None
        for g, freeze in pairs:
            if any(m is None for m in models.values()):
                skipped += 1
                continue
            candidates = {name: predict(model, g)
                          for name, model in models.items()}
            if any(v is None for v in candidates.values()):
                skipped += 1
                continue
            base = model_observation(
                g, freeze, 12.0)  # use saved authentic probability when available
            if isinstance(freeze.get("home_win_probability_uncalibrated"),
                          (float, int)):
                win = int(base["actual_margin"] > 0)
                base["brier"] = (float(freeze["home_win_probability_uncalibrated"])-win)**2
            matched[V04].append(base)
            for name, pred in candidates.items():
                matched[name].append(
                    model_observation(g, pred, models[name]["margin_sd"]))
    if matched[V04]:
        paired_metrics(matched)
    return {
        "status": "AS_OF_COUNTERFACTUAL_NOT_FROZEN",
        "authentic_v04_pre_tip_games": len(frozen),
        "paired_games": len(matched[V04]),
        "skipped": skipped,
        "v05_truly_frozen_pre_tip_games": 0,
        "comparison": {name: metrics(matched[name])
                       for name in (V04, *VARIANTS)},
        "early_under5": {name: metrics([
            row for row in matched[name] if row["current_games_min"] < 5])
            for name in (V04, *VARIANTS)},
        "warning": ("v0.4 was truly frozen pre-tip. v0.5 variants are "
                    "counterfactual recomputations at those capture times, "
                    "not separately archived pre-tip predictions."),
    }


def run(root="data", year=2026, now=None):
    root = Path(root)
    now = now or utcnow()
    games = read_csv(root/"games.csv")
    try:
        verified = set(load_directory(root/"team_directory.json"))
    except (ValueError, OSError):
        verified = set()
    history = retrospective(games, verified, year=year)
    prospect = prospective_counterfactual(
        games, root/"forecast_ledger.jsonl", verified, now, year=year+1)
    candidate = history.get("selected_before_february")
    selected_hist = (history.get("results", {}).get(
        "february_retro_holdout", {}).get("comparison", {}))
    record = {
        "experiment": VERSION, "active_model": ACTIVE_LIVE_VERSION,
        "active_model_changed": False, "promoted": False,
        "historical_score_reconstruction_only": True,
        "historical": history, "current_season": prospect,
        "candidate_selected_on_prior_window": candidate,
        "minimum_truly_frozen_v05_games_for_promotion":
            MIN_TRULY_FROZEN_COMPARISONS,
        "promotion_gate": "BLOCKED_NOT_ENOUGH_FROZEN_V05_FORECASTS",
        "notes": ("No automatic version update. Test improvement independently "
                  "in genuine new pre-tip v0.5 freezes before promotion. "
                  "Historical ESPN game-score acquisition is retrospective."),
    }
    out = root/"reports"/f"v05_transition_challenger_{year}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True)+"\n",
                   encoding="utf-8")
    return record


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data")
    ap.add_argument("--season", default=2026, type=int)
    args = ap.parse_args()
    report = run(args.root, args.season)
    print(json.dumps({"selected": report["candidate_selected_on_prior_window"],
                      "historical": report["historical"]["status"],
                      "february": report["historical"].get("results", {}).get(
                          "february_retro_holdout", {}).get("comparison"),
                      "current": report["current_season"]["comparison"],
                      "promotion": report["promotion_gate"]},
                     indent=2))
