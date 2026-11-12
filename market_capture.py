"""Free source-observed CBB market evidence and strict pretip grading.

Uses odds in the *same* ESPN scoreboard payload used for scores, so no paid
provider or extra market API request is required. Only source-observed quotes
captured strictly before scheduled tipoff qualify. No lines are guessed from
team names, spreads, power rankings or postgame results.

Provider and timestamp are retained; these are *observed* market lines, never
claimed to be closing lines or the user's available FanDuel price.
"""
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import math

UTC=timezone.utc
SOURCE="ESPN_SCOREBOARD_PRETIP_AS_OBSERVED"

def utc(value):
    dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if dt.tzinfo is None: raise ValueError("Missing source timezone")
    return dt.astimezone(UTC)

def finite(value, low, high):
    if type(value) not in (float,int) or not math.isfinite(value):
        return None
    value=float(value)
    return value if low<=value<=high else None

def normalize(payload, observed_utc, raw_sha=None):
    now=utc(observed_utc)
    if not isinstance(payload,dict) or not isinstance(payload.get("events"),list):
        raise ValueError("Malformed source scoreboard")
    quotes=[]
    for ev in payload["events"]:
        try:
            game_id=str(ev["id"])
            tip=utc(ev["date"])
            if not game_id or now>=tip or tip-now>timedelta(hours=72):
                continue
            comp=ev["competitions"][0]
            sides={v["homeAway"]:v for v in comp["competitors"]}
            home_id=str(sides["home"]["team"]["id"])
            away_id=str(sides["away"]["team"]["id"])
            offers=comp.get("odds") or []
            if not isinstance(offers,list): continue
            for offer in offers:
                if not isinstance(offer,dict):continue
                provider=offer.get("provider")
                name=provider.get("name","").strip() if isinstance(provider,dict) else ""
                if not name:continue
                home=offer.get("homeTeamOdds") or {}
                away=offer.get("awayTeamOdds") or {}
                hf=home.get("favorite") if isinstance(home,dict) else None
                af=away.get("favorite") if isinstance(away,dict) else None
                raw_spread=finite(offer.get("spread"),-90,90)
                home_spread=None
                # ESPN often publishes the *magnitude* of the spread.
                # Only unequivocal source favorite flags set the side.
                if raw_spread is not None and type(hf) is bool and type(af) is bool and hf!=af:
                    home_spread=0.0 if raw_spread==0 else (-abs(raw_spread) if hf else abs(raw_spread))
                total=finite(offer.get("overUnder"),80,240)
                if home_spread is None and total is None:continue
                quotes.append({
                    "game_id":game_id,"tip_utc":tip.isoformat(),
                    "home_id":home_id,"away_id":away_id,
                    "captured_utc":now.isoformat(),"source":SOURCE,
                    "provider":name,"provider_id":str(provider.get("id","")),
                    "home_spread":home_spread,"total":total,
                    "spread_derived_from_explicit_favorite_flags":home_spread is not None,
                    "source_raw_sha256":raw_sha,
                    "source_publication_time_verified":False,
                    "closing_line_verified":False})
        except (KeyError,IndexError,TypeError,ValueError,OverflowError):
            continue
    return quotes

def append_pretip(payload, observed_utc, ledger, raw_sha=None):
    rows=normalize(payload,observed_utc,raw_sha)
    ledger=Path(ledger)
    old=[]
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():old.append(json.loads(line))
    ids={(r["game_id"],r["captured_utc"],r["provider_id"],r["provider"]) for r in old}
    additions=[r for r in rows if
               (r["game_id"],r["captured_utc"],r["provider_id"],r["provider"]) not in ids]
    ledger.parent.mkdir(parents=True,exist_ok=True)
    if additions:
        with ledger.open("a",encoding="utf-8") as handle:
            for rec in additions:
                handle.write(json.dumps(rec,sort_keys=True)+"\n")
    return {"captured":len(additions),"source":SOURCE,
            "pre_tip_only":True,"paid_api_calls":0}

