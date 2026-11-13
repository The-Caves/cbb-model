"""Source-verified possession/efficiency challenger; never deployed as picks.

Historical replay uses retrospectively acquired box scores, not authentic
historical API availability. Strict day cutoffs prevent future-game leakage.
"""
import argparse
import json
import math
from collections import defaultdict, Counter
from datetime import timedelta
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix, diags, vstack
from scipy.sparse.linalg import lsqr

from cbb import dt, iso, metrics, utcnow, read_csv, fit as score_fit, predict as score_predict
from boxscore_features import load_accepted
from team_directory import load_directory
from cbb_data_quality import audit

VERSION="CBB_PACE_EFF_CHALLENGER_V0_1"

def usable(rec, cutoff, target, verified_ids, retrospective):
    try:
        if int(rec['season']) not in (target,target-1):
            return False
        if str(rec['home_id']) not in verified_ids or str(rec['away_id']) not in verified_ids:
            return False
        if dt(rec['tip_utc'])+timedelta(hours=4)>cutoff:
            return False
        if not retrospective and dt(rec['source_captured_utc'])>cutoff:
            return False
        return all(35<=float(rec[side]['pace_est'])<=145 and
                   55<=float(rec[side]['off_rtg'])<=180
                   for side in ('home','away'))
    except (KeyError,ValueError,TypeError):
        return False

def fit_efficiency(records, cutoff, target, verified_ids, retrospective=False,
                   min_training=250, alpha=12.0, last_season_weight=0.55):
    verified_ids={str(x) for x in verified_ids}
    selected=[r for r in records if usable(r,cutoff,target,verified_ids,retrospective)]
    if len(selected)<min_training:
        return None
    names=sorted({str(r[k]) for r in selected for k in ('home_id','away_id')})
    index={x:i for i,x in enumerate(names)}
    n=len(names)
    rr=[];cc=[];vv=[];y=[]
    pace_sum=0.0;wsum=0.0;pace_by_team=defaultdict(lambda:[0.0,0.0]);counts=Counter()
    max_tip=None
    for r in selected:
        h,a=str(r['home_id']),str(r['away_id'])
        weight=1.0 if int(r['season'])==target else last_season_weight
        speed=(float(r['home']['pace_est'])+float(r['away']['pace_est']))/2
        pace_sum+=weight*speed;wsum+=weight
        for tid in (h,a):
            pace_by_team[tid][0]+=weight*speed
            pace_by_team[tid][1]+=weight
            if int(r['season'])==target:counts[tid]+=1
        for o,d,side,venue in ((h,a,'home',1),(a,h,'away',-1)):
            row=len(y)
            scale=math.sqrt(weight)
            rr.extend((row,row,row))
            cc.extend((0,1+index[o],1+n+index[d]))
            vv.extend((scale,scale,scale))
            # Home advantage is fixed at 3.5 POINTS per game, not learned
            # from unbalanced early-season Division-I home schedules.
            venue_points=venue*1.75/speed*100
            y.append(scale*(float(r[side]['off_rtg'])-venue_points))
        max_tip=max(max_tip or r['tip_utc'],r['tip_utc'])
    x=coo_matrix((vv,(rr,cc)),shape=(len(y),1+2*n)).tocsr()
    ridge=np.r_[1e-7,np.full(2*n,alpha)]
    beta=lsqr(vstack([x,diags(np.sqrt(ridge))],format='csr'),
              np.r_[y,np.zeros(1+2*n)],atol=1e-7,btol=1e-7,iter_lim=800)[0]
    mean=pace_sum/wsum
    team_tempo={tid:(s/max(w,1e-8)-mean)*w/(w+5)
                for tid,(s,w) in pace_by_team.items()}
    return {'beta':beta,'index':index,'n':n,'counts':counts,'mean_pace':mean,
            'tempo':team_tempo,'last_tip':max_tip,'cutoff_utc':iso(cutoff),
            'training_n':len(selected),'retrospective':retrospective}

