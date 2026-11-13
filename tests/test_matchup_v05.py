"""Matchup v0.5 must keep original model untouched and grade real source lines."""
import json
from datetime import datetime,timedelta,timezone

import numpy as np
import pytest

from cbb import ACTIVE_LIVE_VERSION
from market_capture import normalize
from matchup_v05 import (
    FEATURES, VARIANTS, COLUMNS, matchup_features, ridge,
    train,adjusted,retrospective,freeze,grade,validate_frozen,
    _quote_asof,_winloss,VERSION_BY_VARIANT,
)

UTC=timezone.utc
TIP=datetime(2026,11,18,23,tzinfo=UTC)
def iso(t): return t.isoformat().replace("+00:00","Z")

def game(gid,tip=TIP,final=False):
    return {"game_id":str(gid),"season":2027,"tip_utc":iso(tip),
            "home_id":"1","away_id":"2","home":"A","away":"B",
            "neutral":"0","home_score":"80" if final else "",
            "away_score":"70" if final else "",
            "completed":"1" if final else "0",
            "score_conflict":"0","identity_conflict":"0",
            "first_final_observed_utc":iso(tip+timedelta(hours=5)) if final else ""}

FACTORS=("off_rtg","pace_est","efg_pct","tov_pct","orb_pct","ft_rate","three_rate")
def profile(games=5,boxgames=4,offset=0):
    values={"off_rtg":112+offset,"pace_est":69+offset,
            "efg_pct":.52+offset/100,"tov_pct":.15,
            "orb_pct":.32,"ft_rate":.25,"three_rate":.37}
    profile={"current_d1_games":games,"boxscore_games_current":boxgames,
             "schedule_strength_avg_opponent_points":3+offset,
             "strength_of_record_wins_above_expected":.8+offset,
             "last5_opponent_adjusted_margin":2+offset}
    for factor,value in values.items():
        profile[factor+"_for_adj"]=value
        profile[factor+"_allowed_adj"]=value-1
    return profile


def test_interactions_are_finite_and_refuse_missing_evidence():
    p=matchup_features(profile(offset=3),profile(offset=-3))
    assert len(p)==len(FEATURES)
    assert np.all(np.isfinite(p))
    assert p[-9] > 0
    assert matchup_features(profile(games=1),profile()) is None
    assert matchup_features(profile(boxgames=1),profile()) is None
    assert matchup_features({**profile(),"tov_pct_allowed_adj":None},
                            profile()) is None
    assert len(COLUMNS["core"])<len(COLUMNS["interaction"])
    assert len(VARIANTS)==2


def test_train_is_regularized_capped_and_sample_gated():
    rows=[{"features":np.array([float(i%6)]*len(FEATURES)),
           "margin_residual":80.,"total_residual":-110.,
           "base_margin":5.,"base_total":140.}
          for i in range(220)]
    assert ridge(rows[:10],COLUMNS["core"],"margin_residual") is None
    for variant in VARIANTS:
        pred=adjusted(rows[-1],train(rows,variant))
        assert 5<=pred["margin"]<=10
        assert 133<=pred["total"]<=140


def test_no_holdout_never_promotes_or_mislabels_betting_history():
    report=retrospective([],{},{"1","2"})
    assert report["status"]=="INSUFFICIENT_TRAIN"
    assert report["selected_on_january"] is None
    assert report["retrospective_source_publication_authenticated"] is False
    assert report["betting_lines_historically_authenticated"] is False


def test_no_validated_coefficients_means_no_pretip_forecast(tmp_path):
    ledger=tmp_path/"v05.jsonl"
    result=freeze([game("future")],{},{"1","2"},
                  TIP-timedelta(hours=2),{},ledger)
    assert result["captured"]==0
    assert not ledger.exists()


