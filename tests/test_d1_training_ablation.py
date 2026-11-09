"""No future score or roster claims in the D-I challenger."""
from datetime import datetime,timedelta,timezone
from d1_training_ablation import research_compare

def row(i,season,day,home="1",away="2"):
    tip=datetime(season-1,11,1,19,tzinfo=timezone.utc)+timedelta(days=day)
    return {"game_id":str(i),"season":season,"tip_utc":tip.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home_id":home,"away_id":away,"home":"H","away":"A","neutral":"0",
        "completed":"1","home_score":"80","away_score":"67",
        "first_final_observed_utc":(tip+timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}
def test_matched_d1_and_leakage():
    prior=[row(100+i,2025,i%14) for i in range(20)]
    current=[row(200+i,2026,i) for i in range(15)]
    bad=row(500,2026,9,away="9999")
    q=research_compare(prior+current+[bad],{"1","2"},{"early":["2025-11-08","2025-11-10"]})
    assert not q["promotion"]
    matches=q["by_window"]["early"]["matched_games"]
    assert matches==2
    vals=q["by_window"]["early"]["results"]
    assert {v["n"] for v in vals.values()}=={matches}
def test_empty_d1_directory_rejects_evaluation():
    r=research_compare([row(4,2026,3)],{},{"early":["2025-11-08"]})
    assert r["status"]=="PASS_NO_VERIFIED_D1_DIRECTORY" and not r["promotion"]
