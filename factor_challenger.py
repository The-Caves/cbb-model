"""CBB M2/M3 research challenger: historical as-of residual learning.

All examples are generated using scores and verified D-I boxscore evidence
from games completed *before* the evaluation game's UTC date. Source data
were fetched retrospectively: this is NOT a verified historical pre-tip
wagering backtest. Never change the active v0.4 model or forecast ledger.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import norm

from cbb import dt, fit as score_fit, predict as score_predict, read_csv
from boxscore_features import load_accepted
from team_directory import load_directory
from team_intelligence import build as build_intelligence

VERSION = "CBB_FACTOR_RESIDUAL_V0_1"
CHAMPION = "CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5"
FACTORS = ("off_rtg", "pace_est", "efg_pct", "tov_pct",
           "orb_pct", "ft_rate", "three_rate")
FEATURE_NAMES = ("schedule_gap", "sor_rate_gap", "last5_form_gap") + tuple(
    f + "_" + suffix for f in FACTORS for suffix in ("matchup_margin", "matchup_total")
)
FEATURE_SETS = {
    "form_only": (0, 1, 2),
    "factors_only": tuple(range(3, len(FEATURE_NAMES))),
    "combined": tuple(range(len(FEATURE_NAMES))),
}
TRAIN_MINIMUM = 80
VALIDATION_MINIMUM = 30
TEST_MINIMUM = 30
RIDGE_ALPHA = 100.0
# Predeclared safety caps. These are bounded research corrections, not
# sportsbook-derived optimal parameters.
MAX_MARGIN_CHANGE = 6.0
MAX_TOTAL_CHANGE = 8.0


def features(home, away):
    """Produce a matched matchup vector; never fabricate unavailable factors.

    Each factor contrasts the offense of a team with the opponent's observed
    defensive allowed tendency, avoiding treating raw team stats as schedule
    neutral. Differential and total terms feed separate residual estimators.
    """
    if min(home["current_d1_games"], away["current_d1_games"]) < 3:
        return None
    if min(home.get("boxscore_games_current", 0),
           away.get("boxscore_games_current", 0)) < 2:
        return None
    values = []
    for f in ("schedule_strength_avg_opponent_points",
              "strength_of_record_wins_above_expected",
              "last5_opponent_adjusted_margin"):
        if home.get(f) is None or away.get(f) is None:
            return None
    values.append(home["schedule_strength_avg_opponent_points"] -
                  away["schedule_strength_avg_opponent_points"])
    values.append(
        home["strength_of_record_wins_above_expected"] / home["current_d1_games"] -
        away["strength_of_record_wins_above_expected"] / away["current_d1_games"]
    )
    values.append(home["last5_opponent_adjusted_margin"] -
                  away["last5_opponent_adjusted_margin"])
    for f in FACTORS:
        h_attack = home.get(f + "_for_adj")
        h_defense = home.get(f + "_allowed_adj")
        a_attack = away.get(f + "_for_adj")
        a_defense = away.get(f + "_allowed_adj")
        if None in (h_attack, h_defense, a_attack, a_defense):
            return None
        home_matchup = float(h_attack) - float(a_defense)
        away_matchup = float(a_attack) - float(h_defense)
        values.extend((home_matchup - away_matchup,
                       home_matchup + away_matchup))
    if len(values) != len(FEATURE_NAMES) or not all(math.isfinite(v) for v in values):
        return None
    return np.asarray(values, dtype=float)


def fit_ridge(train, columns, outcome, ridge_alpha=RIDGE_ALPHA):
    """Learn a conservative signed correction to a frozen base score."""
    if len(train) < TRAIN_MINIMUM:
        return None
    X = np.asarray([row["features"][list(columns)] for row in train])
    y = np.asarray([row[outcome] for row in train])
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale < 1e-8] = 1.0
    Z = (X - mean) / scale
    A = np.column_stack([np.ones(len(Z)), Z])
    penalty = np.eye(A.shape[1]) * float(ridge_alpha)
    penalty[0, 0] = 0
    coefficients = np.linalg.solve(A.T @ A + penalty, A.T @ y)
    return {"mean": mean, "scale": scale, "beta": coefficients,
            "n_train": len(train), "features": tuple(columns)}


def ridge_predict(model, example):
    if model is None:
        return 0.0
    vector = example["features"][list(model["features"])]
    normalized = (vector - model["mean"]) / model["scale"]
    return float(model["beta"][0] + model["beta"][1:] @ normalized)


def predict_variant(row, fits):
    margin_change = float(np.clip(ridge_predict(fits["margin"], row),
                                  -MAX_MARGIN_CHANGE, MAX_MARGIN_CHANGE))
    total_change = float(np.clip(ridge_predict(fits["total"], row),
                                 -MAX_TOTAL_CHANGE, MAX_TOTAL_CHANGE))
    return {"margin": row["base_margin"] + margin_change,
            "total": row["base_total"] + total_change,
            "margin_change": margin_change, "total_change": total_change}


def fit_variants(rows, alpha=RIDGE_ALPHA):
    return {name: {
        "margin": fit_ridge(rows, columns, "margin_residual", ridge_alpha=alpha),
        "total": fit_ridge(rows, columns, "total_residual", ridge_alpha=alpha),
    } for name, columns in FEATURE_SETS.items()}


def evaluate(rows, predictor, error_sd=None):
    """Same denominators for every candidate and the v0.4 comparator."""
    if not rows:
        return {"n": 0, "su_accuracy": None, "margin_mae": None,
                "total_mae": None, "margin_bias": None, "total_bias": None,
                "brier": None}
    n = len(rows)
    margins, totals, correct, brier = [], [], [], []
    for r in rows:
        predicted = predictor(r)
        pred_margin, pred_total = predicted["margin"], predicted["total"]
        actual_margin, actual_total = r["actual_margin"], r["actual_total"]
        margins.append(pred_margin - actual_margin)
        totals.append(pred_total - actual_total)
        correct.append(int((pred_margin > 0) == (actual_margin > 0)))
        sd = max(8.0, float(error_sd or r["base_margin_sd"]))
        probability = float(norm.cdf(pred_margin / sd))
        brier.append((probability - int(actual_margin > 0))**2)
    return {
        "n": n, "su_accuracy": round(sum(correct) / n, 4),
        "margin_mae": round(sum(abs(v) for v in margins) / n, 3),
        "total_mae": round(sum(abs(v) for v in totals) / n, 3),
        "margin_bias": round(sum(margins) / n, 3),
        "total_bias": round(sum(totals) / n, 3),
        "brier": round(sum(brier) / n, 4),
    }


def baseline_predict(r):
    return {"margin": r["base_margin"], "total": r["base_total"]}


def _game_finals(games, year, verified):
    eligible = []
    for g in games:
        if int(g["season"]) != year or str(g["home_id"]) not in verified \
                or str(g["away_id"]) not in verified:
            continue
        if str(g.get("completed")) not in ("1", "True", "true"):
            continue
        if str(g.get("score_conflict", "0")) in ("1", "True", "true") \
                or str(g.get("identity_conflict", "0")) in ("1", "True", "true"):
            continue
        if g.get("home_score") in (None, "") or g.get("away_score") in (None, ""):
            continue
        eligible.append(g)
    return eligible


def collect_examples(games, accepted, verified_ids, year=2026, limit_days=None):
    """Build daily replay snapshots before tips; no same-day game in features."""
    d1 = {str(v) for v in verified_ids}
    groups = defaultdict(list)
    for g in _game_finals(games, year, d1):
        groups[g["tip_utc"][:10]].append(g)
    output = []
    skipped = {"baseline_unavailable": 0, "features_unavailable": 0}
    if not d1:
        return output, {"status": "BLOCKED_D1_DIRECTORY", "days": 0, **skipped}
    # Only compute dates used by the predeclared train / validation / holdout
    # experiment. Later-season games cannot influence this locked comparison.
    ordered_days = [d for d in sorted(groups)
                    if f"{year-1}-11-01" <= d < f"{year}-03-01"]
    if limit_days is not None:
        ordered_days = ordered_days[:limit_days]
    for day in ordered_days:
        cutoff = dt(day + "T00:00:00Z")
        try:
            baseline = score_fit(games, cutoff, test_season=year,
                                 retrospective=True, alpha=1.5,
                                 prior_weight=0.30, verified_ids=d1)
        except ValueError:
            skipped["baseline_unavailable"] += len(groups[day])
            continue
        profiles = build_intelligence(games, accepted, d1, cutoff, year=year,
                                     retrospective=True, score_model=baseline)
        assert profiles["as_of_utc"] == baseline["cutoff"]
        assert dt(baseline["training_latest_tip"]) < cutoff
        if profiles["status"] != "RETROSPECTIVE_RESEARCH_ONLY":
            skipped["features_unavailable"] += len(groups[day])
            continue
        for game in groups[day]:
            pred = score_predict(baseline, game)
            if pred is None:
                skipped["baseline_unavailable"] += 1
                continue
            assert dt(game["tip_utc"]) >= cutoff
            assert dt(pred["training_latest_tip"]) < cutoff
            home = profiles["teams"].get(str(game["home_id"]), {})
            away = profiles["teams"].get(str(game["away_id"]), {})
            v = features(home, away) if home and away else None
            if v is None:
                skipped["features_unavailable"] += 1
                continue
            margin = float(game["home_score"]) - float(game["away_score"])
            total = float(game["home_score"]) + float(game["away_score"])
            output.append({
                "game_id": str(game["game_id"]), "day": day,
                "features": v,
                "base_margin": float(pred["pred_margin"]),
                "base_total": float(pred["pred_total"]),
                "base_margin_sd": float(baseline["margin_sd"]),
                "margin_residual": margin - float(pred["pred_margin"]),
                "total_residual": total - float(pred["pred_total"]),
                "actual_margin": margin, "actual_total": total,
                "current_games_min": min(home["current_d1_games"],
                                         away["current_d1_games"]),
                "boxscore_games_min": min(home["boxscore_games_current"],
                                          away["boxscore_games_current"]),
                "base_abs_margin": abs(float(pred["pred_margin"])),
                "sos_gap": abs(float(v[0])),
            })
    return output, {"status": "RETROSPECTIVE_SOURCE_ONLY", "days": len(ordered_days),
                    "eligible_d1_finals": sum(len(groups[d]) for d in ordered_days),
                    "matched_features": len(output), **skipped}


def validation_score(report):
    """Margin prioritized; equal game denominator held constant."""
    return report["margin_mae"] + 0.5 * report["total_mae"]


def backtest(games, accepted, verified_ids, year=2026, min_train=TRAIN_MINIMUM,
             min_validation=VALIDATION_MINIMUM, min_test=TEST_MINIMUM,
             limit_days=None):
    rows, capture = collect_examples(games, accepted, verified_ids,
                                     year=year, limit_days=limit_days)
    previous = year - 1
    training = [r for r in rows if r["day"] < f"{year}-01-01"]
    validation = [r for r in rows if f"{year}-01-01" <= r["day"] < f"{year}-02-01"]
    heldout = [r for r in rows if f"{year}-02-01" <= r["day"] < f"{year}-03-01"]
    result = {
        "model": VERSION, "baseline": CHAMPION, "status": "INSUFFICIENT_DATA",
        "active_model_unchanged": True, "challenger_promoted": False,
        "historical_source_timing_authenticated": False,
        "design": "Train November/December, choose variant on January, test untouched February",
        "train_dates": [f"{previous}-11-01", f"{previous}-12-31"],
        "validation_dates": [f"{year}-01-01", f"{year}-01-31"],
        "test_dates": [f"{year}-02-01", f"{year}-02-28"],
        "feature_names": list(FEATURE_NAMES),
        "ridge_alpha": RIDGE_ALPHA,
        "correction_limits_points": {
            "margin": MAX_MARGIN_CHANGE, "total": MAX_TOTAL_CHANGE,
        },
        "minimums": {"training": min_train, "validation": min_validation,
                     "heldout": min_test},
        "counts": {"training": len(training), "validation": len(validation),
                   "heldout": len(heldout)},
        "coverage": capture,
        "validation": {}, "heldout": {},
        "warning": ("Historical ESPN game boxscores were fetched retrospectively. "
                    "No independent 2025-26 pre-tip source timestamps or "
                    "authenticated market closing lines; results do not "
                    "demonstrate wagering profitability."),
    }
    if len(training) < max(min_train, TRAIN_MINIMUM):
        result["status"] = "INSUFFICIENT_TRAINING_DATA"
        return result
    fits = fit_variants(training)
    result["validation"] = {"v04": evaluate(validation, baseline_predict)}
    if len(validation) < min_validation:
        result["status"] = "INSUFFICIENT_VALIDATION_DATA"
        return result
    for name in FEATURE_SETS:
        result["validation"][name] = evaluate(
            validation, lambda r, m=name: predict_variant(r, fits[m]))
    selected = min(FEATURE_SETS, key=lambda name:
                   (validation_score(result["validation"][name]), name))
    result["selected_on_january_only"] = selected
    result["validation_improvement"] = {
        "margin_mae_points": round(
            result["validation"]["v04"]["margin_mae"] -
            result["validation"][selected]["margin_mae"], 3),
        "total_mae_points": round(
            result["validation"]["v04"]["total_mae"] -
            result["validation"][selected]["total_mae"], 3),
    }
    if len(heldout) < min_test:
        result["status"] = "INSUFFICIENT_UNTOUCHED_FEBRUARY_DATA"
        return result
    result["heldout"] = {
        "v04": evaluate(heldout, baseline_predict),
        selected: evaluate(heldout,
                           lambda r: predict_variant(r, fits[selected])),
    }
    selected_metrics = result["heldout"][selected]
    baseline_metrics = result["heldout"]["v04"]
    result["heldout_improvement"] = {
        "margin_mae_points": round(baseline_metrics["margin_mae"] -
                                   selected_metrics["margin_mae"], 3),
        "total_mae_points": round(baseline_metrics["total_mae"] -
                                  selected_metrics["total_mae"], 3),
        "su_percentage_points": round(100 * (
            selected_metrics["su_accuracy"] - baseline_metrics["su_accuracy"]), 2),
    }
    result["heldout_strata"] = {}
    for group, subset in (
        ("early_low_sample", [r for r in heldout if r["current_games_min"] < 8]),
        ("large_baseline_gap_12plus", [r for r in heldout if r["base_abs_margin"] >= 12]),
        ("sos_gap_6plus", [r for r in heldout if r["sos_gap"] >= 6]),
    ):
        result["heldout_strata"][group] = {
            "v04": evaluate(subset, baseline_predict),
            selected: evaluate(subset,
                               lambda r: predict_variant(r, fits[selected])),
        }
    result["status"] = "RETROSPECTIVE_HOLDOUT_COMPARISON_COMPLETE"
    result["holdout_better_margin"] = (selected_metrics["margin_mae"] <
                                       baseline_metrics["margin_mae"])
    result["holdout_better_total"] = (selected_metrics["total_mae"] <
                                      baseline_metrics["total_mae"])
    result["needs_independent_prospective_validation"] = True
    return result


def run(root="data", year=2026):
    root = Path(root)
    games = read_csv(root / "games.csv")
    accepted = load_accepted(root / "advanced" / "boxscore_team_games.jsonl")
    try:
        verified = load_directory(root / "team_directory.json")
    except (OSError, ValueError):
        verified = {}
    report = backtest(games, accepted, verified, year=year)
    out = root / "reports" / f"factor_challenger_{year}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data")
    parser.add_argument("--season", type=int, default=2026)
    args = parser.parse_args()
    result = run(args.root, args.season)
    print(json.dumps(result, indent=2))