def predict(model,g,min_team_games=3):
    if model is None:return None
    h,a=str(g['home_id']),str(g['away_id'])
    ix=model['index']
    if h not in ix or a not in ix or min(model['counts'][h],model['counts'][a])<min_team_games:
        return None
    n=model['n']; b=model['beta']
    pace=float(np.clip(model['mean_pace']+(model['tempo'][h]+model['tempo'][a])/2,45,100))
    h100=b[0]+b[1+ix[h]]+b[1+n+ix[a]]
    a100=b[0]+b[1+ix[a]]+b[1+n+ix[h]]
    home=pace*h100/100; away=pace*a100/100
    non_neutral=str(g.get('neutral','0')) not in ('1','True','true')
    if non_neutral:home+=1.75;away-=1.75
    if not all(math.isfinite(x) for x in (home,away,pace)) or min(home,away)<35 or max(home,away)>150:
        return None
    return {'model':VERSION,'game_id':str(g['game_id']),
            'pred_home':round(home,2),'pred_away':round(away,2),
            'pred_margin':round(home-away,2),'pred_total':round(home+away,2),
            'predicted_pace':round(pace,2),'training_last_tip':model['last_tip'],
            'training_cutoff_utc':model['cutoff_utc'],'research_only':True}

def backtest(games, boxscores, d1_ids, season=2026,
             min_training=250,min_eval=100):
    """Current v0.4 score baseline and challenger evaluated on identical games.

    Retrospectively fetched source statistics are NOT timestamped evidence
    that those statistics were originally available before hypothetical tip.
    """
    d1={str(x) for x in d1_ids}
    quality=audit(games,{str(r['game_id']):r for r in boxscores},utcnow(),d1)
    if quality['quarantined_rows'] or not d1:
        return {'status':'BLOCKED_UNVERIFIED_SOURCE','challenger_promoted':False,
                'quarantine_count':quality['quarantined_rows']}
    finals={str(g['game_id']):g for g in games
            if int(g['season'])==season
            and str(g.get('completed')) in ('1','True','true')
            and str(g.get('score_conflict','0')) not in ('1','True','true')
            and str(g['home_id']) in d1 and str(g['away_id']) in d1
            and g.get('home_score') not in ('',None) and g.get('away_score') not in ('',None)}
    groups=defaultdict(list)
    for r in boxscores:
        g=finals.get(str(r['game_id']))
        if g is not None and g['tip_utc']==r['tip_utc']:
            groups[g['tip_utc'][:10]].append(g)
    evaluated=[];comparison=[];fit_days=0
    for day in sorted(groups):
        cutoff=dt(day+'T00:00:00Z')
        model=fit_efficiency(boxscores,cutoff,season,d1,retrospective=True,min_training=min_training)
        if model is None:continue
        try:baseline=score_fit(games,cutoff,test_season=season,retrospective=True,
                               alpha=1.5,prior_weight=0.30,verified_ids=d1)
        except ValueError:continue
        fit_days+=1
        for game in groups[day]:
            p=predict(model,game)
            b=score_predict(baseline,game)
            if p is None or b is None:continue
            assert dt(p['training_last_tip'])<cutoff
            assert dt(b['training_latest_tip'])<cutoff
            margin=float(game['home_score'])-float(game['away_score'])
            total=float(game['home_score'])+float(game['away_score'])
            for arr,r in ((evaluated,p),(comparison,b)):
                arr.append({'pred_margin':r['pred_margin'],'pred_total':r['pred_total'],
                            'actual_margin':margin,'actual_total':total,
                            'correct':int((r['pred_margin']>0)==(margin>0))})
    enough=len(evaluated)>=min_eval
    return {'model':VERSION,'status':'RETROSPECTIVE_RESEARCH_ONLY' if enough
            else 'INSUFFICIENT_MATCHED_HISTORICAL_EVALUATIONS',
            'challenger_promoted':False,'games_evaluated':len(evaluated),
            'required_games':min_eval,'minimum_training_games':min_training,
            'fit_days':fit_days,'candidate':metrics(evaluated),
            'v04_same_games':metrics(comparison),
            'baseline_model':'CBB_SCORE_RIDGE_V0_4_D1_PRIOR30_A1_5',
            'historical_boxscores_acquired_retrospectively':True,
            'warning':'No inference of validated picks, profitable wagering or verified historical data availability.'}

def run(root='data',season=2026):
    root=Path(root)
    games=read_csv(root/'games.csv')
    records=list(load_accepted(root/'advanced/boxscore_team_games.jsonl').values())
    try:d1=load_directory(root/'team_directory.json')
    except (OSError,ValueError):d1={}
    result=backtest(games,records,d1,season=season)
    target=root/'reports'/f'efficiency_challenger_{season}.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root',default='data')
    p.add_argument('--season',type=int,default=2026)
    args=p.parse_args()
    print(json.dumps(run(args.root,args.season),indent=2))
