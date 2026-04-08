const strategyNameMap = {
  trend_hma_std_4h_v1: "\u7b56\u7565 1\uff5c\u9806\u52e2 HMA \u6a19\u6e96\u5dee",
  larry_breakout_cmo_2h_4h_v1: "\u7b56\u7565 2\uff5cLarry \u7a81\u7834\u52d5\u80fd",
  bollinger_width_4h_v1: "\u7b56\u7565 3\uff5c\u5e03\u6797\u901a\u9053 BBW",
  ma_breakout_4h_v1: "\u7b56\u7565 4\uff5c\u5747\u7dda\u7a81\u7834\u639b\u55ae",
  burst_sma_channel_1h_v1: "\u7b56\u7565 5\uff5c\u7206\u767c\u6d41 SMA \u901a\u9053",
  naked_k_reversal_1h_v1: "\u7b56\u7565 6\uff5c\u88f8 K \u53cd\u8f49",
  mean_reversion_atr_2h_daily_v1: "\u7b56\u7565 7\uff5c\u5747\u503c\u56de\u6b78 ATR",
  dual_sma_pullback_2h_v1: "\u7b56\u7565 8\uff5c\u96d9\u5747\u7dda\u56de\u8e29",
  ai_generated_meta_v1: "AI \u7b56\u7565\u683c\uff5c\u81ea\u751f\u6210\u6df7\u5408\u7b56\u7565",
};

