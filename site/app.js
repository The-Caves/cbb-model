'use strict';
/* Site renders only exported source evidence. It never computes a fabricated pick. */
const els={};let dataset=null;let selectedDate='all';let sortDirection=1;
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
const teamShort=s=>{const chunks=String(s||'').split(/\s+/);const acronym=chunks.length>1?chunks.slice(-2).map(x=>x.slice(0,1)).join(''):chunks[0]?.slice(0,3);return acronym?.toUpperCase()||'—'};
const allGames=()=>dataset?.games||[];
const findTeam=id=>(dataset?.teams||[]).find(t=>t.id===id);
const recFor=id=>{const t=findTeam(id);return t?`${t.wins}–${t.losses} · ${t.games} GP`:'Record unavailable'};
function timeText(g){return g.completed?'FINAL':hourFmt.format(new Date(g.tip_utc));}
function gameCard(g){
  const p=g.projection;
  const real=g.completed && g.home_score!==null && g.away_score!==null;
  const winner=p?(p.margin>0?g.home:(p.margin<0?g.away:'Toss-up')):'—';
  const side=p?esc(winner):'Unavailable';
  const cardStatus=real?'FINAL':new Date(g.tip_utc).getTime()<=Date.now()?'IN PROGRESS / PENDING':'UPCOMING';
  const condition=p?(p.status==='PASS_LOW_SAMPLE'?'LOW SAMPLE':'PROJECTION STORED'):'NO VERIFIED PICK';
  const when=dateFmt.format(new Date(g.tip_utc));
  const outcome=real && p && p.margin!==0?((g.home_score-g.away_score>0)===(p.margin>0)?' · WINNER HIT':' · WINNER MISS'):'';
  const predictionHtml=p?`
  <div class="proj-head"><small>MODEL PROJECTION</small><span class="proj-tag ${p.status==='RESEARCH_ONLY'?'has':''}">${esc(condition)}</span></div>
  <div class="proj-row">
    <div class="proj-val"><small>PROJECTED WINNER</small><strong class="accent" title="${side}">${side}</strong></div>
    <div class="proj-val"><small>MODEL MARGIN</small><strong>${p.margin===0?'EVEN':`${num(Math.abs(p.margin),1)} pts`}</strong></div>
    <div class="proj-val"><small>MODEL TOTAL</small><strong>${num(p.total,1)}</strong></div>
  </div>`:`<div class="proj-head"><small>MODEL PROJECTION</small><span class="proj-tag">NO CAPTURE</span></div><div class="no-proj">${real?'No archived pre-tip prediction for this game.': 'Waiting for a valid model snapshot before tipoff.'}</div>`;
  const inGameScores=real;
  return `<article class="game-card">
    <div class="card-heading"><span>${esc(when)} · ${esc(timeText(g))}${g.neutral?' · NEUTRAL':''}</span><span class="status-chip ${real?'final':p?'ready':'scheduled'}">${esc(cardStatus)}</span></div>
    <div class="team-lines">
      <div class="team-line"><span class="team-crest">${esc(teamShort(g.away))}</span><div class="team-label"><strong title="${esc(g.away)}">${esc(g.away)}</strong><small>AWAY · ${esc(recFor(g.away_id))}</small></div><div class="team-score ${real?'actual':'live-proj'}">${real?num(g.away_score):p?num(p.away,1):'—'}</div></div>
      <div class="team-line"><span class="team-crest">${esc(teamShort(g.home))}</span><div class="team-label"><strong title="${esc(g.home)}">${esc(g.home)}</strong><small>${g.neutral?'NEUTRAL':'HOME'} · ${esc(recFor(g.home_id))}</small></div><div class="team-score ${real?'actual':'live-proj'}">${real?num(g.home_score):p?num(p.home,1):'—'}</div></div>
    </div>
    <div class="projection">${predictionHtml}</div>
    <div class="card-footer"><span>${p?`Frozen ${esc(new Date(p.captured_utc).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}))}`:'Spread and total: not sourced'}</span><span>${esc(outcome.replace(/^ · /,''))||'No verified market odds'}</span></div>
    <details><summary>Matchup details</summary><div class="details-body">
      ${p?`<p><strong>Projected score:</strong> ${esc(g.away)} ${num(p.away,1)} – ${esc(g.home)} ${num(p.home,1)}</p><p><strong>Season sample:</strong> ${num(p.away_games)} away games / ${num(p.home_games)} home games. Model trained on ${num(p.training_games)} historical/current games.</p><p><strong>Forecast recorded:</strong> ${esc(new Date(p.captured_utc).toLocaleString())}, strictly before tipoff. Win probabilities are not yet calibrated.</p>`:'<p>No pre-tip model capture is stored. A game without one is deliberately not given a projected winner.</p>'}
      <p><strong>ATS / Over-under:</strong> Unavailable until genuine timestamped sportsbook lines are captured. No odds or bet recommendation invented.</p>
    </div></details>
   </article>`;
}
function emptyMarkup(title,desc){return `<div class="empty-state"><div class="empty-inner"><div class="empty-icon">◌</div><h3>${esc(title)}</h3><p>${esc(desc)}</p></div></div>`;}
function dateOptions(){
  const now=todayDay();const all=allGames().map(g=>isoDay(g.tip_utc));const unique=[...new Set(all)].sort();
  if(!unique.includes(selectedDate) && selectedDate!=='all')selectedDate='all';
  const pills=[{key:'all',primary:'All',secondary:'DATES'},...unique.map(k=>({key:k,primary:new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric'}).format(new Date(k+'T12:00:00')),secondary:k===now?'TODAY':new Intl.DateTimeFormat(undefined,{weekday:'short'}).format(new Date(k+'T12:00:00'))}))];
  byId('datePills').innerHTML=pills.map(({key,primary,secondary})=>`<button type="button" data-day="${esc(key)}" class="date-pill ${selectedDate===key?'active':''}" aria-pressed="${selectedDate===key}"><small>${esc(secondary)}</small>${esc(primary)}</button>`).join('');
  byId('datePills').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{selectedDate=b.dataset.day;dateOptions();renderGames();}));
}
function shiftDay(step){const opts=['all',...new Set(allGames().map(g=>isoDay(g.tip_utc)))].sort((a,b)=>a==='all'?-1:b==='all'?1:a.localeCompare(b));let next=opts.indexOf(selectedDate)+step;if(next<0)next=opts.length-1;if(next>=opts.length)next=0;selectedDate=opts[next];dateOptions();renderGames();}
function renderGames(){
  const search=byId('gameSearch').value.trim().toLocaleLowerCase();const filter=byId('gameFilter').value;
  const now=Date.now();const filtered=allGames().filter(g=>(selectedDate==='all'||isoDay(g.tip_utc)===selectedDate)
    &&(!search||`${g.home} ${g.away}`.toLocaleLowerCase().includes(search))
    &&(filter==='all'||filter==='predicted'&&!!g.projection||filter==='completed'&&g.completed||filter==='upcoming'&&!g.completed));
  filtered.sort((a,b)=>a.tip_utc.localeCompare(b.tip_utc)*sortDirection);
  byId('gameCount').textContent=`(${filtered.length})`;
  byId('dayStamp').textContent=selectedDate==='all'?'All dates':dateFmt.format(new Date(selectedDate+'T12:00:00'));
  byId('gameCards').innerHTML=filtered.length?filtered.map(gameCard).join(''):emptyMarkup(allGames().length?'No matchups match these filters':'Awaiting the first live data sync',allGames().length?'Try another day, team, or game status.':'This site is ready, but no real game data or model picks have been imported yet. Once the free GitHub workflow completes, games and actual model projections will populate automatically.');
  const future=allGames().filter(g=>!g.completed && new Date(g.tip_utc).getTime()>now);
  const withp=future.filter(g=>!!g.projection);
  byId('heroCount').textContent=num(withp.length);
  byId('heroCoverage').textContent=future.length?`${withp.length} of ${future.length} upcoming games`:'Waiting for upcoming games';
  byId('kpiGames').textContent=num(allGames().length);
  byId('kpiForecasts').textContent=num(allGames().filter(g=>!!g.projection).length);
  byId('kpiGraded').textContent=num(dataset?.performance?.prospective?.all_games?.n||0);
}
function perfStats(items){return `<div class="performance-stats">${items.map(x=>`<div class="stat-panel"><div class="stat-title">${esc(x.label)}</div><div class="stat-value">${esc(x.value)}</div><div class="stat-sub">${esc(x.sub)}</div></div>`).join('')}</div>`;}
function metricRow(r){if(!r)return '';return `<div class="metric-row"><div class="metric-item"><small>Winner accuracy</small><strong>${nicePct(r.su_accuracy)}</strong></div><div class="metric-item"><small>Margin MAE</small><strong>${r.margin_mae==null?'—':num(r.margin_mae,1)+' pts'}</strong></div><div class="metric-item"><small>Total MAE</small><strong>${r.total_mae==null?'—':num(r.total_mae,1)+' pts'}</strong></div><div class="metric-item"><small>Games graded</small><strong>${num(r.n)}</strong></div></div>`;}
function renderPerformance(){
  const p=dataset.performance||{};const live=p.prospective?.all_games||{};const hist=p.retrospective_2025_26;
  const all=p.prospective?.all_games?.n||0;
  let out=perfStats([{label:'Frozen games graded',value:num(all),sub:'True pre-tip prediction ledger'},{label:'Winner accuracy',value:nicePct(live.su_accuracy),sub:'Not an ATS cover rate'},{label:'Mean margin error',value:live.margin_mae==null?'—':`${num(live.margin_mae,1)} pts`,sub:'Prediction vs. final margin'}]);
  out+=`<section class="data-panel"><h3>2026–27 prospective performance</h3><p>Grades the last archived forecast captured before tipoff against independently ingested final scores. No completed game is added without an earlier forecast.</p>${metricRow(live)}${!all?`<p style="margin-top:16px;color:var(--orange)">No qualified completed forecast is available yet. The performance table will populate after authentic pre-tip captures and final scores.</p>`:''}</section>`;
  if(hist){const h=hist.all_games;const established=hist.established_teams;
    out+=`<section class="data-panel"><h3>2025–26 historical walk-forward backtest</h3><p>Uses scores from dates before each game to reconstruct prediction inputs. Historical score data was downloaded retrospectively; this is not the same as pre-tip prospective validation.</p>${metricRow(h)}<div class="metric-row"><div class="metric-item"><small>Established teams · Winner accuracy</small><strong>${nicePct(established?.su_accuracy)}</strong></div><div class="metric-item"><small>Baseline winner accuracy</small><strong>${nicePct(hist.simple_baseline?.su_accuracy)}</strong></div><div class="metric-item"><small>Baseline margin MAE</small><strong>${hist.simple_baseline?.margin_mae==null?'—':num(hist.simple_baseline.margin_mae,1)+' pts'}</strong></div></div></section>`;
  }else{out+=`<section class="data-panel"><h3>Historical walk-forward backtest</h3><p>Not yet populated. The 2025–26 backtest will be displayed when real completed game scores are downloaded and the historical replay finishes.</p></section>`;}
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
  selectedDate='all'; dateOptions();renderGames();
  byId('prevDay').addEventListener('click',()=>shiftDay(-1));byId('nextDay').addEventListener('click',()=>shiftDay(1));
  byId('gameSearch').addEventListener('input',renderGames);byId('gameFilter').addEventListener('change',renderGames);
  byId('teamSearch').addEventListener('input',renderTeams);
  document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>tab(b.dataset.tab)));
  const key=location.hash.replace('#','');tab(['games','teams','performance','model'].includes(key)?key:'games');
  window.addEventListener('hashchange',()=>{const tabName=location.hash.replace('#','');if(['games','teams','performance','model'].includes(tabName))tab(tabName)});
}
(async()=>{
  try{const response=await fetch('data.json',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);window.CBB_STATIC_DATA=await response.json();}
  catch(e){if(!window.CBB_STATIC_DATA)window.CBB_STATIC_DATA={games:[],teams:[],performance:{},season:'2026–27',generated_at:null};}
  init();
})();
