"""HTML dashboard template served at /dashboard/{game_id}."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NBA Live · {game_id}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;600&display=swap');

  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg:        #080b14;
    --surface:   #0d1117;
    --surface2:  #161b27;
    --border:    #1e2535;
    --border2:   #252d3d;
    --text:      #e2e8f0;
    --muted:     #64748b;
    --muted2:    #475569;
    --home:      #3b82f6;
    --home-dim:  #1d3461;
    --home-glow: rgba(59,130,246,0.15);
    --away:      #f43f5e;
    --away-dim:  #4c1528;
    --away-glow: rgba(244,63,94,0.15);
    --green:     #10b981;
    --yellow:    #f59e0b;
    --red:       #ef4444;
  }}

  html {{ scroll-behavior: smooth; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', -apple-system, sans-serif;
    min-height: 100vh;
    padding: 0;
    overflow-x: hidden;
  }}

  /* Ambient background */
  body::before {{
    content: '';
    position: fixed;
    top: -30%;
    left: -10%;
    width: 60%;
    height: 60%;
    background: radial-gradient(ellipse, rgba(59,130,246,0.06) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }}
  body::after {{
    content: '';
    position: fixed;
    bottom: -30%;
    right: -10%;
    width: 60%;
    height: 60%;
    background: radial-gradient(ellipse, rgba(244,63,94,0.06) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }}

  .page {{ position: relative; z-index: 1; max-width: 820px; margin: 0 auto; padding: 24px 16px 48px; }}

  /* ── Top bar ── */
  .topbar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 28px;
  }}
  .brand {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--muted);
  }}
  .live-pill {{
    display: flex;
    align-items: center;
    gap: 6px;
    background: rgba(239,68,68,0.1);
    border: 1px solid rgba(239,68,68,0.3);
    border-radius: 100px;
    padding: 4px 12px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
    color: #ef4444;
    text-transform: uppercase;
  }}
  .live-dot {{
    width: 6px; height: 6px;
    background: #ef4444;
    border-radius: 50%;
    animation: livepulse 1.4s ease-in-out infinite;
  }}
  .live-pill.final {{
    background: rgba(100,116,139,0.1);
    border-color: rgba(100,116,139,0.3);
    color: var(--muted);
  }}
  .live-pill.final .live-dot {{
    background: var(--muted);
    animation: none;
  }}
  @keyframes livepulse {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50% {{ opacity: 0.4; transform: scale(0.8); }}
  }}

  /* ── Scoreboard ── */
  .scoreboard {{
    background: linear-gradient(145deg, #0f1623 0%, #0d1117 100%);
    border: 1px solid var(--border);
    border-radius: 24px;
    padding: 36px 32px 28px;
    margin-bottom: 16px;
    position: relative;
    overflow: hidden;
  }}
  .scoreboard::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.06), transparent);
  }}

  .teams {{
    display: grid;
    grid-template-columns: 1fr 180px 1fr;
    align-items: center;
    gap: 8px;
    margin-bottom: 32px;
  }}

  .team {{ text-align: center; }}
  .team-tricode {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    margin-bottom: 10px;
  }}
  .team-tricode.home {{ color: var(--home); }}
  .team-tricode.away {{ color: var(--away); }}
  .score {{
    font-size: 72px;
    font-weight: 800;
    line-height: 1;
    letter-spacing: -3px;
    font-variant-numeric: tabular-nums;
    transition: all 0.3s ease;
  }}
  .score.home {{ color: #fff; }}
  .score.away {{ color: #fff; }}
  .score.leading {{ text-shadow: 0 0 40px rgba(255,255,255,0.15); }}

  .center-info {{ text-align: center; }}
  .period-label {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 6px;
  }}
  .clock-display {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 26px;
    font-weight: 600;
    color: var(--text);
    letter-spacing: 1px;
    margin-bottom: 6px;
  }}
  .possession-row {{
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 6px;
    margin-top: 4px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--muted2);
  }}
  .poss-arrow {{
    font-size: 14px;
    transition: color 0.4s ease;
  }}
  .poss-home {{ color: var(--home); }}
  .poss-away {{ color: var(--away); }}

  /* ── Win Probability ── */
  .wp-section {{ margin-top: 4px; }}
  .wp-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 10px;
  }}
  .wp-team-pct {{
    font-size: 22px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    transition: all 0.6s ease;
  }}
  .wp-team-pct.home {{ color: var(--home); }}
  .wp-team-pct.away {{ color: var(--away); }}
  .wp-center-label {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--muted);
  }}

  .wp-bar-track {{
    height: 10px;
    background: var(--surface2);
    border-radius: 100px;
    overflow: hidden;
    position: relative;
  }}
  .wp-bar-home {{
    position: absolute;
    left: 0; top: 0; bottom: 0;
    background: linear-gradient(90deg, #1d4ed8, var(--home));
    border-radius: 100px;
    transition: width 1s cubic-bezier(0.4, 0, 0.2, 1);
  }}
  .wp-bar-away {{
    position: absolute;
    right: 0; top: 0; bottom: 0;
    background: linear-gradient(270deg, #be123c, var(--away));
    border-radius: 100px;
    transition: width 1s cubic-bezier(0.4, 0, 0.2, 1);
  }}
  .wp-divider {{
    position: absolute;
    left: 50%;
    top: -2px; bottom: -2px;
    width: 2px;
    background: var(--bg);
    transform: translateX(-50%);
    z-index: 2;
  }}

  /* ── Chart ── */
  .chart-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 20px 20px 16px;
    margin-bottom: 16px;
  }}
  .card-title {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 16px;
  }}
  .chart-wrap {{ position: relative; height: 140px; }}

  /* ── Stat grid ── */
  .stat-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 12px;
    margin-bottom: 16px;
  }}
  .stat-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 16px 14px;
    transition: border-color 0.3s;
  }}
  .stat-card:hover {{ border-color: var(--border2); }}
  .stat-label {{
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 8px;
  }}
  .stat-value {{
    font-size: 26px;
    font-weight: 700;
    color: var(--text);
    line-height: 1;
    margin-bottom: 4px;
    font-variant-numeric: tabular-nums;
  }}
  .stat-sub {{
    font-size: 10px;
    color: var(--muted);
  }}
  .stat-sub.up {{ color: var(--green); }}
  .stat-sub.down {{ color: var(--red); }}

  /* ── Momentum ── */
  .momentum-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 20px;
    margin-bottom: 16px;
  }}
  .momentum-timeline {{
    display: flex;
    gap: 5px;
    align-items: flex-end;
    height: 48px;
    margin: 14px 0 10px;
  }}
  .m-bar {{
    flex: 1;
    border-radius: 3px 3px 0 0;
    min-height: 6px;
    transition: all 0.4s ease;
    position: relative;
  }}
  .m-bar.home-score {{ background: var(--home); }}
  .m-bar.away-score {{ background: var(--away); }}
  .m-bar.turnover   {{ background: var(--surface2); border: 1px solid var(--border2); }}
  .m-bar.other      {{ background: var(--surface2); border: 1px solid var(--border); }}
  .momentum-status {{
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
    margin-top: 6px;
  }}
  .momentum-status.home-run {{ color: var(--home); font-weight: 600; }}
  .momentum-status.away-run {{ color: var(--away); font-weight: 600; }}
  .momentum-legend {{
    display: flex;
    gap: 16px;
    margin-top: 10px;
  }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 10px;
    color: var(--muted);
  }}
  .legend-dot {{
    width: 8px; height: 8px;
    border-radius: 2px;
  }}

  /* ── Two-col bottom ── */
  .bottom-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 16px;
  }}

  /* ── Foul trouble ── */
  .foul-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 20px;
  }}
  .foul-list {{ margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }}
  .foul-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    border-radius: 10px;
    background: var(--surface2);
  }}
  .foul-name {{ font-size: 13px; font-weight: 500; color: var(--text); }}
  .foul-badge {{
    font-size: 10px;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 6px;
    letter-spacing: 1px;
    text-transform: uppercase;
  }}
  .foul-badge.danger  {{ background: rgba(239,68,68,0.15);  color: #f87171; border: 1px solid rgba(239,68,68,0.3); }}
  .foul-badge.warning {{ background: rgba(245,158,11,0.12); color: #fbbf24; border: 1px solid rgba(245,158,11,0.3); }}
  .foul-empty {{ font-size: 12px; color: var(--muted2); margin-top: 12px; text-align: center; padding: 12px 0; }}

  /* ── On court ── */
  .oncourt-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 20px;
  }}
  .oncourt-section {{ margin-top: 12px; }}
  .oncourt-team-label {{
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 6px;
  }}
  .oncourt-team-label.home {{ color: var(--home); }}
  .oncourt-team-label.away {{ color: var(--away); }}
  .oncourt-chips {{ display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 12px; }}
  .chip {{
    font-size: 11px;
    font-weight: 500;
    padding: 4px 10px;
    border-radius: 6px;
    letter-spacing: 0.3px;
  }}
  .chip.home {{ background: var(--home-dim); color: #93c5fd; border: 1px solid rgba(59,130,246,0.2); }}
  .chip.away {{ background: var(--away-dim); color: #fda4af; border: 1px solid rgba(244,63,94,0.2); }}

  /* ── Footer ── */
  .footer {{
    text-align: center;
    margin-top: 32px;
    font-size: 11px;
    color: var(--muted2);
    letter-spacing: 0.5px;
  }}
  .footer span {{ opacity: 0.5; }}

  /* ── Loading state ── */
  .loading-state {{
    text-align: center;
    padding: 80px 20px;
    color: var(--muted);
  }}
  .spinner {{
    width: 36px; height: 36px;
    border: 2px solid var(--border);
    border-top-color: var(--home);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin: 0 auto 20px;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .loading-title {{ font-size: 15px; font-weight: 500; margin-bottom: 6px; color: var(--muted); }}
  .loading-sub   {{ font-size: 12px; color: var(--muted2); }}

  /* Responsive */
  @media (max-width: 600px) {{
    .teams {{ grid-template-columns: 1fr 120px 1fr; }}
    .score {{ font-size: 52px; }}
    .stat-grid {{ grid-template-columns: 1fr 1fr; }}
    .bottom-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="page">

  <div class="topbar">
    <div class="brand">NBA Game Processor</div>
    <div class="live-pill" id="live-pill">
      <div class="live-dot"></div>
      <span id="live-text">LIVE</span>
    </div>
  </div>

  <div id="root">
    <div class="loading-state">
      <div class="spinner"></div>
      <div class="loading-title">Connecting to live feed…</div>
      <div class="loading-sub">Start the processor and replay to populate data.</div>
    </div>
  </div>

  <div class="footer">
    Game&nbsp;<span style="font-family:monospace">{game_id}</span>
    &nbsp;·&nbsp;
    <span id="update-time">—</span>
  </div>
</div>

<script>
const GAME_ID = "{game_id}";
let wpChart = null;
let wpHistory = [];   // {{time, home}} pairs for the chart

// ── Helpers ──────────────────────────────────────────────────────────────────
function periodLabel(p) {{
  return p <= 4 ? "Q" + p : "OT" + (p - 4);
}}

function possessionHTML(possession, home, away) {{
  if (!possession) return '<span class="poss-arrow">·</span><span>Possession</span>';
  const isHome = possession === "home";
  const team = isHome ? home : away;
  const cls = isHome ? "poss-home" : "poss-away";
  return `<span class="poss-arrow ${{cls}}">▶</span><span style="color:var(--${{isHome ? 'home' : 'away'}})">${{team}}</span>`;
}}

function buildWpChart(ctx) {{
  return new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: [],
      datasets: [
        {{
          label: 'Home Win %',
          data: [],
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59,130,246,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.4,
          fill: 'origin',
        }},
        {{
          label: 'Away Win %',
          data: [],
          borderColor: '#f43f5e',
          backgroundColor: 'rgba(244,63,94,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.4,
          fill: false,
        }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: {{ duration: 400 }},
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{
          display: false,
        }},
        y: {{
          min: 0, max: 100,
          grid: {{ color: 'rgba(255,255,255,0.04)' }},
          ticks: {{
            color: '#475569',
            font: {{ size: 10 }},
            callback: v => v + '%',
            maxTicksLimit: 5,
          }},
          border: {{ display: false }},
        }}
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#161b27',
          borderColor: '#1e2535',
          borderWidth: 1,
          titleColor: '#64748b',
          bodyColor: '#e2e8f0',
          padding: 10,
          callbacks: {{
            label: ctx => ' ' + ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(1) + '%',
          }}
        }}
      }}
    }}
  }});
}}

// ── Render ───────────────────────────────────────────────────────────────────
function render(state, momentum, wp, foul) {{
  const home = state.home_team || "HOME";
  const away = state.away_team || "AWAY";
  const isFinal = state.game_status === "final";
  const homeWp = wp.home_win_probability ?? 0.5;
  const homePct = Math.round(homeWp * 100);
  const awayPct = 100 - homePct;
  const homeLeads = state.home_score > state.away_score;
  const awayLeads = state.away_score > state.home_score;
  const paceDiff = (state.pace - 100.0);

  // Update live badge
  const pill = document.getElementById("live-pill");
  document.getElementById("live-text").textContent = isFinal ? "FINAL" : "LIVE";
  if (isFinal) pill.classList.add("final"); else pill.classList.remove("final");

  // Track WP history for chart
  const timeLabel = periodLabel(state.period) + " " + (state.clock || "");
  if (wpHistory.length === 0 || wpHistory[wpHistory.length-1].home !== homeWp) {{
    wpHistory.push({{ label: timeLabel, home: homeWp }});
    if (wpHistory.length > 200) wpHistory.shift();
  }}

  // Foul trouble HTML
  const allFouls = foul.all_player_fouls || {{}};
  const troublePlayers = Object.entries(allFouls)
    .filter(([,f]) => f >= 4)
    .sort((a,b) => b[1]-a[1]);
  const foulHTML = troublePlayers.length === 0
    ? '<div class="foul-empty">No players in foul trouble</div>'
    : '<div class="foul-list">' + troublePlayers.map(([name, fouls]) => {{
        const cls = fouls >= 5 ? "danger" : "warning";
        const label = fouls >= 6 ? "OUT" : fouls + " PF";
        return `<div class="foul-row"><span class="foul-name">${{name}}</span><span class="foul-badge ${{cls}}">${{label}}</span></div>`;
      }}).join("") + '</div>';

  // Momentum timeline
  const poss = momentum.last_10_possessions || [];
  const maxH = Math.max(...poss.map(p => p === "home_score" ? 1 : 0), 1);
  const barsHTML = poss.map(p => {{
    const h = p === "home_score" ? 100 : p === "away_score" ? 100 : 30;
    return `<div class="m-bar ${{p}}" style="height:${{h}}%"></div>`;
  }}).join("") || '<span style="font-size:12px;color:var(--muted2)">No possessions yet</span>';

  const homeCount = poss.filter(p => p === "home_score").length;
  const awayCount = poss.filter(p => p === "away_score").length;
  let momStatus = "Contested";
  let momClass = "";
  if (homeCount >= awayCount + 2 && homeCount > 0) {{
    momStatus = `${{home}} on a run — ${{homeCount}}-${{awayCount}} last 10`;
    momClass = "home-run";
  }} else if (awayCount >= homeCount + 2 && awayCount > 0) {{
    momStatus = `${{away}} on a run — ${{awayCount}}-${{homeCount}} last 10`;
    momClass = "away-run";
  }} else {{
    momStatus = `Contested · ${{homeCount}}-${{awayCount}} scoring last 10`;
  }}

  // On-court rosters
  const homePlayers = state.home_players_on_court || [];
  const awayPlayers = state.away_players_on_court || [];
  const homeChips = homePlayers.map(p => `<span class="chip home">${{p}}</span>`).join("");
  const awayChips = awayPlayers.map(p => `<span class="chip away">${{p}}</span>`).join("");

  document.getElementById("root").innerHTML = `
    <!-- Scoreboard -->
    <div class="scoreboard">
      <div class="teams">
        <div class="team">
          <div class="team-tricode home">${{home}}</div>
          <div class="score home ${{homeLeads ? 'leading' : ''}}">${{state.home_score}}</div>
        </div>
        <div class="center-info">
          <div class="period-label">${{periodLabel(state.period)}}</div>
          <div class="clock-display">${{state.clock || "—"}}</div>
          <div class="possession-row">${{possessionHTML(state.current_possession, home, away)}}</div>
        </div>
        <div class="team">
          <div class="team-tricode away">${{away}}</div>
          <div class="score away ${{awayLeads ? 'leading' : ''}}">${{state.away_score}}</div>
        </div>
      </div>

      <div class="wp-section">
        <div class="wp-header">
          <div class="wp-team-pct home">${{homePct}}%</div>
          <div class="wp-center-label">Win Probability</div>
          <div class="wp-team-pct away">${{awayPct}}%</div>
        </div>
        <div class="wp-bar-track">
          <div class="wp-bar-home" style="width:${{homePct}}%"></div>
          <div class="wp-bar-away" style="width:${{awayPct}}%"></div>
          <div class="wp-divider"></div>
        </div>
      </div>
    </div>

    <!-- WP Chart -->
    <div class="chart-card">
      <div class="card-title">Win Probability — Game Timeline</div>
      <div class="chart-wrap">
        <canvas id="wp-canvas"></canvas>
      </div>
    </div>

    <!-- Stat cards -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-label">Pace</div>
        <div class="stat-value">${{state.pace.toFixed(1)}}</div>
        <div class="stat-sub ${{paceDiff >= 0 ? 'up' : 'down'}}">${{paceDiff >= 0 ? '+' : ''}}${{paceDiff.toFixed(1)}} vs avg</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Possessions</div>
        <div class="stat-value">${{state.home_possessions + state.away_possessions}}</div>
        <div class="stat-sub">${{home}} ${{state.home_possessions}} · ${{away}} ${{state.away_possessions}}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Minutes Left</div>
        <div class="stat-value">${{Math.max(48 - state.minutes_elapsed, 0).toFixed(0)}}</div>
        <div class="stat-sub">${{(state.minutes_elapsed || 0).toFixed(1)}} elapsed</div>
      </div>
    </div>

    <!-- Momentum -->
    <div class="momentum-card">
      <div class="card-title">Momentum — Last 10 Possessions</div>
      <div class="momentum-timeline">${{barsHTML}}</div>
      <div class="momentum-status ${{momClass}}">${{momStatus}}</div>
      <div class="momentum-legend">
        <div class="legend-item"><div class="legend-dot" style="background:var(--home)"></div>${{home}} score</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--away)"></div>${{away}} score</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--surface2);border:1px solid var(--border2)"></div>No score</div>
      </div>
    </div>

    <!-- Foul trouble + On court -->
    <div class="bottom-grid">
      <div class="foul-card">
        <div class="card-title">Foul Trouble</div>
        ${{foulHTML}}
      </div>
      <div class="oncourt-card">
        <div class="card-title">On Court</div>
        ${{homePlayers.length ? `
          <div class="oncourt-section">
            <div class="oncourt-team-label home">${{home}}</div>
            <div class="oncourt-chips">${{homeChips}}</div>
          </div>` : ''}}
        ${{awayPlayers.length ? `
          <div class="oncourt-section">
            <div class="oncourt-team-label away">${{away}}</div>
            <div class="oncourt-chips">${{awayChips}}</div>
          </div>` : ''}}
        ${{!homePlayers.length && !awayPlayers.length ? '<div class="foul-empty">No lineup data yet</div>' : ''}}
      </div>
    </div>
  `;

  // Render/update chart
  const canvas = document.getElementById("wp-canvas");
  if (canvas) {{
    if (!wpChart) {{
      wpChart = buildWpChart(canvas.getContext("2d"));
    }}
    wpChart.data.labels = wpHistory.map(d => d.label);
    wpChart.data.datasets[0].data = wpHistory.map(d => +(d.home * 100).toFixed(1));
    wpChart.data.datasets[1].data = wpHistory.map(d => +((1 - d.home) * 100).toFixed(1));
    wpChart.update('none');
  }}

  document.getElementById("update-time").textContent = "Updated " + new Date().toLocaleTimeString();
}}

// ── Poll ──────────────────────────────────────────────────────────────────────
async function fetchAll() {{
  try {{
    const [sR, mR, wR, fR] = await Promise.all([
      fetch("/game/" + GAME_ID + "/state"),
      fetch("/game/" + GAME_ID + "/momentum"),
      fetch("/game/" + GAME_ID + "/win-probability"),
      fetch("/game/" + GAME_ID + "/foul-trouble"),
    ]);
    if (!sR.ok) return;
    const [state, momentum, wp, foul] = await Promise.all([sR.json(), mR.json(), wR.json(), fR.json()]);
    render(state, momentum, wp, foul);
  }} catch(e) {{
    console.error(e);
  }}
}}

fetchAll();
setInterval(fetchAll, 2000);
</script>
</body>
</html>"""
