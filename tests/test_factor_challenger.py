"""Deterministic leakage, learning and promotion guards for the CBB challenger."""
from datetime import datetime, timedelta, timezone

import numpy as np

from factor_challenger import (
    FACTORS, FEATURE_NAMES, TRAIN_MINIMUM, backtest, collect_examples,
    evaluate, features, fit_ridge, fit_variants, predict_variant,
)

UTC = timezone.utc
START = datetime(2025, 11, 1, 19, tzinfo=UTC)


def iso(x):
    return x.isoformat().replace("+00:00", "Z")


def game(gid, when, home="1", away="2", home_points=82, away_points=67):
    return {
        "game_id": str(gid), "season": 2026, "tip_utc": iso(when),
        "home_id": home, "away_id": away, "home": home, "away": away,
        "neutral": "0", "home_score": str(home_points),
        "away_score": str(away_points), "completed": "1",
        "score_conflict": "0", "identity_conflict": "0",
        "first_final_observed_utc": iso(when + timedelta(hours=5)),
    }


def boxscore(g):
    return {
        "game_id": g["game_id"], "season": 2026, "tip_utc": g["tip_utc"],
        "home_id": g["home_id"], "away_id": g["away_id"],
        "source_captured_utc": "2026-11-13T19:00:00Z",
        "home": {
            "points": float(g["home_score"]), "off_rtg": 117,
            "pace_est": 70, "efg_pct": .52, "tov_pct": .15,
            "orb_pct": .34, "ft_rate": .27, "three_rate": .38,
        },
        "away": {
            "points": float(g["away_score"]), "off_rtg": 96,
            "pace_est": 70, "efg_pct": .46, "tov_pct": .18,
            "orb_pct": .25, "ft_rate": .24, "three_rate": .35,
        },
    }


def profile(current_games=4, box_games=4, bump=0):
    p = {
        "current_d1_games": current_games,
        "current_d1_wins": 3,
        "schedule_strength_avg_opponent_points": bump,
        "strength_of_record_wins_above_expected": bump / 2,
        "last5_opponent_adjusted_margin": bump,
        "boxscore_games_current": box_games,
    }
    for f in FACTORS:
        p[f + "_for_adj"] = bump + 1
        p[f + "_allowed_adj"] = bump - 1
    return p


def test_factor_feature_shapes_and_no_imputation():
    a, b = profile(bump=2), profile(bump=-3)
    x = features(a, b)
    assert x.shape == (len(FEATURE_NAMES),)
    assert np.all(np.isfinite(x))
    assert x[0] == 5
    assert features({**a, "boxscore_games_current": 1}, b) is None
    assert features({**a, "efg_pct_for_adj": None}, b) is None
    assert features({**a, "current_d1_games": 1}, b) is None


def test_regularized_training_corrections_do_not_exceed_predeclared_caps():
    rows = []
    for i in range(TRAIN_MINIMUM + 20):
        x = np.array([float(i % 7)] * len(FEATURE_NAMES))
        rows.append({"features": x, "margin_residual": 300.,
                     "total_residual": -300., "base_margin": 4.,
                     "base_total": 140.})
    fits = fit_variants(rows)
    p = predict_variant(rows[-1], fits["combined"])
    assert 4 <= p["margin"] <= 10
    assert 132 <= p["total"] <= 140
    assert fit_ridge(rows[:30], (0, 1), "margin_residual") is None


def test_replay_never_sees_same_day_or_future_score():
    prior = [game("N" + str(i), START + timedelta(days=i),
                  home_points=75 + i, away_points=67)
             for i in range(4)]
    tip = datetime(2026, 1, 15, 20, tzinfo=UTC)
    target = game("JAN", tip, home_points=80, away_points=65)
    evidence = {g["game_id"]: boxscore(g) for g in prior}
    rows, audit = collect_examples(prior + [target], evidence, {"1", "2"},
                                   year=2026)
    assert audit["status"] == "RETROSPECTIVE_SOURCE_ONLY"
    january = [r for r in rows if r["game_id"] == "JAN"]
    assert len(january) == 1
    changed = {**target, "home_score": "155", "away_score": "45"}
    revised, _ = collect_examples(prior + [changed], evidence, {"1", "2"},
                                  year=2026)
    changed_january = [r for r in revised if r["game_id"] == "JAN"][0]
    np.testing.assert_allclose(january[0]["features"],
                               changed_january["features"])
    assert january[0]["base_margin"] == changed_january["base_margin"]
    assert january[0]["actual_margin"] != changed_january["actual_margin"]
    assert january[0]["day"] == "2026-01-15"


def test_no_training_data_means_no_promotion():
    g = game("N0", START)
    report = backtest([g], {g["game_id"]: boxscore(g)},
                      {"1", "2"}, year=2026)
    assert report["status"] == "INSUFFICIENT_TRAINING_DATA"
    assert report["active_model_unchanged"]
    assert not report["challenger_promoted"]
    assert report["historical_source_timing_authenticated"] is False


def test_metrics_are_on_identical_denominators():
    rows = [
        {"base_margin": 4., "base_total": 140.,
         "actual_margin": -2., "actual_total": 136.,
         "base_margin_sd": 10.},
        {"base_margin": -8., "base_total": 132.,
         "actual_margin": -6., "actual_total": 142.,
         "base_margin_sd": 10.},
    ]
    baseline = evaluate(rows, lambda r: {"margin": r["base_margin"],
                                         "total": r["base_total"]})
    competitor = evaluate(rows, lambda r: {"margin": r["base_margin"] - 2,
                                           "total": r["base_total"] + 4})
    assert baseline["n"] == competitor["n"] == 2
    assert 0 <= baseline["brier"] <= 1
