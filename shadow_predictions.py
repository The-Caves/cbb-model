"""Independent D-I-only early-season shadow forecast stream.

Never replaces CBB v0.3, never grades as a validated betting pick, and never
backfills a forecast after tipoff. Public ESPN IDs only, all source cutoffs
enforced by cbb.fit; prior season observation weight 0.30.
"""
import argparse
import json
from datetime import timedelta
from pathlib import Path
from cbb import fit, predict, read_csv, utcnow, iso, dt
from team_directory import load_directory

SHADOW_VERSION="CBB_D1_PRIOR30_SHADOW_0_1"

def append_shadow(rows, verified_ids, ledger, now=None):
    now=now or utcnow()
    ids={str(v) for v in verified_ids}
    if not ids:
        return {"status":"PASS_NO_VERIFIED_TEAMS","captured":0}
    try:
        model=fit(rows,now,retrospective=False,prior_weight=0.30,verified_ids=ids)
    except ValueError:
        return {"status":"PASS_NO_AUTHENTICATED_TRAINING","captured":0}
    ledger=Path(ledger);ledger.parent.mkdir(parents=True,exist_ok=True)
    prev=[json.loads(x) for x in ledger.read_text().splitlines() if x.strip()] if ledger.exists() else []
    keys={(v["game_id"],v["captured_utc"],v["model"]) for v in prev}
    captured=[]
    for g in rows:
        tip=dt(g["tip_utc"])
        if not now < tip <= now+timedelta(hours=72):
            continue
        if (str(g["home_id"]) not in ids or str(g["away_id"]) not in ids or
            str(g.get("score_conflict","0")) in ("1","True","true") or
            str(g.get("identity_conflict","0")) in ("1","True","true")):
            continue
        p=predict(model,g)
        if p is None:
            continue
        p.update(model=SHADOW_VERSION,captured_utc=iso(now),validation="SHADOW_UNVALIDATED",
                 public_betting_recommendation=False,live_champion_changed=False)
        key=(p["game_id"],p["captured_utc"],p["model"])
        if key not in keys:
            captured.append(p);keys.add(key)
    with ledger.open("a",encoding="utf-8") as out:
        for rec in captured:
            out.write(json.dumps(rec,sort_keys=True)+"\n")
    return {"status":"SHADOW_ONLY","captured":len(captured),
            "training_games":model["n_games"],"model":SHADOW_VERSION,
            "prior_weight":0.30}

def main():
    p=argparse.ArgumentParser();p.add_argument("--root",default="data")
    args=p.parse_args();root=Path(args.root)
    try:ids=load_directory(root/"team_directory.json")
    except (OSError,ValueError):ids={}
    print(json.dumps(append_shadow(read_csv(root/"games.csv"),ids,
                                  root/"shadow_ledger.jsonl"),indent=2))
if __name__=="__main__":
    main()
