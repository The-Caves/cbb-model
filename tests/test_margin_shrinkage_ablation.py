"""All candidates must evaluate identical D-I games strictly before their tips."""
from datetime import datetime,timedelta,timezone
from margin_shrinkage_ablation import run_comparison, VARIANTS

UTC=timezone.utc

def game(i,year,day,home="1",away="2"):
    tip=datetime(year-1,11,1,19,tzinfo=UTC)+timedelta(days=day)
    return {"game_id":str(i),"season":year,"tip_utc":tip.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "home_id":home,"away_id":away,"home":home,"away":away,"neutral":0,
            "completed":1,"home_score":82,"away_score":70,
            "first_final_observed_utc":(tip+timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}

def test_matched_no_promotion_and_no_unknown_schools():
    rows=[game(i,2025,i%20) for i in range(40)]
    rows += [game(100+i,2026,i) for i in range(20)]
    rows.append(game(999,2026,9,away="unverified"))
    report=run_comparison(rows,{"1":"H","2":"A"},
                          windows={"holdout":["2025-11-08","2025-11-10"]})
    assert report["promoted"] is False
    assert report["reports"]["holdout"]["n"]==2
    assert len(report["reports"]["holdout"]["models"])==len(VARIANTS)
    assert {x["all"]["n"] for x in report["reports"]["holdout"]["models"].values()}=={2}

def test_no_verified_directory_is_not_fake_evidence():
    assert run_comparison([],{})["status"]=="BLOCKED_NO_VERIFIED_D1_TEAMS"