def validate_quote(q,g):
    try:
        if q.get("source")!=SOURCE:return False
        if str(q["game_id"])!=str(g["game_id"]):return False
        if str(q["home_id"])!=str(g["home_id"]) or str(q["away_id"])!=str(g["away_id"]):return False
        if utc(q["tip_utc"])!=utc(g["tip_utc"]):return False
        if not utc(q["captured_utc"])<utc(g["tip_utc"]):return False
        if utc(g["tip_utc"])-utc(q["captured_utc"])>timedelta(hours=72):return False
        if not q.get("provider"):return False
        if q.get("home_spread") is not None:
            if finite(q["home_spread"],-90,90) is None:return False
            if q.get("spread_derived_from_explicit_favorite_flags") is not True:return False
        if q.get("total") is not None and finite(q["total"],80,240) is None:return False
        return q.get("home_spread") is not None or q.get("total") is not None
    except (ValueError,KeyError,TypeError,OverflowError):return False

def last_pretip_quotes(path, by_id):
    path=Path(path)
    if not path.exists():return {}
    out={}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():continue
        rec=json.loads(line)
        g=by_id.get(str(rec.get("game_id")))
        if g is None or not validate_quote(rec,g):continue
        # FanDuel quotes preferred if source actually identifies FanDuel.
        # Never mislabel DraftKings/ESPN as FD.
        key=str(g["game_id"])
        existing=out.get(key)
        score=lambda x:(int("fanduel" in x["provider"].lower()),
                        utc(x["captured_utc"]))
        if existing is None or score(rec)>score(existing):
            out[key]=rec
    return out

def outcome(model,actual):
    if model==0 or actual==0:return "P"
    return "W" if (model>0)==(actual>0) else "L"

def counts(items, field):
    wins=sum(p[field]=="W" for p in items)
    losses=sum(p[field]=="L" for p in items)
    pushes=sum(p[field]=="P" for p in items)
    return {"w":wins,"l":losses,"p":pushes,"n":len(items),
            "win_rate":round(wins/(wins+losses),4) if wins+losses else None}

def grade_market(rows, frozen, quotes, cutoff, directory):
    from cbb import eligible
    verified={str(x) for x in directory}
    cutoff=utc(cutoff)
    by_id={str(g["game_id"]):g for g in rows}
    observed=[]
    for gid,q in quotes.items():
        g=by_id.get(gid);p=frozen.get(gid)
        if g is None or p is None:continue
        if str(g["home_id"]) not in verified or str(g["away_id"]) not in verified:continue
        if not validate_quote(q,g) or not eligible(g,cutoff,retrospective=False):continue
        if not utc(p["captured_utc"])<utc(g["tip_utc"]):continue
        if str(p["home_id"])!=str(g["home_id"]) or str(p["away_id"])!=str(g["away_id"]):continue
        home_margin=float(g["home_score"])-float(g["away_score"])
        actual_total=float(g["home_score"])+float(g["away_score"])
        rec={"game_id":gid,"model":p["model"],"provider":q["provider"],
             "market_capture_utc":q["captured_utc"],"model_capture_utc":p["captured_utc"]}
        if q.get("home_spread") is not None:
            rec["ats"]=outcome(float(p["pred_margin"])+float(q["home_spread"]),
                               home_margin+float(q["home_spread"]))
            rec["spread_edge_points"]=round(float(p["pred_margin"])+float(q["home_spread"]),2)
        if q.get("total") is not None:
            rec["ou"]=outcome(float(p["pred_total"])-float(q["total"]),
                              actual_total-float(q["total"]))
            rec["total_edge_points"]=round(float(p["pred_total"])-float(q["total"]),2)
        observed.append(rec)
    def rollup(group):
        return {"ats":counts([v for v in group if "ats" in v],"ats"),
                "ou":counts([v for v in group if "ou" in v],"ou")}
    versions={key:rollup([v for v in observed if v["model"]==key])
              for key in sorted({x["model"] for x in observed})}
    return {"status":"OBSERVED_PRETIP_MARKET_RESEARCH_NOT_CLOSING_ODDS",
            "source":SOURCE,"book_lines_captured_before_tip":True,
            "ats_or_ou_grade_only_when_matching_frozen_model":True,
            "matched_games":len(observed),"overall":rollup(observed),"by_model":versions,
            "provider_counts":{k:sum(x["provider"]==k for x in observed)
                               for k in sorted({x["provider"] for x in observed})},
            "notes":"No claim that sportsbook prices were actually available to this user. No calibrated probabilities or ROI without odds and full availability evidence."}
