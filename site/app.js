'use strict';
/* Site renders only exported source evidence. It never computes a fabricated pick. */
let dataset=null;let selectedDate='';let sortDirection=1;
const byId=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=0)=>v==null?'—':Number(v).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const nicePct=v=>v==null?'—':`${Math.round(Number(v)*100)}%`;
const dateFmt=new Intl.DateTimeFormat(undefined,{weekday:'short',month:'short',day:'numeric'});
const hourFmt=new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit',timeZoneName:'short'});
const dayFmt=new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit'});
const dateKey=d=>{
  const parts=dayFmt.formatToParts(d);const v=k=>parts.find(x=>x.type===k)?.value||'00';
  return `${v('year')}-${v('month')}-${v('day')}`;
};
const isoDay=s=>dateKey(new Date(s));
const todayDay=()=>dateKey(new Date());
const availableDates=(games, today=todayDay())=>[...new Set([...games.map(g=>isoDay(g.tip_utc)),today])].sort();
const nextUnstartedGame=(games, now=Date.now())=>games
  .filter(g=>!g.completed && Number.isFinite(Date.parse(g.tip_utc)) && Date.parse(g.tip_utc)>now)
  .sort((a,b)=>a.tip_utc.localeCompare(b.tip_utc))[0]||null;
const teamShort=s=>{const chunks=String(s||'').split(/\s+/);const acronym=chunks.length>1?chunks.slice(-2).map(x=>x.slice(0,1)).join(''):chunks[0]?.slice(0,3);return acronym?.toUpperCase()||'—'};
const allGames=()=>dataset?.games||[];
const isD1=g=>g.verified_d1_matchup===false?false:(g.home_division==='D1'&&g.away_division==='D1');
const byScope=(games,scope='d1')=>games.filter(g=>
  scope==='all'||(scope==='other'?!isD1(g):isD1(g)));
