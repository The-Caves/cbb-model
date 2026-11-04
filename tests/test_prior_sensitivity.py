from datetime import datetime,timedelta,timezone
from prior_sensitivity import compare

def game(i,year,day,home="1",away="2"):
    dt=datetime(year-1,11,1,tzinfo=timezone.utc)+timedelta(days=day)
    return {"game_id":str(i),"season":year,"tip_utc":dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "completed":1,"neutral":0,"home_id":home,"away_id":away,
            "home":"Team One","away":"Team Two","home_score":80,"away_score":70,
            "first_final_observed_utc":(dt+timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}

def test_matched_no_promotion():
    gs=[game(i,2025,i) for i in range(7)]
    gs += [game(100+i,2026,i) for i in range(10)]
    report=compare(gs,{"1":"A","2":"B"},end="2025-11-11")
    assert report["active_model_unchanged"]
    counts={item["all"]["n"] for item in report["variants"].values()}
    assert counts=={10}
    assert report["variants"]["v03_60pct_per_game"]["prior_season_per_game_weight"]==0.6

def test_unknown_division_or_disputed_game_not_labeled_valid():
    gs=[game(i,2025,i) for i in range(7)]
    gs += [game(100,2026,0,away="9"),game(101,2026,1),game(102,2026,2)]
    gs[-1]["score_conflict"]=1
    report=compare(gs,{"1":"A","2":"B"},end="2025-11-05")
    assert report["matched_games"]==1
    assert report["historical_source_time_authenticated"] is False
