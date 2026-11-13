"""Predeclared opponent-adjusted recency challengers must be leak-free."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from cbb import ACTIVE_LIVE_VERSION, fit, predict
from recency_challenger import (
    ALL, VARIANTS, CONFIGS, VERSION, backtest, fit_variant, freeze, grade,
    matchup_strata, validate_shadow, version,
)

UTC=timezone.utc
BASE=datetime(2026,11,1,20,tzinfo=UTC)


def iso(x):
    return x.isoformat().replace("+00:00","Z")


def game(gid,tip,home="1",away="2",hs=75,ats=69,completed=True):
    return {
        "game_id":str(gid),"tip_utc":iso(tip),
        "season":tip.year+int(tip.month>=7),
        "home_id":str(home),"away_id":str(away),
        "home":str(home),"away":str(away),"neutral":"0",
        "home_score":str(hs) if completed else "",
        "away_score":str(ats) if completed else "",
        "completed":str(int(completed)),
        "score_conflict":"0","identity_conflict":"0",
        "first_final_observed_utc":iso(tip+timedelta(hours=5)) if completed else "",
    }


def history():
    prior=datetime(2025,11,3,20,tzinfo=UTC)
    current=datetime(2026,11,2,20,tzinfo=UTC)
    older=[game("old"+str(i),prior+timedelta(days=i),
                hs=65+(i%5),ats=69-(i%4)) for i in range(8)]
    today=[game("cur"+str(i),current+timedelta(days=i*2),
                hs=(61 if i < 4 else 100),ats=70) for i in range(9)]
    return older+today


def test_optin_recency_changes_weight_but_exact_champion_default_stays_same():
    data=history()
    cutoff=BASE+timedelta(days=28)
    args={"test_season":2027, "verified_ids":{"1","2"},
          "prior_weight":.30,"alpha":1.5,"retrospective":True}
    champion=fit(data,cutoff,**args)
    explicit_unweighted=fit(data,cutoff,**args,recency_half_life_days=None)
    future=game("NEXT",cutoff+timedelta(hours=1),completed=False)
    assert predict(champion,future)==predict(explicit_unweighted,future)
    near=fit(data,cutoff,**args,recency_half_life_days=21,
             recency_floor=.60)
    early=predict(champion,future)
    newer=predict(near,future)
    assert abs(early["pred_margin"]-newer["pred_margin"])>0.001
    assert near["recency_half_life_days"]==21
    assert champion["recency_half_life_days"] is None


def test_future_game_results_do_not_change_asof_recency_rating():
    data=history()
    cutoff=BASE+timedelta(days=28)
    after=game("AFTER",cutoff+timedelta(days=2),hs=180,ats=25)
    changed={**after, "home_score":"25","away_score":"180"}
    a=fit_variant(data+[after],cutoff,2027,{"1","2"},"recency_45",True)
    b=fit_variant(data+[changed],cutoff,2027,{"1","2"},"recency_45",True)
    np.testing.assert_allclose(a["beta"],b["beta"],atol=1e-8)
    assert a["training_latest_tip"]<iso(cutoff)


def test_untrusted_or_extreme_weight_settings_fail():
    with pytest.raises(ValueError):
        fit(history(),BASE+timedelta(days=28),test_season=2027,
            retrospective=True,recency_half_life_days=4)
    with pytest.raises(ValueError):
        fit(history(),BASE+timedelta(days=28),test_season=2027,
            retrospective=True,recency_floor=.01)


def test_no_retrospective_coverage_is_never_called_a_win():
    report=backtest(history(),{"1","2"},year=2026)
    assert report["status"]=="INSUFFICIENT_MATCHED_GAME_COVERAGE"
    assert report["selected_on_validation_before_test"] is None
    assert not report["promoted"]
    assert report["historical_source_time_authenticated"] is False
    assert report["results"]["february_test"]["comparison"]["v04"]["n"]==0


def test_unique_immutable_model_ids_and_configuration():
    assert len(set(version(name) for name in VARIANTS))==3
    assert len(ALL)==5
    assert CONFIGS["v04"]=={"prior_weight":.30,"alpha":1.5}
    assert CONFIGS["lighter_carryover"]["prior_weight"]==.15
    assert all(CONFIGS[name]["recency_floor"]==.6 for name in VARIANTS)
    with pytest.raises(ValueError):
        version("v04")


def test_freeze_only_real_future_games_and_no_overlap_with_live(tmp_path):
    now=BASE+timedelta(days=28)
    target=game("FUTURE",now+timedelta(hours=10),completed=False)
    started=game("STARTED",now-timedelta(minutes=1),completed=False)
    outfile=tmp_path/"recency.jsonl"
    report=freeze(history()+[target,started],{"1","2"},now,outfile)
    assert report["captured"]==3
    rows=[json.loads(x) for x in outfile.read_text().splitlines()]
    assert len(rows)==3
    assert {r["game_id"] for r in rows}=={"FUTURE"}
    assert all(validate_shadow(p,target,now) for p in rows)
    assert freeze(history()+[target],{"1","2"},now,outfile)["captured"]==0
    assert freeze(history()+[target],{"1","2"},now+timedelta(hours=10),
                  outfile)["captured"]==0
    tamper={**rows[0], "pred_total":200}
    assert not validate_shadow(tamper,target,now)


def test_grade_requires_same_game_frozen_v04_with_capture_within_two_hours(tmp_path):
    tip=BASE+timedelta(days=30)
    now=tip+timedelta(hours=8)
    played=game("g",tip,hs=80,ats=68)
    winner=tmp_path/"v04.jsonl"
    research=tmp_path/"recency.jsonl"
    champion={
        "model":ACTIVE_LIVE_VERSION,
        "game_id":"g","tip_utc":iso(tip),"home_id":"1","away_id":"2",
        "pred_home":78,"pred_away":72,"pred_margin":6,
        "pred_total":150,"captured_utc":iso(tip-timedelta(hours=3)),
        "home_current_games":4,"away_current_games":4,
    }
    challenger={**champion,
        "model":version("recency_45"),
        "variant":"recency_45",
        "champion_version":ACTIVE_LIVE_VERSION,
        "training_latest_tip":iso(tip-timedelta(days=1)),
        "prior_weight":.30,"recency_half_life_days":45,
        "recency_floor":.60,
        "captured_utc":iso(tip-timedelta(hours=6)),
    }
    winner.write_text(json.dumps(champion)+"\n")
    research.write_text(json.dumps(challenger)+"\n")
    missing=grade([played],{"1","2"},now,winner,research)
    assert missing["graded_by_variant"]["recency_45"]["v04"]["n"]==0
    challenger["captured_utc"]=iso(tip-timedelta(hours=4))
    research.write_text(json.dumps(challenger)+"\n")
    valid=grade([played],{"1","2"},now,winner,research)
    assert valid["graded_by_variant"]["recency_45"]["v04"]["n"]==1
    assert valid["graded_by_variant"]["recency_45"]["challenger"]["n"]==1
    assert valid["promotion_gate"]=="NOT_AUTO_PROMOTED"
