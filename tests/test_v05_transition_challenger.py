"""V0.5 reproducibility, split integrity and pre-tip evidence tests."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cbb import ACTIVE_LIVE_VERSION
from v05_transition_challenger import (
    VARIANTS, V04, final_game, frozen_v04, metrics, parameters,
    prospective_counterfactual, retrospective,
)

UTC = timezone.utc

def iso(t):
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def game(gid, tip, h="1", a="2", hs=72, ats=65, finished=True):
    return {"game_id": str(gid), "tip_utc": iso(tip),
            "season": tip.year + int(tip.month >= 7),
            "home_id": h, "away_id": a, "home": h, "away": a, "neutral": "0",
            "completed": "1" if finished else "0",
            "home_score": str(hs) if finished else "",
            "away_score": str(ats) if finished else "",
            "first_final_observed_utc": iso(tip + timedelta(hours=5)) if finished else "",
            "score_conflict": "0", "identity_conflict": "0"}


def test_predeclared_prior_fades_with_time_without_hindsight():
    nov = datetime(2025, 11, 10, tzinfo=UTC)
    feb = datetime(2026, 2, 10, tzinfo=UTC)
    assert 0.06 <= parameters("roster_fade", feb)["prior_weight"] < parameters("roster_fade", nov)["prior_weight"] <= .30
    assert parameters(V04, nov) == {"prior_weight": 0.30, "alpha": 1.5}
    assert parameters("lighter_carryover", nov)["prior_weight"] == .15
    assert parameters("less_shrinkage", nov)["alpha"] == .75
    with pytest.raises(ValueError):
        parameters("unsupported", nov)


def test_no_directory_prohibits_retrospective_selection():
    assert retrospective([], {}, 2026)["status"] == "BLOCKED_NO_D1_DIRECTORY"


def test_retrospective_future_game_has_no_training_as_of():
    # Only teams 1 and 2, both have two genuinely completed pre-cutoff games.
    tip = datetime(2025, 11, 18, 19, tzinfo=UTC)
    old = [game(str(i), tip - timedelta(days=10-i), hs=80+i) for i in range(3)]
    future = game("later", tip, hs=122)
    comparison = retrospective(old + [future], {"1", "2"}, year=2026,
                               start="2025-11-01", end="2025-11-30")
    assert comparison["results"]["november"]["comparison"][V04]["n"] >= 1
    assert comparison["selected_before_february"] is None
    assert not comparison["heldout_sufficient"]


def test_frozen_v04_checks_team_identity_capture_and_tip(tmp_path):
    tip = datetime(2026, 11, 15, 20, tzinfo=UTC)
    observed = datetime(2026, 11, 16, 3, tzinfo=UTC)
    g = game("a", tip)
    def snapshot(captured, **extra):
        return {"model": ACTIVE_LIVE_VERSION, "game_id": "a",
                "home_id": "1", "away_id": "2", "tip_utc": iso(tip),
                "captured_utc": iso(captured), "pred_home": 76,
                "pred_away": 71, "pred_margin": 5,
                "pred_total": 147, "home_current_games": 3,
                "away_current_games": 4, **extra}
    evidence = [
        snapshot(tip - timedelta(hours=10)),
        snapshot(tip - timedelta(hours=3)),
        snapshot(tip + timedelta(minutes=1)),
        snapshot(tip - timedelta(hours=1), home_id="9"),
        snapshot(tip - timedelta(hours=1), pred_margin=40),
    ]
    path = tmp_path / "frozen.jsonl"
    path.write_text("\n".join(json.dumps(s) for s in evidence))
    rec = frozen_v04([g], path, observed, {"1", "2"}, year=2027)
    assert rec["a"]["captured_utc"] == iso(tip - timedelta(hours=3))
    rec_bad = frozen_v04([g], path, observed, {"1"}, year=2027)
    assert rec_bad == {}


def test_strict_observed_cutoff_and_version_filter(tmp_path):
    tip = datetime(2026, 11, 15, 20, tzinfo=UTC)
    now = datetime(2026, 11, 16, 3, tzinfo=UTC)
    g = game("a", tip)
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps({
        "model": "CBB_SCORE_RIDGE_V0_3", "game_id": "a", "tip_utc": iso(tip),
        "captured_utc": iso(tip - timedelta(hours=2))}) + "\n")
    matched = prospective_counterfactual([g], path, {"1", "2"}, now, 2027)
    assert matched["authentic_v04_pre_tip_games"] == 0
    assert matched["paired_games"] == 0
    assert matched["v05_truly_frozen_pre_tip_games"] == 0


def test_metrics_not_falsely_su_or_roi_when_empty():
    assert metrics([])["su_accuracy"] is None
    assert len(VARIANTS) == 3
    assert not final_game(game("g", datetime(2025, 12, 3, tzinfo=UTC),
                               h="1", a="999"), 2026, {"1", "2"})


def test_v04_favorite_subsets_use_same_matchups_for_every_variant():
    first = datetime(2025, 11, 1, 20, tzinfo=UTC)
    games = [game(str(i), first + timedelta(days=i), hs=104, ats=55)
             for i in range(22)]
    report = retrospective(games, {"1", "2"}, 2026,
                           start="2025-11-01", end="2025-11-25")
    sub = report["results"]["november"]["v04_favorites_12plus"]
    assert sub[V04]["n"] > 0
    assert {sub[key]["n"] for key in (V04, *VARIANTS)} == {sub[V04]["n"]}