def test_score_validation_rejects_late_or_tampered_forecasts():
    g=game("g")
    now=TIP-timedelta(hours=3)
    base={"game_id":"g","home_id":"1","away_id":"2","tip_utc":iso(TIP),
          "captured_utc":iso(now),
          "training_latest_tip":iso(now-timedelta(days=1)),
          "model":VERSION_BY_VARIANT["core"],"variant":"core",
          "champion_version":ACTIVE_LIVE_VERSION,"base_prior_weight":.15,
          "pred_home":80,"pred_away":70,"pred_margin":10,"pred_total":150}
    assert validate_frozen(base,g,now)
    assert not validate_frozen({**base,"captured_utc":iso(TIP)},g,TIP)
    assert not validate_frozen({**base,"pred_total":200},g,now)
    assert not validate_frozen({**base,"home_id":"4"},g,now)
    assert not validate_frozen({**base,"model":VERSION_BY_VARIANT["interaction"]},g,now)


def quote(g,when,spread=-6.5,total=147.5):
    return {"source":"ESPN_SCOREBOARD_PRETIP_AS_OBSERVED",
            "provider":"DraftKings","game_id":g["game_id"],
            "tip_utc":g["tip_utc"],"home_id":g["home_id"],
            "away_id":g["away_id"],"captured_utc":iso(when),
            "home_spread":spread,"total":total,
            "spread_derived_from_explicit_favorite_flags":True}


def test_market_quote_must_exist_before_both_forecasts():
    g=game("g")
    qearly=quote(g,TIP-timedelta(hours=5))
    qlate=quote(g,TIP-timedelta(hours=1),spread=-9)
    cutoff=TIP-timedelta(hours=3)
    assert _quote_asof([qearly,qlate],g,cutoff)["home_spread"]==-6.5
    assert _quote_asof([qlate],g,cutoff) is None
    bad={**qearly,"away_id":"other"}
    assert _quote_asof([bad],g,cutoff) is None


def test_same_frozen_completed_game_with_identical_quote_grades_ats_and_ou(tmp_path):
    finished=game("g",final=True)
    champ=tmp_path/"champion.jsonl"
    shadow=tmp_path/"shadow.jsonl"
    market=tmp_path/"market.jsonl"
    now=TIP+timedelta(hours=8)
    frozen=TIP-timedelta(hours=3)
    original={"model":ACTIVE_LIVE_VERSION,"game_id":"g",
              "home_id":"1","away_id":"2","tip_utc":iso(TIP),
              "captured_utc":iso(frozen),"pred_home":77,"pred_away":72,
              "pred_margin":5,"pred_total":149,
              "home_current_games":4,"away_current_games":4}
    challenger={"model":VERSION_BY_VARIANT["core"],"variant":"core",
                "game_id":"g","tip_utc":iso(TIP),"home_id":"1","away_id":"2",
                "captured_utc":iso(frozen-timedelta(minutes=15)),
                "training_latest_tip":iso(frozen-timedelta(days=1)),
                "champion_version":ACTIVE_LIVE_VERSION,
                "base_prior_weight":.15,
                "pred_home":82,"pred_away":71,"pred_margin":11,"pred_total":153}
    champ.write_text(json.dumps(original)+"\n")
    shadow.write_text(json.dumps(challenger)+"\n")
    market.write_text(json.dumps(quote(finished,TIP-timedelta(hours=6)))+"\n")
    report=grade([finished],{"1","2"},now,champ,shadow,market)
    core=report["models"]["core"]
    assert core["frozen_pairs"]==1
    assert core["challenger"]["n"]==core["v04"]["n"]==1
    assert core["source_observed_market"]["matched_market_games"]==1
    assert core["source_observed_market"]["ats_with_min3pt_model_edge"]["wins"]==1
    assert core["source_observed_market"]["ou_with_min4pt_model_edge"]["wins"]==1
    # The base forecast has smaller spread edge and does not qualify.
    market.write_text(json.dumps(quote(finished,TIP-timedelta(hours=1)))+"\n")
    assert grade([finished],{"1","2"},now,champ,shadow,market)["models"]["core"]["source_observed_market"]["matched_market_games"]==0