const findTeam=id=>(dataset?.teams||[]).find(t=>t.id===id);
const recFor=id=>{const t=findTeam(id);return t?`${t.wins}–${t.losses} · ${t.games} GP`:'Record unavailable'};
function timeText(g){return g.completed?'FINAL':g.game_state==='in'?`LIVE${g.game_clock?' · '+g.game_clock:''}`:hourFmt.format(new Date(g.tip_utc));}
function gameCard(g){
  const p=g.projection;
  const real=g.completed && g.home_score!==null && g.away_score!==null;
  const live=!real && g.game_state==='in' && g.home_live_score!=null && g.away_live_score!=null;
  const winner=p?(p.margin>0?g.home:(p.margin<0?g.away:'Toss-up')):'—';
  const side=p?esc(winner):'Unavailable';
  const awayDivision=g.away_division==='OTHER_OR_UNVERIFIED'?' · OTHER / UNVERIFIED OPPONENT':'';
  const homeDivision=g.home_division==='OTHER_OR_UNVERIFIED'?' · OTHER / UNVERIFIED OPPONENT':'';
  const cardStatus=real?'FINAL':live?'LIVE':new Date(g.tip_utc).getTime()<=Date.now()?'STARTED / PENDING':'UPCOMING';
  const condition=p?(p.reliability?.status==='PASS_LOW_CURRENT_SAMPLE'?'PASS · LOW SAMPLE':p.reliability?.status==='PASS_NO_VERIFIED_MARKET'?'PASS · NO BOOK LINE':p.status==='PASS_LOW_SAMPLE'?'PASS · LOW SAMPLE':'RESEARCH PROJECTION'):'NO VERIFIED PICK';
  const when=dateFmt.format(new Date(g.tip_utc));
  const outcome=real && p && p.margin!==0?((g.home_score-g.away_score>0)===(p.margin>0)?' · WINNER HIT':' · WINNER MISS'):'';
  const predictionHtml=p?`
  <div class="proj-head"><small>MODEL PROJECTION · ${esc(p.model||'VERSION UNKNOWN')}</small><span class="proj-tag ${p.status==='RESEARCH_ONLY'?'has':''}">${esc(condition)}</span></div>
  <div class="proj-row">
    <div class="proj-val"><small>PROJECTED WINNER</small><strong class="accent" title="${side}">${side}</strong></div>
    <div class="proj-val"><small>MODEL MARGIN · NOT A BOOK LINE</small><strong>${p.margin===0?'EVEN':`${num(Math.abs(p.margin),1)} pts`}</strong></div>
    <div class="proj-val"><small>MODEL TOTAL</small><strong>${num(p.total,1)}</strong></div>
  </div>
  <div class="data-note" style="font-size:11px;line-height:1.4;margin-top:10px;color:var(--muted)">
    Margin and total are research projections, not betting lines.
    ${p.reliability?.margin_mae_points!=null?`Historical D-I average margin error: ${num(p.reliability.margin_mae_points,1)} pts over ${num(p.reliability.historical_comparison_games)} graded games.`:''}
    <strong>PASS: no verified ATS edge or player availability check.</strong>
  </div>`:`<div class="proj-head"><small>MODEL PROJECTION</small><span class="proj-tag">NO CAPTURE</span></div><div class="no-proj">${(g.identity_source_under_review||g.score_source_under_review)?'Source identity or final score under review: this matchup is excluded from model training and grading.':!isD1(g)?'Other or unverified opponent: no qualified Division I projection. ESPN lists the game; FanDuel listing is not verified.':real?'No archived pre-tip prediction for this game.': 'Waiting for a valid model snapshot before tipoff.'}</div>`;
  const shadow=g.shadow_projection;
  const shadowDetails=shadow?`<p><strong>Experimental D-I-only second opinion:</strong> ${esc(shadow.margin>0?g.home:g.away)} by ${num(Math.abs(shadow.margin),1)}; projected total ${num(shadow.total,1)}. ${shadow.winner_disagrees?'<strong>MODELS DISAGREE — PASS.</strong>':''} This unvalidated challenger uses a lighter previous-season weight and is not a sportsbook recommendation. Captured ${esc(new Date(shadow.captured_utc).toLocaleString())} before tipoff.</p>`:'';
  const awayShown=real?num(g.away_score):live?num(g.away_live_score):p?num(p.away,1):'—';
  const homeShown=real?num(g.home_score):live?num(g.home_live_score):p?num(p.home,1):'—';
  const scoreKind=real?'FINAL':live?'LIVE':p?'PROJ':'';
  return `<article class="game-card" data-game-id="${esc(g.id)}">
    <div class="card-heading"><span>${esc(when)} · ${esc(timeText(g))}${g.neutral?' · NEUTRAL':''}</span><span class="status-chip ${real?'final':p?'ready':'scheduled'}">${esc(cardStatus)}</span></div>
    <div class="team-lines">
      <div class="team-line"><span class="team-crest">${esc(teamShort(g.away))}</span><div class="team-label"><strong title="${esc(g.away)}">${esc(g.away)}</strong><small>AWAY · ${esc(recFor(g.away_id))}${esc(awayDivision)}</small></div><div class="team-score ${real?'actual':'live-proj'}">${awayShown}<small class="score-kind">${scoreKind}</small></div></div>
      <div class="team-line"><span class="team-crest">${esc(teamShort(g.home))}</span><div class="team-label"><strong title="${esc(g.home)}">${esc(g.home)}</strong><small>${g.neutral?'NEUTRAL':'HOME'} · ${esc(recFor(g.home_id))}${esc(homeDivision)}</small></div><div class="team-score ${real?'actual':'live-proj'}">${homeShown}<small class="score-kind">${scoreKind}</small></div></div>
    </div>
    <div class="projection">${predictionHtml}</div>
    <div class="card-footer"><span>${p?`Frozen ${esc(new Date(p.captured_utc).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}))}`:'Spread and total: not sourced'}</span><span>${esc(outcome.replace(/^ · /,''))||'No verified market odds'}</span></div>
    <details><summary>Matchup details</summary><div class="details-body">
      ${p?`<p><strong>Projected score:</strong> ${esc(g.away)} ${num(p.away,1)} – ${esc(g.home)} ${num(p.home,1)}</p><p><strong>Season sample:</strong> ${num(p.away_games)} away games / ${num(p.home_games)} home games. Model trained on ${num(p.training_games)} historical/current games.</p><p><strong>Forecast recorded:</strong> ${esc(new Date(p.captured_utc).toLocaleString())}, strictly before tipoff. Win probabilities are not yet calibrated.</p>`:'<p>No pre-tip model capture is stored. A game without one is deliberately not given a projected winner.</p>'}
      ${shadowDetails}
      <p><strong>ATS / Over-under:</strong> Unavailable until genuine timestamped sportsbook lines are captured. No odds or bet recommendation invented.</p>
    </div></details>
   </article>`;
}
function emptyMarkup(title,desc){return `<div class="empty-state"><div class="empty-inner"><div class="empty-icon">◌</div><h3>${esc(title)}</h3><p>${esc(desc)}</p></div></div>`;}
function dateOptions(){
  const now=todayDay();const unique=availableDates(allGames(),now);
  if(!unique.includes(selectedDate) && selectedDate!=='all')selectedDate=now;
  const pills=[{key:'all',primary:'All',secondary:'DATES'},...unique.map(k=>({key:k,primary:new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric'}).format(new Date(k+'T12:00:00')),secondary:k===now?'TODAY':new Intl.DateTimeFormat(undefined,{weekday:'short'}).format(new Date(k+'T12:00:00'))}))];
  byId('datePills').innerHTML=pills.map(({key,primary,secondary})=>`<button type="button" data-day="${esc(key)}" class="date-pill ${selectedDate===key?'active':''}" aria-pressed="${selectedDate===key}"><small>${esc(secondary)}</small>${esc(primary)}</button>`).join('');
  byId('datePills').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{selectedDate=b.dataset.day;dateOptions();renderGames(b.dataset.day===todayDay());}));
  // Keep the currently selected date visible in the horizontal strip.
  const strip=byId('datePills');const active=strip.querySelector('.date-pill.active');
  if(active)strip.scrollLeft=Math.max(0,active.offsetLeft-strip.offsetLeft-(strip.clientWidth-active.clientWidth)/2);
}
function shiftDay(step){const opts=availableDates(allGames());let next=opts.indexOf(selectedDate)+step;if(next<0)next=0;if(next>=opts.length)next=opts.length-1;selectedDate=opts[next];dateOptions();renderGames(selectedDate===todayDay());}
function renderGames(focusNext=false){
  const search=byId('gameSearch').value.trim().toLocaleLowerCase();const filter=byId('gameFilter').value;const scope=byId('matchupScope').value;
  const now=Date.now();const filtered=byScope(allGames(),scope).filter(g=>(selectedDate==='all'||isoDay(g.tip_utc)===selectedDate)
    &&(!search||`${g.home} ${g.away}`.toLocaleLowerCase().includes(search))
    &&(filter==='all'||filter==='predicted'&&!!g.projection||filter==='completed'&&g.completed||filter==='upcoming'&&!g.completed));
  filtered.sort((a,b)=>a.tip_utc.localeCompare(b.tip_utc)*sortDirection);
  byId('gameCount').textContent=`(${filtered.length})`;
  byId('dayStamp').textContent=selectedDate==='all'?'All dates':dateFmt.format(new Date(selectedDate+'T12:00:00'));
  byId('gameCards').innerHTML=filtered.length?filtered.map(gameCard).join(''):emptyMarkup(allGames().length?'No matchups match these filters':'Awaiting the first live data sync',allGames().length?'Try another day, team, or game status.':'This site is ready, but no real game data or model picks have been imported yet. Once the free GitHub workflow completes, games and actual model projections will populate automatically.');
  const future=byScope(allGames(),scope).filter(g=>!g.completed && new Date(g.tip_utc).getTime()>now);
  const withp=future.filter(g=>!!g.projection);
  byId('heroCount').textContent=num(withp.length);
  byId('heroCoverage').textContent=future.length?`${withp.length} of ${future.length} upcoming games`:'Waiting for upcoming games';
  byId('kpiGames').textContent=num(byScope(allGames(),scope).length);
  byId('kpiForecasts').textContent=num(byScope(allGames(),scope).filter(g=>!!g.projection).length);
  byId('kpiGraded').textContent=num(dataset?.performance?.prospective?.verified_d1_matchups?.n||0);
  // Scroll to the next tipoff on today's board, leaving earlier and live
  // games in the document. With no future tipoffs, keep the list at the top.
  if(focusNext && selectedDate===todayDay() && !search && filter==='all'){
    const next=nextUnstartedGame(filtered, now);
    if(next && typeof requestAnimationFrame==='function')requestAnimationFrame(()=>{
      const target=[...document.querySelectorAll('.game-card')].find(el=>el.dataset.gameId===String(next.id));
      if(target)target.scrollIntoView({block:'start',behavior:'auto'});
    });
  }
}
function perfStats(items){return `<div class="performance-stats">${items.map(x=>`<div class="stat-panel"><div class="stat-title">${esc(x.label)}</div><div class="stat-value">${esc(x.value)}</div><div class="stat-sub">${esc(x.sub)}</div></div>`).join('')}</div>`;}
function metricRow(r){if(!r)return '';return `<div class="metric-row"><div class="metric-item"><small>Winner accuracy</small><strong>${nicePct(r.su_accuracy)}</strong></div><div class="metric-item"><small>Margin MAE</small><strong>${r.margin_mae==null?'—':num(r.margin_mae,1)+' pts'}</strong></div><div class="metric-item"><small>Total MAE</small><strong>${r.total_mae==null?'—':num(r.total_mae,1)+' pts'}</strong></div><div class="metric-item"><small>Games graded</small><strong>${num(r.n)}</strong></div></div>`;}
function renderPerformance(){
  const p=dataset.performance||{};const live=p.prospective?.verified_d1_matchups||{};const hist=p.retrospective_2025_26;
  const all=live.n||0;
  let out=perfStats([{label:'Frozen games graded',value:num(all),sub:'True pre-tip prediction ledger'},{label:'Winner accuracy',value:nicePct(live.su_accuracy),sub:'Not an ATS cover rate'},{label:'Mean margin error',value:live.margin_mae==null?'—':`${num(live.margin_mae,1)} pts`,sub:'Prediction vs. final margin'}]);
  out+=`<section class="data-panel"><h3>2026–27 prospective performance · D-I only</h3><p>Only verified Division I matchups using original pre-tip forecasts. Smaller-school/unverified games are reported separately.</p>${metricRow(live)}${!all?`<p style="margin-top:16px;color:var(--orange)">No qualified completed forecast is available yet. The performance table will populate after authentic pre-tip captures and final scores.</p>`:''}</section>`;
  if(hist){const h=hist.all_games;const established=hist.established_teams;const prior=p.retrospective_v02;
    out+=`<section class="data-panel"><h3>2025–26 historical walk-forward backtest · ${esc(hist.model||'version unreported')}</h3><p>Uses scores from dates before each game to reconstruct prediction inputs. Historical score data was downloaded retrospectively; this is not the same as pre-tip prospective validation.</p>${metricRow(h)}<div class="metric-row"><div class="metric-item"><small>Established teams · Winner accuracy</small><strong>${nicePct(established?.su_accuracy)}</strong></div><div class="metric-item"><small>Baseline winner accuracy</small><strong>${nicePct(hist.simple_baseline?.su_accuracy)}</strong></div><div class="metric-item"><small>Baseline margin MAE</small><strong>${hist.simple_baseline?.margin_mae==null?'—':num(hist.simple_baseline.margin_mae,1)+' pts'}</strong></div></div>${prior?`<p style="margin-top:14px;color:var(--muted)">Previous v0.2 on same historical replay: ${nicePct(prior.all_games?.su_accuracy)} SU, ${num(prior.all_games?.margin_mae,1)}-point margin MAE. This comparison does not imply a betting edge.</p>`:''}</section>`;
  }else{out+=`<section class="data-panel"><h3>Historical walk-forward backtest</h3><p>Not yet populated. The 2025–26 backtest will be displayed when real completed game scores are downloaded and the historical replay finishes.</p></section>`;}
  const other=p.prospective?.other_or_unverified_matchups;
  if(other?.n)out+=`<section class="data-panel"><h3>Other or unverified matchups</h3><p>Excluded from D-I headline performance; sportsbook availability unknown.</p>${metricRow(other)}</section>`;
  const results=(dataset.recent_frozen_results||[]).slice().reverse().slice(0,30);
  out+=`<section class="data-panel"><h3>Most recent tracked results</h3><p>Never includes picks first calculated after tipoff. ATS and over/under records are not available without authenticated lines.</p><div class="results-list">${results.length?results.map(r=>`<div class="result-line"><div><strong>${esc(r.away)} at ${esc(r.home)}</strong><small>${esc(dateFmt.format(new Date(r.tip_utc)))} · Projected margin ${num(r.pred_margin,1)} · Actual margin ${num(r.actual_margin,0)}</small></div><span class="result-badge ${r.winner_correct?'hit':'miss'}">${r.winner_correct?'WINNER HIT':'WINNER MISS'}</span></div>`).join(''):`<div class="empty-state" style="min-height:130px">${emptyInner('No completed frozen predictions yet','Results appear here only after the game finishes and the original forecast is verified.')}</div>`}</div></section>`;
  byId('performanceContent').innerHTML=out;
}
function emptyInner(title,sub){return `<div class="empty-inner"><h3>${esc(title)}</h3><p>${esc(sub)}</p></div>`;}
function renderTeams(){
  const search=byId('teamSearch').value.trim().toLocaleLowerCase();const teams=(dataset.teams||[]).filter(t=>t.name.toLocaleLowerCase().includes(search));
  byId('teamList').innerHTML=teams.length?teams.map(t=>`<article class="team-tile"><div class="team-tile-top"><span class="team-crest">${esc(teamShort(t.name))}</span><div><strong>${esc(t.name)}</strong><small>${num(t.games)} completed games</small></div></div><div class="team-numbers"><div><small>RECORD</small><strong>${num(t.wins)}–${num(t.losses)}</strong></div><div><small>POINTS FOR</small><strong>${num(t.points_for,1)}</strong></div><div><small>POINTS AGAINST</small><strong>${num(t.points_against,1)}</strong></div></div></article>`).join(''):`<div class="empty-state" style="grid-column:1/-1">${emptyInner((dataset.teams||[]).length?'No matching team':'No current team profiles','Historical team profiles will appear after the first verified scoreboard ingestion.')}</div>`;
}
function tab(name){
  ['games','performance','teams','model'].forEach(key=>{const active=key===name;byId(`view-${key}`).hidden=!active;const btn=document.querySelector(`.tab[data-tab="${key}"]`);btn.classList.toggle('active',active);if(active)btn.setAttribute('aria-current','page');else btn.removeAttribute('aria-current')});
  if(name==='performance')renderPerformance();if(name==='teams')renderTeams();
  if(location.hash!==`#${name}`)history.replaceState(null,'',`#${name}`);
}
function init(){
  dataset=window.CBB_STATIC_DATA||{games:[],teams:[],performance:{}};
  byId('seasonLabel').textContent=dataset.season||'2026–27';
  const last=dataset.generated_at;
  const time=last?`Synced ${new Date(last).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}`:'Not synced';
  byId('syncLabel').textContent=time;byId('footerSync').textContent=time;
  if(!last){byId('dataAlert').hidden=false;byId('dataAlert').textContent='Model data has not been published yet. The dashboard is ready, but no predictions will be shown until real data and a pre-tip ledger are available.';}
  selectedDate=todayDay();dateOptions();renderGames(true);
  byId('prevDay').addEventListener('click',()=>shiftDay(-1));byId('nextDay').addEventListener('click',()=>shiftDay(1));
  byId('gameSearch').addEventListener('input',renderGames);byId('gameFilter').addEventListener('change',renderGames);byId('matchupScope').addEventListener('change',()=>renderGames(true));
  byId('teamSearch').addEventListener('input',renderTeams);
  document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>tab(b.dataset.tab)));
  const key=location.hash.replace('#','');tab(['games','teams','performance','model'].includes(key)?key:'games');
  window.addEventListener('hashchange',()=>{const tabName=location.hash.replace('#','');if(['games','teams','performance','model'].includes(tabName))tab(tabName)});
}
if(typeof window!=='undefined' && typeof document!=='undefined')(async()=>{
  try{const response=await fetch('data.json',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);window.CBB_STATIC_DATA=await response.json();}
  catch(e){if(!window.CBB_STATIC_DATA)window.CBB_STATIC_DATA={games:[],teams:[],performance:{},season:'2026–27',generated_at:null};}
  init();
})();

if(typeof module!=='undefined' && module.exports){module.exports={dateKey,availableDates,nextUnstartedGame,isD1,byScope};}
