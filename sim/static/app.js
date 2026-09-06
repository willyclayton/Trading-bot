(() => {
  const $ = (sel) => document.querySelector(sel);
  const state = { data: null, tab: "positions", busy: false };

  const fmt = {
    usd: (v, d = 2) => (v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })),
    signed: (v, d = 2) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(d)),
    pct: (v, d = 2) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(d) + "%"),
    num: (v, d = 4) => (v == null ? "—" : Number(v).toFixed(d)),
    time: (ts) => new Date(ts * 1000).toLocaleTimeString(),
  };
  const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- API ----------------------------------------------------------------
  async function api(action, body = {}) {
    const res = await fetch(`/api/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || res.statusText);
    return json;
  }
  async function refresh() {
    try {
      const res = await fetch("/api/state");
      state.data = await res.json();
      render();
    } catch (e) {
      toast("server unreachable: " + e.message, true);
    }
  }
  async function act(action, body, okMsg) {
    if (state.busy) return;
    state.busy = true;
    document.body.style.cursor = "progress";
    try {
      const r = await api(action, body);
      if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg);
      await refresh();
    } catch (e) {
      toast(e.message, true);
    } finally {
      state.busy = false;
      document.body.style.cursor = "";
    }
  }
  let toastTimer;
  function toast(msg, err = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (err ? " err" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
  }

  // ---- render -------------------------------------------------------------
  function render() {
    const d = state.data;
    if (!d) return;
    const s = d.stats, r = s.real, sh = s.shadow;

    $("#clock").textContent = `${s.date}  ·  day ${s.day}  ·  ${s.steps} steps`;
    $("#badge-paused").classList.toggle("hidden", !s.paused);
    $("#badge-halted").classList.toggle("hidden", !s.halted);
    $("#resume").classList.toggle("hidden", !s.paused);
    $("#heartbeat-dot").classList.toggle("live", s.auto);

    const auto = $("#auto-toggle");
    if (auto.checked !== s.auto) auto.checked = s.auto;
    $("#auto-label").classList.toggle("on", s.auto);
    $("#auto-sub").textContent = s.auto ? `on · 1 day / ${d.config.auto_interval_s}s · approving everything` : "off · human approves";
    for (const id of ["step-1", "step-5", "step-20"]) $("#" + id).disabled = s.auto;

    const gap = +(r.equity - sh.equity).toFixed(2);
    $("#kpis").innerHTML = [
      kpi("Equity", fmt.usd(r.equity), `cash ${fmt.usd(r.cash)} · invested ${fmt.usd(r.invested)}`),
      kpi("P&L", `<span class="${cls(r.pnl)}">${fmt.signed(r.pnl)}</span>`, `<span class="${cls(r.pnl)}">${fmt.pct(r.pnl_pct)}</span> on ${fmt.usd(d.config.starting_equity, 0)}`),
      kpi("Drawdown", `<span class="${cls(r.drawdown_pct)}">${fmt.pct(r.drawdown_pct)}</span>`, `high-water ${fmt.usd(r.hwm)} · pause at −${(d.config.drawdown_pause_pct * 100).toFixed(0)}%`),
      kpi("Trades", r.n_trades, r.n_trades ? `win ${r.win_rate}% · avg ${fmt.signed(r.avg_r)}R · total ${fmt.signed(r.total_r)}R` : "no round trips yet"),
      kpi("Friction paid", fmt.usd(r.total_costs), `slippage ${d.config.slippage_bps} bp/side + fees, rounded up`),
      kpi("Positions", `${r.open_positions}<span class="muted" style="font-size:13px">/${d.config.max_positions}</span>`, `${s.pending} pending · risk ${(d.config.risk_pct * 100).toFixed(0)}% = ${fmt.usd(r.equity * d.config.risk_pct)}/trade`),
      kpi("Shadow book", fmt.usd(sh.equity), `real − shadow <span class="${cls(gap)}">${fmt.signed(gap)}</span> · ${sh.n_trades} trades`),
      kpi("Human filter", `${s.discretionary_reject_rate}%`, `${s.discretionary_rejects} discretionary of ${s.rejects} rejects · should trend to 0`),
    ].join("");

    renderChart(d);
    renderPending(d);
    renderQuotes(d);
    renderTab(d);
  }
  const kpi = (label, value, sub) => `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;

  function renderChart(d) {
    const svg = $("#chart");
    const W = 800, H = 260, padL = 54, padR = 10, padT = 10, padB = 22;
    const real = d.curve, shadow = d.shadow_curve;
    if (real.length < 2) { svg.innerHTML = `<text x="400" y="130" fill="#8b98a8" text-anchor="middle" font-size="13">step a day to start the curve</text>`; return; }
    const all = real.concat(shadow);
    let lo = Math.min(...all.map((p) => p.equity)), hi = Math.max(...all.map((p) => Math.max(p.equity, p.hwm)));
    const pauseLine = real[real.length - 1].hwm * (1 - d.config.drawdown_pause_pct);
    lo = Math.min(lo, pauseLine, d.config.starting_equity); hi = Math.max(hi, d.config.starting_equity);
    const span = (hi - lo) || 1; lo -= span * 0.06; hi += span * 0.06;
    const dayMin = real[0].day, dayMax = real[real.length - 1].day;
    const x = (day) => padL + ((day - dayMin) / Math.max(1, dayMax - dayMin)) * (W - padL - padR);
    const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
    const path = (pts, key) => pts.map((p, i) => `${i ? "L" : "M"}${x(p.day).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
    const ticks = 4, grid = [];
    for (let i = 0; i <= ticks; i++) {
      const v = lo + ((hi - lo) * i) / ticks;
      grid.push(`<line x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}" stroke="#223043" stroke-width="1"/>`);
      grid.push(`<text x="${padL - 6}" y="${y(v) + 4}" fill="#8b98a8" font-size="11" text-anchor="end" font-family="monospace">$${v.toFixed(0)}</text>`);
    }
    const xl = [real[0], real[Math.floor(real.length / 2)], real[real.length - 1]];
    const xlabels = xl.map((p, i) => `<text x="${x(p.day)}" y="${H - 6}" fill="#8b98a8" font-size="11" text-anchor="${i === 0 ? "start" : i === 2 ? "end" : "middle"}" font-family="monospace">${p.date}</text>`).join("");
    svg.innerHTML = grid.join("") + xlabels +
      `<line x1="${padL}" x2="${W - padR}" y1="${y(d.config.starting_equity)}" y2="${y(d.config.starting_equity)}" stroke="#3a4a60" stroke-dasharray="2 4"/>` +
      `<line x1="${padL}" x2="${W - padR}" y1="${y(pauseLine)}" y2="${y(pauseLine)}" stroke="#f5b342" stroke-dasharray="4 4" opacity=".7"/>` +
      `<path d="${path(real, "hwm")}" fill="none" stroke="#8b98a8" stroke-width="1" stroke-dasharray="3 3" opacity=".8"/>` +
      `<path d="${path(shadow, "equity")}" fill="none" stroke="#b48cff" stroke-width="1.6" opacity=".9"/>` +
      `<path d="${path(real, "equity")}" fill="none" stroke="#2ecc71" stroke-width="2.2"/>`;
    const last = real[real.length - 1];
    $("#chart-foot").textContent = `${real.length} sessions · latest ${last.date}  equity ${fmt.usd(last.equity)}  cash ${fmt.usd(last.cash)}  hwm ${fmt.usd(last.hwm)}`;
  }

  function renderPending(d) {
    $("#pending-count").textContent = d.pending.length;
    const el = $("#pending");
    if (!d.pending.length) {
      el.innerHTML = `<div class="empty">${d.stats.auto ? "Auto mode approves proposals the moment they are created." : d.stats.paused ? "Drawdown pause: no new proposals until you resume entries." : d.stats.halted ? "Halted." : "No signals at today's close. Step a day."}</div>`;
      return;
    }
    const cats = d.reject_categories.map((c) => `<option value="${c}" ${c === "discretion" ? "selected" : ""}>${c}</option>`).join("");
    el.innerHTML = d.pending.map((p) => {
      const rr = ((p.target_price - p.signal_close) / (p.signal_close - p.stop_price)).toFixed(1);
      return `<div class="proposal" data-id="${p.id}" data-token="${p.token}">
        <div class="row1"><span class="sym">BUY ${p.symbol}</span><span class="mono small muted">${p.id}</span></div>
        <div class="plan">
          <span>notional <b>${fmt.usd(p.notional)}</b></span><span>qty <b>${fmt.num(p.qty)}</b></span>
          <span>signal close <b>${p.signal_close.toFixed(2)}</b></span><span>risk <b>${fmt.usd(p.risk_dollars)}</b> (1R)</span>
          <span>stop <b class="neg">${p.stop_price.toFixed(2)}</b></span><span>target <b class="pos">${p.target_price.toFixed(2)}</b> (${rr}R)</span>
          <span>max hold <b>${p.max_hold_days}d</b></span><span>fill <b>next open</b>, DAY mkt</span>
        </div>
        <div class="why">${esc(p.reason)}. Exits are part of the plan and will not wait for you.</div>
        <div class="actions">
          <button class="approve" data-act="approve">Approve</button>
          <button class="reject" data-act="reject">Reject</button>
          <select class="cat">${cats}</select>
        </div>
      </div>`;
    }).join("");
  }

  function renderQuotes(d) {
    const held = new Set(d.positions.map((p) => p.symbol));
    $("#quotes").innerHTML = d.quotes.map((q) => {
      const pts = q.spark, lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1;
      const path = pts.map((v, i) => `${i ? "L" : "M"}${(i / (pts.length - 1)) * 100},${28 - ((v - lo) / span) * 26 - 1}`).join(" ");
      return `<div class="quote"><span class="sym">${q.symbol} ${held.has(q.symbol) ? '<span class="held">HELD</span>' : ""}</span>
        <span class="px">${q.close.toFixed(2)} <span class="${cls(q.change_pct)}">${fmt.pct(q.change_pct)}</span></span>
        <svg viewBox="0 0 100 28" preserveAspectRatio="none"><path d="${path}" fill="none" stroke="${q.change_pct >= 0 ? "#2ecc71" : "#ff5c5c"}" stroke-width="1.5"/></svg></div>`;
    }).join("");
  }

  function table(cols, rows, empty) {
    if (!rows.length) return `<div class="empty">${empty}</div>`;
    const head = cols.map((c) => `<th class="${c.num ? "num" : ""}">${c.h}</th>`).join("");
    const body = rows.map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.f(r)}</td>`).join("")}</tr>`).join("");
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  const status = (s) => `<span class="status ${s}">${s}</span>`;
  const EXIT_TONE = { target: "live", stop: "rejected", kill: "rejected", time: "expired" };
  const exitBadge = (r) => `<span class="status ${EXIT_TONE[r] || ""}">${r}</span>`;
  const pnl = (v) => `<span class="${cls(v)}">${fmt.signed(v)}</span>`;

  function renderTab(d) {
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.tab));
    const body = $("#tab-body");
    const positions = (rows) => table([
      { h: "Symbol", f: (p) => `<b>${p.symbol}</b>` },
      { h: "Qty", num: true, f: (p) => fmt.num(p.qty) },
      { h: "Entry", num: true, f: (p) => `${p.entry_price.toFixed(2)} <span class="muted">(${p.entry_date})</span>` },
      { h: "Last", num: true, f: (p) => p.last_close.toFixed(2) },
      { h: "Value", num: true, f: (p) => fmt.usd(p.market_value) },
      { h: "Unrealised", num: true, f: (p) => `${pnl(p.unrealized)} <span class="${cls(p.unrealized_pct)}">${fmt.pct(p.unrealized_pct)}</span>` },
      { h: "Stop", num: true, f: (p) => `<span class="neg">${p.stop_price.toFixed(2)}</span>` },
      { h: "Target", num: true, f: (p) => `<span class="pos">${p.target_price.toFixed(2)}</span>` },
      { h: "Held", num: true, f: (p) => `${d.stats.day - p.entry_day}/${p.max_hold_days}d` },
      { h: "Exit", f: (p) => (p.exit_pending ? `<span class="status expired">selling next open · ${p.exit_pending}</span>` : '<span class="muted">open</span>') },
      { h: "Proposal", f: (p) => `<span class="mono muted">${p.proposal_id}</span>` },
    ], rows, "No open positions.");

    const trades = (rows) => {
      const sum = rows.reduce((a, t) => ({ net: a.net + t.net_pnl, costs: a.costs + t.costs, r: a.r + t.r_multiple }), { net: 0, costs: 0, r: 0 });
      return `<div class="summary-row"><span>${rows.length} round trips</span><span>net ${pnl(+sum.net.toFixed(2))}</span><span>friction ${fmt.usd(sum.costs)}</span><span>${fmt.signed(sum.r)}R</span>
        <span>exits: ${["target", "stop", "time", "kill"].map((k) => `${k} ${rows.filter((t) => t.exit_reason === k).length}`).join(" · ")}</span></div>` +
        table([
          { h: "Symbol", f: (t) => `<b>${t.symbol}</b>` },
          { h: "Entry", f: (t) => `${t.entry_date} <span class="mono">@ ${t.entry_price.toFixed(2)}</span>` },
          { h: "Exit", f: (t) => `${t.exit_date} <span class="mono">@ ${t.exit_price.toFixed(2)}</span>` },
          { h: "Reason", f: (t) => exitBadge(t.exit_reason) },
          { h: "Hold", num: true, f: (t) => `${t.hold_days}d` },
          { h: "Qty", num: true, f: (t) => fmt.num(t.qty) },
          { h: "Gross", num: true, f: (t) => pnl(t.gross_pnl) },
          { h: "Slippage", num: true, f: (t) => fmt.usd(t.slippage) },
          { h: "Fees", num: true, f: (t) => fmt.usd(t.fees) },
          { h: "Net", num: true, f: (t) => `<b>${pnl(t.net_pnl)}</b>` },
          { h: "R", num: true, f: (t) => pnl(t.r_multiple) },
          { h: "Proposal", f: (t) => `<span class="mono muted">${t.proposal_id}</span>` },
        ], rows, "No closed trades yet.");
    };

    if (state.tab === "positions") body.innerHTML = positions(d.positions);
    else if (state.tab === "trades") body.innerHTML = trades(d.trades);
    else if (state.tab === "orders") body.innerHTML = table([
      { h: "Order", f: (o) => `<span class="mono">${o.id}</span>` },
      { h: "client_order_id", f: (o) => `<span class="mono muted">${o.client_order_id}</span>` },
      { h: "Kind", f: (o) => o.kind },
      { h: "Side", f: (o) => `<span class="${o.side === "buy" ? "pos" : "neg"}">${o.side.toUpperCase()}</span>` },
      { h: "Symbol", f: (o) => `<b>${o.symbol}</b>` },
      { h: "Qty", num: true, f: (o) => fmt.num(o.qty) },
      { h: "TIF / class", f: (o) => `${o.time_in_force} / ${o.order_class}` },
      { h: "Status", f: (o) => status(o.status) },
      { h: "Submitted", num: true, f: (o) => `day ${o.submitted_day}` },
      { h: "Filled", num: true, f: (o) => (o.filled_day != null ? `day ${o.filled_day}` : "—") },
      { h: "Ref open", num: true, f: (o) => (o.reference_price != null ? o.reference_price.toFixed(2) : "—") },
      { h: "Fill", num: true, f: (o) => (o.filled_price != null ? o.filled_price.toFixed(4) : "—") },
      { h: "Slip", num: true, f: (o) => fmt.usd(o.slippage) },
      { h: "Fees", num: true, f: (o) => fmt.usd(o.fees) },
    ], d.orders, "No orders yet.");
    else if (state.tab === "proposals") body.innerHTML = table([
      { h: "Id", f: (p) => `<span class="mono">${p.id}</span>` },
      { h: "Symbol", f: (p) => `<b>${p.symbol}</b>` },
      { h: "Status", f: (p) => status(p.status) },
      { h: "Notional", num: true, f: (p) => fmt.usd(p.notional) },
      { h: "Signal", num: true, f: (p) => p.signal_close.toFixed(2) },
      { h: "Stop / target", num: true, f: (p) => `${p.stop_price.toFixed(2)} / ${p.target_price.toFixed(2)}` },
      { h: "Decided by", f: (p) => p.decided_by || "—" },
      { h: "Drift", num: true, f: (p) => (p.drift_pct != null ? fmt.pct(p.drift_pct) : "—") },
      { h: "Fill", num: true, f: (p) => (p.fill_price != null ? p.fill_price.toFixed(4) : "—") },
      { h: "Note", f: (p) => `<span class="muted">${esc(p.block_reason || (p.reject_category ? `${p.reject_category}: ${p.reject_note}` : p.reason))}</span>` },
    ], d.proposals, "No proposals yet.");
    else if (state.tab === "shadow") body.innerHTML =
      `<p class="muted small">Every proposal that reached PENDING, filled at the modelled open as if approved. When Auto is on, real and shadow are the same book; the difference when Auto is off is what human filtering cost or saved.</p>` +
      `<h2 style="margin:10px 0 6px">Shadow positions</h2>` + positions(d.shadow_positions) +
      `<h2 style="margin:14px 0 6px">Shadow trades</h2>` + trades(d.shadow_trades);
    else if (state.tab === "audit") body.innerHTML = table([
      { h: "#", num: true, f: (a) => a.id },
      { h: "Wall clock", f: (a) => `<span class="muted mono">${fmt.time(a.ts)}</span>` },
      { h: "Sim day", f: (a) => `<span class="mono">${a.day} · ${a.date}</span>` },
      { h: "Event", f: (a) => `<span class="ev ${a.event}">${a.event}</span>` },
      { h: "Detail", f: (a) => `<span style="white-space:normal">${esc(a.detail)}</span>` },
    ], d.audit, "Empty audit log.");
  }

  // ---- events ---------------------------------------------------------------
  $("#auto-toggle").addEventListener("change", (e) => act("auto", { on: e.target.checked }, (r) => (r.auto ? "Auto ON — approving every proposal and advancing one day per tick" : "Auto OFF — proposals now wait for you")));
  $("#step-1").addEventListener("click", () => act("step", { days: 1 }));
  $("#step-5").addEventListener("click", () => act("step", { days: 5 }));
  $("#step-20").addEventListener("click", () => act("step", { days: 20 }));
  $("#resume").addEventListener("click", () => act("resume", {}, "Entries resumed after manual review"));
  $("#reset").addEventListener("click", () => {
    if (confirm("Wipe all history and start over with a fresh $1,000 account?")) act("reset", {}, "Reset to a fresh account");
  });
  $("#pending").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const card = btn.closest(".proposal");
    const body = { id: card.dataset.id, token: card.dataset.token };
    if (btn.dataset.act === "reject") body.category = card.querySelector(".cat").value;
    act(btn.dataset.act, body, `${body.id} ${btn.dataset.act === "approve" ? "approved" : "rejected"}`);
  });
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (!b) return;
    state.tab = b.dataset.tab;
    render();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "n" && !e.metaKey && !e.ctrlKey && !state.data?.stats.auto) act("step", { days: 1 });
  });

  refresh();
  setInterval(() => { if (!state.busy) refresh(); }, 1000);
})();
