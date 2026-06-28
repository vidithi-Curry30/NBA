"""HTML dashboard template served at /dashboard/{game_id}."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NBA Live — {game_id}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background: #0a0e1a;
    color: #e8eaf0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    min-height: 100vh;
    padding: 24px 16px;
  }}

  .container {{ max-width: 720px; margin: 0 auto; }}

  /* Header */
  .header {{
    text-align: center;
    margin-bottom: 28px;
  }}
  .header h1 {{
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: #6b7280;
    margin-bottom: 4px;
  }}
  .live-badge {{
    display: inline-block;
    background: #ef4444;
    color: white;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
    padding: 2px 8px;
    border-radius: 3px;
    animation: pulse 2s infinite;
  }}
  .live-badge.final {{ background: #6b7280; animation: none; }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.5; }}
  }}

  /* Scoreboard */
  .scoreboard {{
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 16px;
    padding: 32px 24px 24px;
    margin-bottom: 16px;
  }}
  .teams {{
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: 16px;
    margin-bottom: 20px;
  }}
  .team {{ text-align: center; }}
  .team-name {{
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 2px;
    color: #9ca3af;
    text-transform: uppercase;
    margin-bottom: 8px;
  }}
  .team-name.home {{ color: #60a5fa; }}
  .team-name.away {{ color: #f87171; }}
  .score {{
    font-size: 64px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: -2px;
  }}
  .score.home {{ color: #93c5fd; }}
  .score.away {{ color: #fca5a5; }}
  .game-clock {{
    text-align: center;
  }}
  .period {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 2px;
    color: #6b7280;
    text-transform: uppercase;
    margin-bottom: 4px;
  }}
  .clock {{
    font-size: 24px;
    font-weight: 300;
    color: #e5e7eb;
    font-variant-numeric: tabular-nums;
  }}

  /* Win probability */
  .wp-section {{ margin-bottom: 6px; }}
  .wp-labels {{
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: #6b7280;
    margin-bottom: 6px;
    font-weight: 500;
    letter-spacing: 0.5px;
  }}
  .wp-bar-track {{
    height: 8px;
    background: #1f2937;
    border-radius: 4px;
    overflow: hidden;
  }}
  .wp-bar-fill {{
    height: 100%;
    background: linear-gradient(90deg, #3b82f6, #60a5fa);
    border-radius: 4px;
    transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1);
  }}
  .wp-pct {{
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    font-weight: 600;
    margin-top: 6px;
  }}
  .wp-pct .home-pct {{ color: #60a5fa; }}
  .wp-pct .away-pct {{ color: #f87171; }}

  /* Cards grid */
  .cards {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 12px;
  }}
  .card {{
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 12px;
    padding: 16px;
  }}
  .card-label {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: #4b5563;
    margin-bottom: 10px;
  }}
  .card-value {{
    font-size: 28px;
    font-weight: 600;
    color: #e5e7eb;
    line-height: 1;
    margin-bottom: 4px;
  }}
  .card-sub {{
    font-size: 11px;
    color: #6b7280;
  }}
  .card-sub.positive {{ color: #34d399; }}
  .card-sub.negative {{ color: #f87171; }}

  /* Momentum */
  .momentum-card {{
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
  }}
  .momentum-dots {{
    display: flex;
    gap: 6px;
    align-items: center;
    margin: 10px 0 8px;
  }}
  .dot {{
    width: 24px;
    height: 24px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 700;
    flex-shrink: 0;
  }}
  .dot.home-score {{ background: #1d4ed8; color: #93c5fd; }}
  .dot.away-score {{ background: #991b1b; color: #fca5a5; }}
  .dot.turnover {{ background: #1f2937; color: #4b5563; }}
  .dot.other {{ background: #1f2937; color: #4b5563; }}
  .momentum-label {{
    font-size: 12px;
    color: #9ca3af;
  }}
  .momentum-label.home-run {{ color: #60a5fa; font-weight: 600; }}
  .momentum-label.away-run {{ color: #f87171; font-weight: 600; }}

  /* Foul trouble */
  .foul-card {{
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
  }}
  .foul-list {{ margin-top: 8px; }}
  .foul-item {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid #1f2937;
    font-size: 13px;
  }}
  .foul-item:last-child {{ border-bottom: none; }}
  .foul-player {{ color: #e5e7eb; }}
  .foul-count {{
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
  }}
  .foul-count.danger {{ background: #7f1d1d; color: #fca5a5; }}
  .foul-count.warning {{ background: #78350f; color: #fcd34d; }}
  .foul-none {{ font-size: 12px; color: #4b5563; margin-top: 8px; }}

  /* On court */
  .oncourt-card {{
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
  }}
  .oncourt-row {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 8px;
    margin-bottom: 10px;
  }}
  .player-chip {{
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 4px;
    font-weight: 500;
  }}
  .player-chip.home {{ background: #1e3a5f; color: #93c5fd; }}
  .player-chip.away {{ background: #3f1515; color: #fca5a5; }}
  .oncourt-team-label {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-top: 8px;
    margin-bottom: 4px;
  }}
  .oncourt-team-label.home {{ color: #3b82f6; }}
  .oncourt-team-label.away {{ color: #ef4444; }}

  /* Footer */
  .footer {{
    text-align: center;
    margin-top: 20px;
    font-size: 11px;
    color: #374151;
  }}
  .updated {{ font-size: 11px; color: #374151; text-align: right; margin-top: 8px; }}

  .error-state {{
    text-align: center;
    padding: 60px 20px;
    color: #4b5563;
  }}
  .error-state h2 {{ font-size: 18px; margin-bottom: 8px; color: #6b7280; }}
  .spinner {{
    width: 32px; height: 32px;
    border: 3px solid #1f2937;
    border-top-color: #3b82f6;
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 20px auto;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>NBA Game Processor</h1>
    <span class="live-badge" id="status-badge">LIVE</span>
  </div>

  <div id="content">
    <div class="error-state">
      <div class="spinner"></div>
      <h2>Waiting for game data</h2>
      <p>Start the processor and replay to see live stats.</p>
    </div>
  </div>

  <div class="footer">nba-game-processor &nbsp;·&nbsp; game {game_id}</div>
</div>

<script>
const GAME_ID = "{game_id}";
const BASE = "";

function periodLabel(p) {{
  if (p <= 4) return "Q" + p;
  return "OT" + (p - 4);
}}

function dotClass(p) {{
  if (p === "home_score") return "home-score";
  if (p === "away_score") return "away-score";
  if (p === "turnover") return "turnover";
  return "other";
}}

function dotLetter(p) {{
  if (p === "home_score") return "H";
  if (p === "away_score") return "A";
  return "·";
}}

function paceDiff(pace) {{
  const diff = (pace - 100.0).toFixed(1);
  const sign = diff >= 0 ? "+" : "";
  return sign + diff + " vs avg";
}}

function render(state, momentum, wp, foul) {{
  const home = state.home_team || "HOME";
  const away = state.away_team || "AWAY";
  const isFinal = state.game_status === "final";
  const homePct = Math.round((wp.home_win_probability || 0.5) * 100);
  const awayPct = 100 - homePct;
  const paceDiffVal = (state.pace - 100.0);

  // Foul trouble items
  let foulHTML = "";
  const allFouls = foul.all_player_fouls || {{}};
  const troublePlayers = Object.entries(allFouls)
    .filter(([_, f]) => f >= 4)
    .sort((a, b) => b[1] - a[1]);

  if (troublePlayers.length === 0) {{
    foulHTML = '<p class="foul-none">No players in foul trouble</p>';
  }} else {{
    foulHTML = '<div class="foul-list">' + troublePlayers.map(([player, fouls]) => {{
      const cls = fouls >= 5 ? "danger" : "warning";
      const label = fouls >= 6 ? "FOULED OUT" : fouls + " fouls";
      return `<div class="foul-item"><span class="foul-player">${{player}}</span><span class="foul-count ${{cls}}">${{label}}</span></div>`;
    }}).join("") + "</div>";
  }}

  // Momentum dots
  const possessions = momentum.last_10_possessions || [];
  const dotsHTML = possessions.map(p =>
    `<div class="dot ${{dotClass(p)}}">${{dotLetter(p)}}</div>`
  ).join("");

  const homeCount = possessions.filter(p => p === "home_score").length;
  const awayCount = possessions.filter(p => p === "away_score").length;
  let momentumLabel = "Contested";
  let momentumClass = "";
  if (homeCount > awayCount + 2) {{
    momentumLabel = home + " on a run (" + homeCount + "-" + awayCount + " last 10)";
    momentumClass = "home-run";
  }} else if (awayCount > homeCount + 2) {{
    momentumLabel = away + " on a run (" + awayCount + "-" + homeCount + " last 10)";
    momentumClass = "away-run";
  }} else {{
    momentumLabel = "Contested · " + homeCount + "-" + awayCount + " last 10";
  }}

  // On-court rosters
  const homePlayers = (state.home_players_on_court || []);
  const awayPlayers = (state.away_players_on_court || []);
  const homeChips = homePlayers.map(p => `<span class="player-chip home">${{p}}</span>`).join("");
  const awayChips = awayPlayers.map(p => `<span class="player-chip away">${{p}}</span>`).join("");

  const badge = document.getElementById("status-badge");
  badge.textContent = isFinal ? "FINAL" : "LIVE";
  badge.className = "live-badge" + (isFinal ? " final" : "");

  document.getElementById("content").innerHTML = `
    <div class="scoreboard">
      <div class="teams">
        <div class="team">
          <div class="team-name home">${{home}}</div>
          <div class="score home">${{state.home_score}}</div>
        </div>
        <div class="game-clock">
          <div class="period">${{periodLabel(state.period)}}</div>
          <div class="clock">${{state.clock}}</div>
        </div>
        <div class="team">
          <div class="team-name away">${{away}}</div>
          <div class="score away">${{state.away_score}}</div>
        </div>
      </div>

      <div class="wp-section">
        <div class="wp-labels"><span>${{home}}</span><span>Win Probability</span><span>${{away}}</span></div>
        <div class="wp-bar-track">
          <div class="wp-bar-fill" style="width: ${{homePct}}%"></div>
        </div>
        <div class="wp-pct">
          <span class="home-pct">${{homePct}}%</span>
          <span class="away-pct">${{awayPct}}%</span>
        </div>
      </div>
    </div>

    <div class="cards">
      <div class="card">
        <div class="card-label">Pace</div>
        <div class="card-value">${{state.pace.toFixed(1)}}</div>
        <div class="card-sub ${{paceDiffVal >= 0 ? 'positive' : 'negative'}}">${{paceDiff(state.pace)}} poss/48min</div>
      </div>
      <div class="card">
        <div class="card-label">Possessions</div>
        <div class="card-value">${{state.home_possessions + state.away_possessions}}</div>
        <div class="card-sub">${{home}} ${{state.home_possessions}} · ${{away}} ${{state.away_possessions}}</div>
      </div>
    </div>

    <div class="momentum-card">
      <div class="card-label">Momentum — Last 10 Possessions</div>
      <div class="momentum-dots">${{dotsHTML || '<span style="color:#4b5563;font-size:12px">No possessions yet</span>'}}</div>
      <div class="momentum-label ${{momentumClass}}">${{momentumLabel}}</div>
    </div>

    <div class="foul-card">
      <div class="card-label">Foul Trouble</div>
      ${{foulHTML}}
    </div>

    ${{(homePlayers.length || awayPlayers.length) ? `
    <div class="oncourt-card">
      <div class="card-label">On Court</div>
      ${{homePlayers.length ? `<div class="oncourt-team-label home">${{home}}</div><div class="oncourt-row">${{homeChips}}</div>` : ""}}
      ${{awayPlayers.length ? `<div class="oncourt-team-label away">${{away}}</div><div class="oncourt-row">${{awayChips}}</div>` : ""}}
    </div>` : ""}}

    <div class="updated">Updated ${{new Date().toLocaleTimeString()}}</div>
  `;
}}

async function fetchAll() {{
  try {{
    const [stateRes, momentumRes, wpRes, foulRes] = await Promise.all([
      fetch(BASE + "/game/" + GAME_ID + "/state"),
      fetch(BASE + "/game/" + GAME_ID + "/momentum"),
      fetch(BASE + "/game/" + GAME_ID + "/win-probability"),
      fetch(BASE + "/game/" + GAME_ID + "/foul-trouble"),
    ]);

    if (!stateRes.ok) {{
      document.getElementById("content").innerHTML = `
        <div class="error-state">
          <div class="spinner"></div>
          <h2>Waiting for game data</h2>
          <p>Start the processor and replay to see live stats.</p>
        </div>`;
      return;
    }}

    const [state, momentum, wp, foul] = await Promise.all([
      stateRes.json(), momentumRes.json(), wpRes.json(), foulRes.json()
    ]);

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
