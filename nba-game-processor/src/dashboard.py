"""HTML dashboard template served at /dashboard/{game_id}."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NBA Live · {game_id}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:        #06080f;
    --surface:   #0c1018;
    --surface2:  #131921;
    --surface3:  #1a2030;
    --border:    #1e2738;
    --border2:   #28334a;
    --text:      #e8edf5;
    --muted:     #5a6a80;
    --muted2:    #3d4f63;
    --home:      #3b82f6;
    --home-soft: rgba(59,130,246,0.12);
    --away:      #f43f5e;
    --away-soft: rgba(244,63,94,0.12);
    --green:     #10b981;
    --yellow:    #f59e0b;
    --red:       #ef4444;
    --radius:    16px;
  }}

  html {{ scroll-behavior: smooth; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
    overflow-x: hidden;
  }}

  /* Ambient glows */
  .glow-home {{
    position: fixed; top: -200px; left: -150px;
    width: 600px; height: 600px;
    background: radial-gradient(ellipse, rgba(59,130,246,0.07) 0%, transparent 65%);
    pointer-events: none; z-index: 0;
  }}
  .glow-away {{
    position: fixed; bottom: -200px; right: -150px;
    width: 600px; height: 600px;
    background: radial-gradient(ellipse, rgba(244,63,94,0.07) 0%, transparent 65%);
    pointer-events: none; z-index: 0;
  }}

  .page {{ position: relative; z-index: 1; max-width: 900px; margin: 0 auto; padding: 20px 16px 60px; }}

  /* ── Top bar ── */
  .topbar {{
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 24px;
  }}
  .brand {{ font-size: 10px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; color: var(--muted); }}
  .live-pill {{
    display: flex; align-items: center; gap: 6px;
    background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.25);
    border-radius: 100px; padding: 4px 12px;
    font-size: 10px; font-weight: 700; letter-spacing: 2px; color: #ef4444; text-transform: uppercase;
  }}
  .live-dot {{ width: 6px; height: 6px; background: #ef4444; border-radius: 50%; animation: livepulse 1.4s ease-in-out infinite; }}
  .live-pill.final {{ background: rgba(90,106,128,0.08); border-color: rgba(90,106,128,0.25); color: var(--muted); }}
  .live-pill.final .live-dot {{ background: var(--muted); animation: none; }}
  @keyframes livepulse {{ 0%,100% {{ opacity:1; transform:scale(1); }} 50% {{ opacity:0.3; transform:scale(0.75); }} }}

  /* ── Scoreboard ── */
  .scoreboard {{
    background: linear-gradient(160deg, #0f1520 0%, #0c1018 100%);
    border: 1px solid var(--border); border-radius: 24px;
    padding: 32px 28px 24px; margin-bottom: 14px; position: relative; overflow: hidden;
  }}
  .scoreboard::after {{
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.05), transparent);
  }}

  .teams {{ display: grid; grid-template-columns: 1fr 160px 1fr; align-items: center; gap: 8px; margin-bottom: 28px; }}

  .team {{ text-align: center; }}
  .team-tricode {{ font-size: 11px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; margin-bottom: 8px; }}
  .team-tricode.home {{ color: var(--home); }}
  .team-tricode.away {{ color: var(--away); }}
  .score {{
    font-size: 76px; font-weight: 800; line-height: 1; letter-spacing: -4px;
    font-variant-numeric: tabular-nums; color: #fff;
    transition: transform 0.15s ease, color 0.3s ease;
  }}
  .score.bump {{ animation: scorebump 0.3s ease; }}
  @keyframes scorebump {{ 0% {{ transform: scale(1); }} 50% {{ transform: scale(1.08); color: #60a5fa; }} 100% {{ transform: scale(1); }} }}
  .score.away.bump {{ animation: scorebump-away 0.3s ease; }}
  @keyframes scorebump-away {{ 0% {{ transform: scale(1); }} 50% {{ transform: scale(1.08); color: #fb7185; }} 100% {{ transform: scale(1); }} }}

  .center-col {{ text-align: center; }}
  .period-label {{ font-size: 10px; font-weight: 700; letter-spacing: 2.5px; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }}
  .clock-display {{ font-family: 'JetBrains Mono', monospace; font-size: 28px; font-weight: 600; color: var(--text); letter-spacing: 1px; }}
  .score-diff-badge {{
    display: inline-block; margin-top: 8px; padding: 2px 10px;
    border-radius: 100px; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;
  }}
  .score-diff-badge.home-lead {{ background: var(--home-soft); color: #93c5fd; }}
  .score-diff-badge.away-lead {{ background: var(--away-soft); color: #fda4af; }}
  .score-diff-badge.tied {{ background: rgba(255,255,255,0.04); color: var(--muted); }}

  .possession-row {{
    display: flex; justify-content: center; align-items: center; gap: 6px; margin-top: 10px;
    font-size: 10px; font-weight: 600; letter-spacing: 1.5px; text-transform: uppercase; color: var(--muted2);
  }}
  .poss-arrow {{ font-size: 13px; transition: color 0.4s ease; }}
  .poss-home {{ color: var(--home); }}
  .poss-away {{ color: var(--away); }}

  /* ── Win Probability Bar ── */
  .wp-section {{ margin-top: 4px; }}
  .wp-header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }}
  .wp-pct {{ font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; transition: all 0.6s ease; }}
  .wp-pct.home {{ color: var(--home); }}
  .wp-pct.away {{ color: var(--away); }}
  .wp-label {{ font-size: 10px; font-weight: 600; letter-spacing: 2px; text-transform: uppercase; color: var(--muted); }}

  .wp-track {{ height: 10px; background: var(--surface3); border-radius: 100px; overflow: hidden; position: relative; }}
  .wp-fill-home {{
    position: absolute; left: 0; top: 0; bottom: 0;
    background: linear-gradient(90deg, #1d4ed8, #3b82f6);
    border-radius: 100px; transition: width 1s cubic-bezier(0.4,0,0.2,1);
  }}
  .wp-fill-away {{
    position: absolute; right: 0; top: 0; bottom: 0;
    background: linear-gradient(270deg, #be123c, #f43f5e);
    border-radius: 100px; transition: width 1s cubic-bezier(0.4,0,0.2,1);
  }}
  .wp-mid {{ position: absolute; left: 50%; top: -2px; bottom: -2px; width: 2px; background: var(--bg); transform: translateX(-50%); z-index: 2; }}

  /* ── Two-column layout ── */
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }}
  .three-col {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; margin-bottom: 14px; }}

  /* ── Card base ── */
  .card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px;
    transition: border-color 0.2s;
  }}
  .card:hover {{ border-color: var(--border2); }}
  .card-title {{
    font-size: 9px; font-weight: 700; letter-spacing: 2.5px;
    text-transform: uppercase; color: var(--muted); margin-bottom: 14px;
  }}
  .stat-value {{ font-size: 28px; font-weight: 700; color: var(--text); line-height: 1; margin-bottom: 4px; font-variant-numeric: tabular-nums; }}
  .stat-sub {{ font-size: 11px; color: var(--muted); }}
  .stat-sub.up {{ color: var(--green); }}
  .stat-sub.down {{ color: var(--red); }}

  /* ── WP Chart ── */
  .chart-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; margin-bottom: 14px; }}
  .chart-wrap {{ position: relative; height: 150px; }}

  /* ── Momentum ── */
  .momentum-bars {{ display: flex; gap: 5px; align-items: flex-end; height: 52px; margin: 12px 0 10px; }}
  .m-bar {{ flex: 1; border-radius: 4px 4px 0 0; min-height: 5px; transition: height 0.4s ease; }}
  .m-bar.home_score {{ background: var(--home); }}
  .m-bar.away_score {{ background: var(--away); }}
  .m-bar.turnover   {{ background: var(--surface3); border: 1px solid var(--border2); }}
  .m-bar.other      {{ background: var(--surface3); border: 1px solid var(--border); min-height: 4px; }}
  .momentum-status {{ font-size: 12px; font-weight: 500; color: var(--muted); }}
  .momentum-status.home-run {{ color: var(--home); font-weight: 600; }}
  .momentum-status.away-run {{ color: var(--away); font-weight: 600; }}
  .momentum-legend {{ display: flex; gap: 14px; margin-top: 10px; }}
  .legend-item {{ display: flex; align-items: center; gap: 5px; font-size: 10px; color: var(--muted); }}
  .legend-dot {{ width: 8px; height: 8px; border-radius: 2px; }}

  /* ── Play-by-play feed ── */
  .feed-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; margin-bottom: 14px; }}
  .feed-list {{ display: flex; flex-direction: column; gap: 2px; max-height: 220px; overflow-y: auto; }}
  .feed-list::-webkit-scrollbar {{ width: 4px; }}
  .feed-list::-webkit-scrollbar-track {{ background: transparent; }}
  .feed-list::-webkit-scrollbar-thumb {{ background: var(--border2); border-radius: 4px; }}
  .feed-item {{
    display: grid; grid-template-columns: 48px 52px 1fr;
    gap: 8px; align-items: center; padding: 7px 10px;
    border-radius: 8px; font-size: 12px;
    transition: background 0.2s;
  }}
  .feed-item:hover {{ background: var(--surface2); }}
  .feed-item.score-event {{ background: rgba(59,130,246,0.04); }}
  .feed-item.score-event.away-ev {{ background: rgba(244,63,94,0.04); }}
  .feed-clock {{ font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--muted2); }}
  .feed-team {{
    font-size: 10px; font-weight: 700; letter-spacing: 1px;
    padding: 2px 6px; border-radius: 4px; text-align: center;
    text-transform: uppercase;
  }}
  .feed-team.home {{ background: var(--home-soft); color: #93c5fd; }}
  .feed-team.away {{ background: var(--away-soft); color: #fda4af; }}
  .feed-team.none {{ background: transparent; color: transparent; }}
  .feed-desc {{ color: var(--text); font-size: 12px; line-height: 1.4; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .feed-empty {{ font-size: 12px; color: var(--muted2); text-align: center; padding: 20px 0; }}

  /* ── Foul trouble ── */
  .foul-list {{ display: flex; flex-direction: column; gap: 7px; }}
  .foul-row {{ display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; border-radius: 10px; background: var(--surface2); }}
  .foul-name {{ font-size: 13px; font-weight: 500; }}
  .foul-badge {{ font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 6px; letter-spacing: 0.5px; text-transform: uppercase; }}
  .foul-badge.danger  {{ background: rgba(239,68,68,0.12); color: #f87171; border: 1px solid rgba(239,68,68,0.25); }}
  .foul-badge.warning {{ background: rgba(245,158,11,0.10); color: #fbbf24; border: 1px solid rgba(245,158,11,0.25); }}
  .foul-empty {{ font-size: 12px; color: var(--muted2); text-align: center; padding: 16px 0; }}

  /* ── On court ── */
  .oncourt-section {{ margin-top: 10px; }}
  .oncourt-label {{ font-size: 9px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 6px; }}
  .oncourt-label.home {{ color: var(--home); }}
  .oncourt-label.away {{ color: var(--away); }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 10px; }}
  .chip {{ font-size: 11px; font-weight: 500; padding: 4px 9px; border-radius: 6px; }}
  .chip.home {{ background: rgba(59,130,246,0.1); color: #93c5fd; border: 1px solid rgba(59,130,246,0.2); }}
  .chip.away {{ background: rgba(244,63,94,0.1); color: #fda4af; border: 1px solid rgba(244,63,94,0.2); }}

  /* ── Footer ── */
  .footer {{ text-align: center; margin-top: 32px; font-size: 11px; color: var(--muted2); }}

  /* ── Loading ── */
  .loading-state {{ text-align: center; padding: 100px 20px; color: var(--muted); }}
  .spinner {{ width: 36px; height: 36px; border: 2px solid var(--border); border-top-color: var(--home); border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 20px; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

  @media (max-width: 600px) {{
    .teams {{ grid-template-columns: 1fr 120px 1fr; }}
    .score {{ font-size: 52px; }}
    .two-col, .three-col {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="glow-home"></div>
<div class="glow-away"></div>

<div class="page">
  <div class="topbar">
    <div class="brand">NBA Game Processor</div>
    <div class="live-pill" id="live-pill"><div class="live-dot"></div><span id="live-text">LIVE</span></div>
  </div>

  <div id="root">
    <div class="loading-state">
      <div class="spinner"></div>
      <div style="font-size:15px;font-weight:500;margin-bottom:6px">Connecting to live feed…</div>
      <div style="font-size:12px;color:var(--muted2)">Start docker compose up, then run: python -m src.replay --game {game_id} --speed 20</div>
    </div>
  </div>

  <div class="footer">
    Game <span style="font-family:monospace;opacity:0.6">{game_id}</span>
    &nbsp;·&nbsp; <span id="update-time">—</span>
  </div>
</div>

<script>
const GAME_ID = "{game_id}";
let wpChart = null;
let wpHistory = [];
let prevHomeScore = null;
let prevAwayScore = null;

function periodLabel(p) {{ return p <= 4 ? "Q" + p : "OT" + (p - 4); }}

function possHTML(possession, home, away) {{
  if (!possession) return '<span class="poss-arrow">·</span><span>Possession</span>';
  const isHome = possession === "home";
  const cls = isHome ? "poss-home" : "poss-away";
  const team = isHome ? home : away;
  return `<span class="poss-arrow ${{cls}}">▶</span><span style="color:var(--${{isHome?'home':'away'}})">${{team}}</span>`;
}}

function buildChart(ctx) {{
  return new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: [],
      datasets: [
        {{ label: 'Home', data: [], borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.07)', borderWidth: 2, pointRadius: 0, tension: 0.45, fill: true }},
        {{ label: 'Away', data: [], borderColor: '#f43f5e', backgroundColor: 'rgba(244,63,94,0.05)', borderWidth: 2, pointRadius: 0, tension: 0.45, fill: false }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false, animation: {{ duration: 350 }},
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ display: false }},
        y: {{
          min: 0, max: 100,
          grid: {{ color: 'rgba(255,255,255,0.03)' }},
          ticks: {{ color: '#3d4f63', font: {{ size: 10 }}, callback: v => v + '%', maxTicksLimit: 5 }},
          border: {{ display: false }},
        }}
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#131921', borderColor: '#1e2738', borderWidth: 1,
          titleColor: '#5a6a80', bodyColor: '#e8edf5', padding: 10,
          callbacks: {{ label: c => ' ' + c.dataset.label + ': ' + c.parsed.y.toFixed(1) + '%' }}
        }}
      }}
    }}
  }});
}}

function eventIcon(type) {{
  const t = (type || "").toLowerCase();
  if (t === "score") return "🏀";
  if (t === "missed shot") return "·";
  if (t === "rebound") return "↩";
  if (t === "turnover") return "↔";
  if (t === "foul") return "✋";
  if (t === "substitution") return "⇄";
  if (t === "period start") return "▶";
  if (t === "end of game") return "🏁";
  return "·";
}}

function renderFeed(events, home, away) {{
  if (!events || !events.length) return '<div class="feed-empty">No plays yet</div>';
  return events.slice(0, 15).map(ev => {{
    const f = ev.fields || {{}};
    const isScore = f.event_type === "score";
    const teamTricode = f.team || "";
    const isHome = teamTricode === home;
    const isAway = teamTricode === away;
    const teamCls = isHome ? "home" : isAway ? "away" : "none";
    const rowCls = isScore ? (isHome ? "score-event" : "score-event away-ev") : "";
    const desc = f.description || f.event_type || "—";
    const period = f.period ? "Q" + f.period : "";
    const clock = f.clock || "";
    const clockStr = period + " " + clock;
    return `<div class="feed-item ${{rowCls}}">
      <span class="feed-clock">${{clockStr}}</span>
      <span class="feed-team ${{teamCls}}">${{teamTricode || "—"}}</span>
      <span class="feed-desc">${{eventIcon(f.event_type)}} ${{desc}}</span>
    </div>`;
  }}).join("");
}}

function render(state, momentum, wp, foul, events) {{
  const home = state.home_team || "HOME";
  const away = state.away_team || "AWAY";
  const isFinal = state.game_status === "final";
  const homeWp = wp.home_win_probability ?? 0.5;
  const homePct = Math.round(homeWp * 100);
  const awayPct = 100 - homePct;
  const diff = state.home_score - state.away_score;
  const absDiff = Math.abs(diff);
  const paceDiff = (state.pace - 100).toFixed(1);

  // Live badge
  const pill = document.getElementById("live-pill");
  document.getElementById("live-text").textContent = isFinal ? "FINAL" : "LIVE";
  pill.className = "live-pill" + (isFinal ? " final" : "");

  // Score bump animation
  let homeBump = "", awayBump = "";
  if (prevHomeScore !== null && state.home_score > prevHomeScore) homeBump = "bump";
  if (prevAwayScore !== null && state.away_score > prevAwayScore) awayBump = "bump";
  prevHomeScore = state.home_score;
  prevAwayScore = state.away_score;

  // WP history
  const timeLabel = periodLabel(state.period) + " " + (state.clock || "");
  if (!wpHistory.length || wpHistory[wpHistory.length-1].v !== homeWp) {{
    wpHistory.push({{ l: timeLabel, v: homeWp }});
    if (wpHistory.length > 250) wpHistory.shift();
  }}

  // Score diff badge
  let diffBadgeHTML = "";
  if (diff === 0) diffBadgeHTML = `<div class="score-diff-badge tied">Tied</div>`;
  else if (diff > 0) diffBadgeHTML = `<div class="score-diff-badge home-lead">${{home}} +${{absDiff}}</div>`;
  else diffBadgeHTML = `<div class="score-diff-badge away-lead">${{away}} +${{absDiff}}</div>`;

  // Momentum
  const poss = momentum.last_10_possessions || [];
  const barsHTML = poss.length
    ? poss.map(p => `<div class="m-bar ${{p}}" style="height:${{p==='home_score'||p==='away_score'?'100%':'28%'}}"></div>`).join("")
    : '<span style="font-size:12px;color:var(--muted2)">Waiting…</span>';
  const hc = poss.filter(p=>p==="home_score").length;
  const ac = poss.filter(p=>p==="away_score").length;
  let momStatus = `Contested · ${{hc}}-${{ac}} scoring last 10`;
  let momCls = "";
  if (hc >= ac + 2) {{ momStatus = `${{home}} on a run — ${{hc}}-${{ac}} last 10`; momCls = "home-run"; }}
  else if (ac >= hc + 2) {{ momStatus = `${{away}} on a run — ${{ac}}-${{hc}} last 10`; momCls = "away-run"; }}

  // Foul trouble
  const allFouls = foul.all_player_fouls || {{}};
  const troublePlayers = Object.entries(allFouls).filter(([,f])=>f>=4).sort((a,b)=>b[1]-a[1]);
  const foulHTML = troublePlayers.length
    ? '<div class="foul-list">' + troublePlayers.map(([n,f]) =>
        `<div class="foul-row"><span class="foul-name">${{n}}</span><span class="foul-badge ${{f>=5?'danger':'warning'}}">${{f>=6?'OUT':f+' PF'}}</span></div>`
      ).join("") + "</div>"
    : '<div class="foul-empty">No foul trouble</div>';

  // On court
  const hp = state.home_players_on_court || [];
  const ap = state.away_players_on_court || [];
  const rosterHTML = (hp.length || ap.length) ? `
    ${{hp.length ? `<div class="oncourt-section"><div class="oncourt-label home">${{home}}</div><div class="chips">${{hp.map(p=>`<span class="chip home">${{p}}</span>`).join("")}}</div></div>` : ''}}
    ${{ap.length ? `<div class="oncourt-section"><div class="oncourt-label away">${{away}}</div><div class="chips">${{ap.map(p=>`<span class="chip away">${{p}}</span>`).join("")}}</div></div>` : ''}}
  ` : '<div class="foul-empty">No lineup data yet</div>';

  // Feed
  const feedItems = events ? renderFeed(events.events || [], home, away) : '<div class="feed-empty">No events yet</div>';

  document.getElementById("root").innerHTML = `
    <div class="scoreboard">
      <div class="teams">
        <div class="team">
          <div class="team-tricode home">${{home}}</div>
          <div class="score home ${{homeBump}}" id="home-score">${{state.home_score}}</div>
        </div>
        <div class="center-col">
          <div class="period-label">${{periodLabel(state.period)}}</div>
          <div class="clock-display">${{state.clock || "—"}}</div>
          ${{diffBadgeHTML}}
          <div class="possession-row">${{possHTML(state.current_possession, home, away)}}</div>
        </div>
        <div class="team">
          <div class="team-tricode away">${{away}}</div>
          <div class="score away ${{awayBump}}" id="away-score">${{state.away_score}}</div>
        </div>
      </div>
      <div class="wp-section">
        <div class="wp-header">
          <div class="wp-pct home">${{homePct}}%</div>
          <div class="wp-label">Win Probability</div>
          <div class="wp-pct away">${{awayPct}}%</div>
        </div>
        <div class="wp-track">
          <div class="wp-fill-home" style="width:${{homePct}}%"></div>
          <div class="wp-fill-away" style="width:${{awayPct}}%"></div>
          <div class="wp-mid"></div>
        </div>
      </div>
    </div>

    <div class="chart-card">
      <div class="card-title">Win Probability — Game Timeline</div>
      <div class="chart-wrap"><canvas id="wp-canvas"></canvas></div>
    </div>

    <div class="three-col">
      <div class="card">
        <div class="card-title">Pace</div>
        <div class="stat-value">${{state.pace.toFixed(1)}}</div>
        <div class="stat-sub ${{+paceDiff>=0?'up':'down'}}">${{+paceDiff>=0?'+':''}}${{paceDiff}} vs avg</div>
      </div>
      <div class="card">
        <div class="card-title">Possessions</div>
        <div class="stat-value">${{state.home_possessions + state.away_possessions}}</div>
        <div class="stat-sub">${{home}} ${{state.home_possessions}} · ${{away}} ${{state.away_possessions}}</div>
      </div>
      <div class="card">
        <div class="card-title">Minutes Left</div>
        <div class="stat-value">${{Math.max(48 - (state.minutes_elapsed||0), 0).toFixed(0)}}</div>
        <div class="stat-sub">${{(state.minutes_elapsed||0).toFixed(1)}} elapsed</div>
      </div>
    </div>

    <div class="two-col">
      <div class="card">
        <div class="card-title">Momentum — Last 10 Possessions</div>
        <div class="momentum-bars">${{barsHTML}}</div>
        <div class="momentum-status ${{momCls}}">${{momStatus}}</div>
        <div class="momentum-legend">
          <div class="legend-item"><div class="legend-dot" style="background:var(--home)"></div>${{home}}</div>
          <div class="legend-item"><div class="legend-dot" style="background:var(--away)"></div>${{away}}</div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Foul Trouble</div>
        ${{foulHTML}}
      </div>
    </div>

    <div class="feed-card">
      <div class="card-title">Play-by-Play Feed</div>
      <div class="feed-list">${{feedItems}}</div>
    </div>

    <div class="card">
      <div class="card-title">On Court</div>
      ${{rosterHTML}}
    </div>
  `;

  // Chart update
  const canvas = document.getElementById("wp-canvas");
  if (canvas) {{
    if (!wpChart) wpChart = buildChart(canvas.getContext("2d"));
    wpChart.data.labels = wpHistory.map(d => d.l);
    wpChart.data.datasets[0].data = wpHistory.map(d => +(d.v * 100).toFixed(1));
    wpChart.data.datasets[1].data = wpHistory.map(d => +((1 - d.v) * 100).toFixed(1));
    wpChart.update('none');
  }}

  document.getElementById("update-time").textContent = "Updated " + new Date().toLocaleTimeString();
}}

async function fetchAll() {{
  try {{
    const [sR, mR, wR, fR, eR] = await Promise.all([
      fetch("/game/" + GAME_ID + "/state"),
      fetch("/game/" + GAME_ID + "/momentum"),
      fetch("/game/" + GAME_ID + "/win-probability"),
      fetch("/game/" + GAME_ID + "/foul-trouble"),
      fetch("/game/" + GAME_ID + "/events?limit=15"),
    ]);
    if (!sR.ok) return;
    const [state, momentum, wp, foul, events] = await Promise.all([
      sR.json(), mR.json(), wR.json(), fR.json(), eR.ok ? eR.json() : Promise.resolve(null)
    ]);
    render(state, momentum, wp, foul, events);
  }} catch(e) {{ console.error(e); }}
}}

fetchAll();
setInterval(fetchAll, 2000);
</script>
</body>
</html>"""
