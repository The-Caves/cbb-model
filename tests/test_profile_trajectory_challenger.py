"""Chronological CBB profile trajectory challenger safety and fitting tests."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from cbb import ACTIVE_LIVE_VERSION, fit
from profile_trajectory_challenger import (
    CAP_MARGIN, CAP_TOTAL, FEATURE_NAMES, FEATURE_SETS, MIN_TRAIN,
    VERSION, candidate, champion, correction, freeze, grade, make_profiles,
    matchup_features, ridge, run_backtest, score, strength, train,
)

UTC=timezone.utc


def iso(day):
    return day.isoformat().replace("+00:00","Z")


def game(gid, tip, home="1", away="2", hs=75, ats=65, completed=True):
    return {
        "game_id":str(gid),"tip_utc":iso(tip),
        "season":tip.year+int(tip.month>=7),
        "home_id":home,"away_id":away,"home":home,"away":away,
        "home_score":str(hs) if completed else "",
        "away_score":str(ats) if completed else "",
        "neutral":"0","completed":str(int(completed)),
        "score_conflict":"0","identity_conflict":"0",
        "first_final_observed_utc":(
            iso(tip+timedelta(hours=5)) if completed else ""),
    }


def history():
    old=datetime(2024,11,6,20,tzinfo=UTC)
    current=datetime(2025,11,4,20,tzinfo=UTC)
    older=[game("old"+str(i),old+timedelta(days=i),
                home="1" if i%2 else "2",
                away="2" if i%2 else "1",
                hs=80 if i%2 else 61,ats=61 if i%2 else 80)
           for i in range(14)]
    today=[game("cur"+str(i),current+timedelta(days=i),
                home="1" if i%2 else "2",
                away="2" if i%2 else "1",
                hs=81 if i%2 else 64,ats=64 if i%2 else 81)
           for i in range(10)]
    return older+today


def test_dated_profile_is_unaffected_by_future_target_score():
    cutoff=datetime(2025,11,20,tzinfo=UTC)
    rows=history()
    later=game("target",cutoff+timedelta(days=2),hs=122,ats=20)
    original, older, profiles=make_profiles(
        rows+[later],{"1","2"},cutoff,2026,retrospective=True)
    changed={**later,"home_score":"30","away_score":"150"}
    current2, older2, other=make_profiles(
        rows+[changed],{"1","2"},cutoff,2026,retrospective=True)
    assert original["training_latest_tip"] < iso(cutoff)
    assert older["training_latest_tip"] < iso(cutoff-timedelta(days=21))
    assert original["cutoff"] == iso(cutoff)
    assert older["cutoff"] == iso(cutoff-timedelta(days=21))
    assert profiles["1"]["as_of_utc"] == iso(cutoff)
    assert profiles["1"]["old_as_of_utc"] == iso(cutoff-timedelta(days=21))
    assert profiles["1"]["current_strength"] == other["1"]["current_strength"]
    np.testing.assert_array_equal(
        matchup_features(profiles["1"],profiles["2"]),
        matchup_features(other["1"],other["2"]),
    )


def test_profile_features_fail_closed_on_missing_history_and_low_samples():
    now=datetime(2025,11,20,tzinfo=UTC)
    _, _, profiles=make_profiles(history(),{"1","2"},now,2026,True)
    features=matchup_features(profiles["1"],profiles["2"])
    assert features.shape == (len(FEATURE_NAMES),)
    assert np.all(np.isfinite(features))
    assert matchup_features({**profiles["1"],"current_d1_games":1},
                            profiles["2"]) is None
    assert matchup_features({**profiles["1"],"recent_adjusted_margin":None},
                            profiles["2"]) is None


def test_regularized_challenger_is_bounded_and_does_not_fake_fit():
    rows=[]
    for i in range(MIN_TRAIN+20):
        vector=np.array([float(i%7)]*len(FEATURE_NAMES))
        rows.append({"features":vector,"margin_residual":900.,
                     "total_residual":-900.,"base_margin":4.,
                     "base_total":140.})
    assert ridge(rows[:10],FEATURE_SETS["trajectory_only"],"margin_residual") is None
    fit_result=train(rows,FEATURE_SETS["trajectory_context"])
    predicted=candidate(rows[0],fit_result)
    assert predicted["margin"] == 4+CAP_MARGIN
    assert predicted["total"] == 140-CAP_TOTAL
    assert champion(rows[0]) == {"margin":4.,"total":140.}
    assert fit_result["margin"]["n_train"]==len(rows)
    assert len(fit_result["margin"]["weights"])==len(FEATURE_SETS["trajectory_context"])+1


def test_tiny_history_has_no_claimed_accuracy_or_promotion(tmp_path):
    report=run_backtest(root=tmp_path,year=2026,
                        games=history(),verified={"1","2"})
    assert report["status"]=="INSUFFICIENT_TRAINING_DATA"
    assert report["counts"]["train"] < MIN_TRAIN
    assert report["promoted"] is False
    assert report["active_model_changed"] is False
    assert report["historical_source_timestamps_authenticated"] is False


def test_no_unvalidated_forecast_and_no_retroactive_freeze(tmp_path):
    now=datetime(2025,11,20,tzinfo=UTC)
    future=game("next",now+timedelta(hours=5),completed=False)
    ledger=tmp_path/"candidate.jsonl"
    blocked=freeze(history()+[future],{"1","2"},now,
                   {"status":"INSUFFICIENT_DATA"},ledger)
    assert blocked["captured"]==0
    assert not ledger.exists()


def test_true_pretip_profile_candidate_is_graded_only_on_same_game(tmp_path):
    tip=datetime(2026,11,21,20,tzinfo=UTC)
    final=game("G",tip,home="1",away="2",hs=80,ats=73)
    now=tip+timedelta(hours=9)
    base=tmp_path/"base.jsonl"
    candidate_path=tmp_path/"profile.jsonl"
    frozen={
        "model":ACTIVE_LIVE_VERSION,"game_id":"G","tip_utc":iso(tip),
        "home_id":"1","away_id":"2",
        "captured_utc":iso(tip-timedelta(hours=2)),
        "pred_home":79,"pred_away":72,"pred_margin":7,"pred_total":151,
        "home_current_games":5,"away_current_games":4,
    }
    latest={
        "model":VERSION,"game_id":"G","tip_utc":iso(tip),
        "home_id":"1","away_id":"2",
        "captured_utc":iso(tip-timedelta(hours=3)),
        "training_latest_tip":iso(tip-timedelta(days=1)),
        "old_training_latest_tip":iso(tip-timedelta(days=31)),
        "old_profile_as_of_utc":iso(tip-timedelta(days=21,hours=3)),
        "new_profile_as_of_utc":iso(tip-timedelta(hours=3)),
        "pred_home":77,"pred_away":74,"pred_margin":3,"pred_total":151,
    }
    base.write_text(json.dumps(frozen)+"\n")
    candidate_path.write_text(json.dumps(latest)+"\n")
    comparison=grade([final],{"1","2"},now,candidate_path,base)
    assert comparison["paired_games"]==1
    assert comparison["v04"]["n"]==comparison["v05_trajectory"]["n"]==1
    assert comparison["promotion_gate"]=="INSUFFICIENT_PAIRED_GAMES"
    latest["captured_utc"]=iso(tip+timedelta(minutes=1))
    candidate_path.write_text(json.dumps(latest)+"\n")
    assert grade([final],{"1","2"},now,candidate_path,base)["paired_games"]==0