function formatNumber(value, digits = 2) {
  const num = Number(value || 0);
  return num.toLocaleString("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function pnlClass(value) {
  return Number(value || 0) >= 0 ? "profit" : "loss";
}

function badgeClass(status) {
  if (status === "\u6301\u5009\u4e2d") return "badge badge-hold";
  if (status === "\u7b49\u5f85\u89f8\u767c") return "badge badge-pending";
  return "badge badge-idle";
}

function renderSummary(data) {
  const balance = data.balance || {};
  const summary = data.summary || {};
  const cards = [
    ["\u4ea4\u6613\u6240\u7e3d\u9918\u984d", `${formatNumber(balance.total_equity_usdt, 2)} U`, balance.note || ""],
    ["\u55ae\u7b56\u7565\u5206\u914d", `${formatNumber(balance.allocated_equity_usdt, 2)} U`, `\u7b56\u7565\u69fd\u4f4d\uff1a${balance.strategy_slot_count || 0}`],
    ["\u7e3d\u5df2\u5be6\u73fe\u640d\u76ca", `${formatNumber(summary.total_realized_pnl_usdt, 2)} U`, "\u6240\u6709\u7b56\u7565\u7d2f\u7a4d\u6de8\u640d\u76ca"],
    ["\u7e3d\u672a\u5be6\u73fe\u640d\u76ca", `${formatNumber(summary.total_unrealized_pnl_usdt, 2)} U`, "\u76ee\u524d\u6301\u5009\u7684\u6d6e\u52d5\u76c8\u8667"],
    ["\u7e3d\u624b\u7e8c\u8cbb", `${formatNumber(summary.total_fees_usdt, 2)} U`, "\u6240\u6709\u5df2\u5e73\u5009\u4ea4\u6613\u7d2f\u7a4d\u624b\u7e8c\u8cbb"],
    ["\u5df2\u5e73\u5009\u7b46\u6578", `${summary.total_trades || 0} \u7b46`, `\u6301\u5009\u4e2d ${summary.holding_count || 0}\uff5c\u7b49\u5f85\u4e2d ${summary.pending_count || 0}`],
  ];

  document.getElementById("summaryGrid").innerHTML = cards.map(([label, value, hint]) => `
    <article class="summary-card">
      <span class="label">${label}</span>
      <div class="value ${value.includes("-") ? "loss" : "profit"}">${value}</div>
      <div class="hint">${hint}</div>
    </article>
  `).join("");
}

function renderRunner(runner) {
  const scanRows = runner?.last_results || [];
  const tradeRows = runner?.last_trade_results || [];

  const scanItems = scanRows.map((row) => `
    <div class="review-card">
      <h4>${strategyNameMap[row.strategy_id] || row.strategy_id}</h4>
      <p>\u72c0\u614b\uff1a${row.status || "\u7121"}\uff5c\u5019\u9078\u6578\uff1a${row.candidate_count || 0}\uff5c\u6700\u4f73\u5e63\uff1a${row.symbol || "\u7121"}\uff5c\u65b9\u5411\uff1a${row.side || "\u7121"}\uff5c\u9031\u671f\uff1a${row.timeframe || "\u7121"}</p>
      <ul class="review-list">
        ${(row.top_candidates || []).map((candidate) => `
          <li>${candidate.symbol || "\u7121"}\uff5c${candidate.side || "\u7121"}\uff5c${candidate.timeframe || "\u7121"}\uff5cRR ${formatNumber(candidate.rr_ratio, 2)}\uff5c\u52dd\u7387 ${formatNumber((candidate.win_rate || 0) * 100, 2)}%</li>
        `).join("")}
      </ul>
    </div>
  `).join("");

  const tradeItems = tradeRows.map((row) => `
    <li>${strategyNameMap[row.strategy_id] || row.strategy_id}\uff5c\u968e\u6bb5\uff1a${row.phase || "\u7121"}\uff5c\u72c0\u614b\uff1a${row.status || "\u7121"}\uff5c\u5546\u54c1\uff1a${row.symbol || "\u7121"}\uff5c\u65b9\u5411\uff1a${row.side || "\u7121"}</li>
  `).join("");

  document.getElementById("runnerPanel").innerHTML = `
    <div class="ai-block">
      <h3 class="ai-title">\u8f2a\u5de1\u7e3d\u72c0\u614b</h3>
      <ul class="ai-list">
        <li>\u80cc\u666f\u8f2a\u5de1\uff1a${runner?.enabled ? "\u5df2\u555f\u7528" : "\u672a\u555f\u7528"}</li>
        <li>Thread \u5b58\u6d3b\uff1a${runner?.thread_alive ? "\u662f" : "\u5426"}</li>
        <li>\u76ee\u524d\u57f7\u884c\u4e2d\uff1a${runner?.running ? "\u662f" : "\u5426"}</li>
        <li>\u6a21\u5f0f\uff1a${runner?.mode || "\u672a\u77e5"}</li>
        <li>\u6383\u63cf\u9593\u9694\uff1a${runner?.interval_sec || 0} \u79d2</li>
        <li>\u7d2f\u7a4d\u8f2a\u6578\uff1a${runner?.loop_count || 0}</li>
        <li>\u4e0a\u6b21\u958b\u59cb\uff1a${runner?.last_cycle_started_at || "\u5c1a\u7121"}</li>
        <li>\u4e0a\u6b21\u5b8c\u6210\uff1a${runner?.last_cycle_finished_at || "\u5c1a\u7121"}</li>
        <li>\u6700\u8fd1\u932f\u8aa4\uff1a${runner?.last_error || "\u7121"}</li>
      </ul>
    </div>
    <div class="ai-block">
      <h3 class="ai-title">\u6700\u8fd1\u4e00\u8f2a\u6383\u5e63\u7d50\u679c</h3>
      ${scanItems || '<p class="ai-list">\u76ee\u524d\u9084\u6c92\u6709\u6383\u63cf\u7d50\u679c\u3002\u8acb\u78ba\u8a8d OKX API \u5df2\u586b\u597d\uff0c\u800c\u4e14\u80cc\u666f\u8f2a\u5de1\u5df2\u555f\u7528\u3002</p>'}
    </div>
    <div class="ai-block">
      <h3 class="ai-title">\u6700\u8fd1\u4e00\u8f2a\u7b56\u7565\u57f7\u884c</h3>
      <ul class="ai-list">
        ${tradeItems || "<li>\u76ee\u524d\u5c1a\u672a\u6709\u7b56\u7565\u57f7\u884c\u7d50\u679c\u3002</li>"}
      </ul>
    </div>
  `;
}

function renderStrategies(strategies) {
  document.getElementById("strategyGrid").innerHTML = (strategies || []).map((item) => `
    <article class="strategy-card">
      <div class="topline">
        <div>
          <h3>${item.name || strategyNameMap[item.strategy_id] || item.strategy_id}</h3>
          <div class="mini-meta">
            <span>${item.symbol || "\u5c1a\u672a\u9078\u5e63"}</span>
            <span>${item.side || "\u672a\u9032\u5834"}</span>
            <span>${item.timeframe || "—"}</span>
          </div>
        </div>
        <span class="${badgeClass(item.status_text)}">${item.status_text}</span>
      </div>

      <div class="metrics">
        <div class="metric-box">
          <span class="metric-label">\u76ee\u524d\u7b56\u7565\u640d\u76ca</span>
          <span class="metric-value ${pnlClass(item.realized_pnl_usdt)}">${formatNumber(item.realized_pnl_usdt, 2)} U</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">\u76ee\u524d\u6301\u5009\u6d6e\u76c8\u8667</span>
          <span class="metric-value ${pnlClass(item.unrealized_pnl_usdt)}">${formatNumber(item.unrealized_pnl_usdt, 2)} U</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">TP / SL</span>
          <span class="metric-value">${item.take_profit_price || 0} / ${item.stop_loss_price || 0}</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">RR / \u52dd\u7387</span>
          <span class="metric-value">${formatNumber(item.rr_ratio, 2)} / ${formatNumber(item.win_rate_total || item.win_rate, 2)}%</span>
        </div>
      </div>

      <div class="move-row">
        <span class="chip ${item.stop_moved ? "chip-on" : "chip-off"}">\u505c\u640d${item.stop_moved ? "\u6709\u79fb\u52d5" : "\u672a\u79fb\u52d5"}</span>
        <span class="chip ${item.take_moved ? "chip-on" : "chip-off"}">\u505c\u5229${item.take_moved ? "\u6709\u79fb\u52d5" : "\u672a\u79fb\u52d5"}</span>
        <span class="chip chip-off">${item.scan_state_text || "\u672a\u77e5\u72c0\u614b"}</span>
        <span class="chip chip-off">\u4ea4\u6613 ${item.trade_count || 0} \u7b46</span>
      </div>

      <ul class="note-list">
        <li>\u9032\u5834\u50f9\uff1a${item.entry_price || 0}\uff5c\u73fe\u50f9\uff1a${item.current_price || 0}</li>
        <li>\u5e73\u5747\u69d3\u687f\uff1a${formatNumber(item.avg_leverage, 2)}\uff5c\u5e73\u5747\u4fdd\u8b49\u91d1\uff1a${formatNumber(item.avg_margin_usdt, 2)} U</li>
        <li>\u624b\u7e8c\u8cbb\uff1a${formatNumber(item.fees_usdt, 2)} U\uff5c\u6700\u5f8c\u7ba1\u7406\u6642\u9593\uff1a${item.last_management_at || "\u5c1a\u7121"}</li>
        <li>\u6700\u4f73\u5019\u9078\uff1a${item.scan_best_symbol || "\u5c1a\u672a\u627e\u5230"}\uff5c\u5019\u9078\u6578\uff1a${item.scan_candidate_count || 0}</li>
      </ul>
    </article>
  `).join("");
}

function renderAiPanel(aiPanel) {
  const profile = aiPanel?.ai_strategy_profile || {};
  const reviewItems = (aiPanel?.latest_strategy_reviews || []).map((item) => `
    <div class="review-card">
      <h4>${strategyNameMap[item.strategy_id] || item.strategy_id}</h4>
      <p>${item.headline || "\u76ee\u524d\u6c92\u6709\u6458\u8981\u3002"}</p>
      <ul class="review-list">
        ${(item.recommendations || []).map((rec) => `<li>${rec}</li>`).join("")}
      </ul>
    </div>
  `).join("");

  document.getElementById("aiPanel").innerHTML = `
    <div class="ai-block">
      <h3 class="ai-title">AI \u7b56\u7565\u683c\u72c0\u614b</h3>
      <ul class="ai-list">
        <li>\u662f\u5426\u555f\u7528\uff1a${aiPanel?.ai_strategy_enabled ? "\u5df2\u555f\u7528" : "\u5c1a\u672a\u555f\u7528"}</li>
        <li>\u4f86\u6e90\u7b56\u7565\u6578\uff1a${(profile.source_strategy_ids || []).length}</li>
        <li>\u81ea\u52d5\u5957\u7528\u5efa\u8b70\uff1a${profile.auto_apply_suggestions ? "\u662f" : "\u5426"}</li>
        <li>\u6700\u8fd1\u9031\u5831\u9031\u5225\uff1a${aiPanel?.last_week_key || "\u5c1a\u7121"}</li>
      </ul>
    </div>
    <div class="ai-block">
      <h3 class="ai-title">\u6574\u9ad4\u89c0\u5bdf</h3>
      <ul class="ai-list">
        ${(aiPanel?.overall_observations || ["\u76ee\u524d\u9084\u6c92\u6709 AI \u9031\u5831\u3002"]).map((item) => `<li>${item}</li>`).join("")}
      </ul>
    </div>
    <div class="ai-block">
      <h3 class="ai-title">\u7b56\u7565\u5efa\u8b70\u6b04</h3>
      ${reviewItems || '<p class="ai-list">\u76ee\u524d\u9084\u6c92\u6709\u7b56\u7565\u5efa\u8b70\u5167\u5bb9\u3002</p>'}
    </div>
  `;
}

function renderTrades(rows) {
  document.getElementById("tradeTableBody").innerHTML = (rows || []).map((row) => `
    <tr>
      <td>${strategyNameMap[row.strategy_id] || row.strategy_id || "\u2014"}</td>
      <td>${row.symbol || "\u2014"}</td>
      <td>${row.side === "buy" ? "\u591a\u55ae" : "\u7a7a\u55ae"}</td>
      <td>${row.entry_price || 0}</td>
      <td>${row.exit_price || 0}</td>
      <td class="${pnlClass(row.net_pnl_usdt)}">${formatNumber(row.net_pnl_usdt, 2)} U</td>
      <td>${formatNumber(row.fees_usdt, 2)} U</td>
      <td>${row.exit_reason || "\u2014"}</td>
      <td>${formatNumber(row.hold_minutes, 1)}</td>
    </tr>
  `).join("");
}

function renderLogs(logs) {
  document.getElementById("logPanel").innerHTML = `
    <div class="log-block">
      <h3 class="log-title">\u6700\u65b0\u4e2d\u6587\u65e5\u8a8c</h3>
      <pre>${(logs?.rows || []).join("\n") || "\u76ee\u524d\u6c92\u6709\u65e5\u8a8c\u5167\u5bb9\u3002"}</pre>
    </div>
  `;
}

async function refreshDashboard() {
  const [dashboardRes, logRes] = await Promise.all([
    fetch("/api/dashboard"),
    fetch("/api/logs"),
  ]);
  const dashboard = await dashboardRes.json();
  const logs = await logRes.json();

  document.getElementById("generatedAt").textContent = dashboard.generated_at || "—";
  document.getElementById("bgMode").textContent = dashboard.background_has_custom_image ? "\u81ea\u8a02\u67ef\u5357\u80cc\u666f" : "\u5075\u63a2\u98a8\u80cc\u666f";

  renderSummary(dashboard);
  renderRunner(dashboard.runner || {});
  renderStrategies(dashboard.strategies || []);
  renderAiPanel(dashboard.ai_panel || {});
  renderTrades(dashboard.recent_trades || []);
  renderLogs(logs || {});
}

document.getElementById("refreshButton").addEventListener("click", refreshDashboard);
refreshDashboard();
setInterval(refreshDashboard, 20000);
