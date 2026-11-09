"""Strict pre-tip freeze and do-not-promote tests for research shadow."""
import json
from datetime import datetime,timedelta,timezone
from shadow_predictions import append_shadow,SHADOW_VERSION
from cbb import dt
UTC=timezone.utc
def game(i,tip,h="1",a="2",done=True):
    return {"game_id":str(i),"tip_utc":tip.strftime("%Y-%m-%dT%H:%M:%SZ"),
       "season":tip.year+int(tip.month>=7),"home_id":h,"away_id":a,
       "home":h,"away":a,"neutral":0,"completed":int(done),
       "home_score":80 if done else "","away_score":70 if done else "",
       "first_final_observed_utc":(tip+timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ") if done else ""}
def test_shadow_freezes_only_verified_upcoming_and_does_not_change_champion(tmp_path):
    now=datetime(2026,11,9,17,0,tzinfo=UTC)
    old=datetime(2025,11,6,18,0,tzinfo=UTC)
    rows=[game(i,old+timedelta(days=i%12)) for i in range(30)]
    rows += [game(100,now+timedelta(hours=3),done=False),
             game(101,now+timedelta(hours=3),a="999",done=False),
             game(102,now-timedelta(minutes=1),done=False)]
    ledger=tmp_path/"shadow.jsonl"
    x=append_shadow(rows,{"1","2"},ledger,now)
    assert x["captured"]==1
    assert x["model"]==SHADOW_VERSION
    saved=[json.loads(x) for x in ledger.read_text().splitlines()]
    assert len(saved)==1 and saved[0]["game_id"]=="100"
    assert dt(saved[0]["captured_utc"])<dt(saved[0]["tip_utc"])
    assert saved[0]["validation"]=="SHADOW_UNVALIDATED"
    assert saved[0]["live_champion_changed"] is False
    assert append_shadow(rows,{"1","2"},ledger,now)["captured"]==0
def test_missing_verified_roster_never_infers():
    assert append_shadow([],{},'/tmp/does-not-create-shadow.json')["captured"]==0
