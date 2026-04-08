const strategyNameMap = {
  trend_hma_std_4h_v1: "策略 1｜順勢 HMA 標準差",
  larry_breakout_cmo_2h_4h_v1: "策略 2｜Larry 突破動能",
  bollinger_width_4h_v1: "策略 3｜布林通道 BBW",
  ma_breakout_4h_v1: "策略 4｜均線突破掛單",
  burst_sma_channel_1h_v1: "策略 5｜爆發流 SMA 通道",
  naked_k_reversal_1h_v1: "策略 6｜裸 K 反轉",
  mean_reversion_atr_2h_daily_v1: "策略 7｜均值回歸 ATR",
  dual_sma_pullback_2h_v1: "策略 8｜雙均線回踩",
  ai_generated_meta_v1: "AI 策略格｜自生成混合策略",
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
  if (status === "持倉中") return "badge badge-hold";
  if (status === "等待觸發") return "badge badge-pending";
  return "badge badge-idle";
}

function renderSummary(data) {
  const balance = data.balance || {};
  const summary = data.summary || {};
  const cards = [
    ["交易所總餘額", `${formatNumber(balance.total_equity_usdt, 2)} U`, balance.note || ""],
    ["單策略分配", `${formatNumber(balance.allocated_equity_usdt, 2)} U`, `策略槽位：${balance.strategy_slot_count || 0}`],
    ["總已實現損益", `${formatNumber(summary.total_realized_pnl_usdt, 2)} U`, "所有策略累積淨損益"],
    ["總未實現損益", `${formatNumber(summary.total_unrealized_pnl_usdt, 2)} U`, "目前持倉的浮動盈虧"],
    ["總手續費", `${formatNumber(summary.total_fees_usdt, 2)} U`, "所有已平倉交易累積手續費"],
    ["已平倉筆數", `${summary.total_trades || 0} 筆`, `持倉中 ${summary.holding_count || 0}｜等待中 ${summary.pending_count || 0}`],
  ];

  document.getElementById("summaryGrid").innerHTML = cards.map(([label, value, hint]) => `
    <article class="summary-card">
      <span class="label">${label}</span>
      <div class="value ${value.includes("-") ? "loss" : "profit"}">${value}</div>
      <div class="hint">${hint}</div>
    </article>
  `).join("");
}

function renderStrategies(strategies) {
  document.getElementById("strategyGrid").innerHTML = (strategies || []).map((item) => `
    <article class="strategy-card">
      <div class="topline">
        <div>
          <h3>${item.name || strategyNameMap[item.strategy_id] || item.strategy_id}</h3>
          <div class="mini-meta">
            <span>${item.symbol || "尚未選幣"}</span>
            <span>${item.side || "未進場"}</span>
            <span>${item.timeframe || "—"}</span>
          </div>
        </div>
        <span class="${badgeClass(item.status_text)}">${item.status_text}</span>
      </div>

      <div class="metrics">
        <div class="metric-box">
          <span class="metric-label">目前策略損益</span>
          <span class="metric-value ${pnlClass(item.realized_pnl_usdt)}">${formatNumber(item.realized_pnl_usdt, 2)} U</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">目前持倉浮盈虧</span>
          <span class="metric-value ${pnlClass(item.unrealized_pnl_usdt)}">${formatNumber(item.unrealized_pnl_usdt, 2)} U</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">TP / SL</span>
          <span class="metric-value">${item.take_profit_price || 0} / ${item.stop_loss_price || 0}</span>
        </div>
        <div class="metric-box">
          <span class="metric-label">RR / 勝率</span>
          <span class="metric-value">${formatNumber(item.rr_ratio, 2)} / ${formatNumber(item.win_rate_total || item.win_rate, 2)}%</span>
        </div>
      </div>

      <div class="move-row">
        <span class="chip ${item.stop_moved ? "chip-on" : "chip-off"}">停損${item.stop_moved ? "有移動" : "未移動"}</span>
        <span class="chip ${item.take_moved ? "chip-on" : "chip-off"}">停利${item.take_moved ? "有移動" : "未移動"}</span>
        <span class="chip chip-off">手續費 ${formatNumber(item.fees_usdt, 2)} U</span>
        <span class="chip chip-off">交易 ${item.trade_count || 0} 筆</span>
      </div>

      <ul class="note-list">
        <li>進場價：${item.entry_price || 0}｜現價：${item.current_price || 0}</li>
        <li>平均槓桿：${formatNumber(item.avg_leverage, 2)}｜平均保證金：${formatNumber(item.avg_margin_usdt, 2)} U</li>
        <li>${item.opened_at ? `開倉時間：${item.opened_at}` : (item.pending_created_at ? `等待建立時間：${item.pending_created_at}` : "目前尚無開倉時間")}</li>
      </ul>
    </article>
  `).join("");
}

function renderAiPanel(aiPanel) {
  const profile = aiPanel?.ai_strategy_profile || {};
  const reviewItems = (aiPanel?.latest_strategy_reviews || []).map((item) => `
    <div class="review-card">
      <h4>${strategyNameMap[item.strategy_id] || item.strategy_id}</h4>
      <p>${item.headline || "目前沒有摘要。"}</p>
      <ul class="review-list">
        ${(item.recommendations || []).map((rec) => `<li>${rec}</li>`).join("")}
      </ul>
    </div>
  `).join("");

  document.getElementById("aiPanel").innerHTML = `
    <div class="ai-block">
      <h3 class="ai-title">AI 策略格狀態</h3>
      <ul class="ai-list">
        <li>是否啟用：${aiPanel?.ai_strategy_enabled ? "已啟用" : "尚未啟用"}</li>
        <li>來源策略數：${(profile.source_strategy_ids || []).length}</li>
        <li>自動套用建議：${profile.auto_apply_suggestions ? "是" : "否"}</li>
        <li>最近週報週別：${aiPanel?.last_week_key || "尚無"}</li>
      </ul>
    </div>
    <div class="ai-block">
      <h3 class="ai-title">整體觀察</h3>
      <ul class="ai-list">
        ${(aiPanel?.overall_observations || ["目前還沒有 AI 週報。"]).map((item) => `<li>${item}</li>`).join("")}
      </ul>
    </div>
    <div class="ai-block">
      <h3 class="ai-title">策略建議欄</h3>
      ${reviewItems || '<p class="ai-list">目前還沒有策略建議內容。</p>'}
    </div>
  `;
}

function renderTrades(rows) {
  document.getElementById("tradeTableBody").innerHTML = (rows || []).map((row) => `
    <tr>
      <td>${strategyNameMap[row.strategy_id] || row.strategy_id || "—"}</td>
      <td>${row.symbol || "—"}</td>
      <td>${row.side === "buy" ? "多單" : "空單"}</td>
      <td>${row.entry_price || 0}</td>
      <td>${row.exit_price || 0}</td>
      <td class="${pnlClass(row.net_pnl_usdt)}">${formatNumber(row.net_pnl_usdt, 2)} U</td>
      <td>${formatNumber(row.fees_usdt, 2)} U</td>
      <td>${row.exit_reason || "—"}</td>
      <td>${formatNumber(row.hold_minutes, 1)}</td>
    </tr>
  `).join("");
}

function renderLogs(logs) {
  document.getElementById("logPanel").innerHTML = `
    <div class="log-block">
      <h3 class="log-title">最新中文日誌</h3>
      <pre>${(logs?.rows || []).join("\n") || "目前沒有日誌內容。"}</pre>
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
  document.getElementById("bgMode").textContent = dashboard.background_has_custom_image ? "自訂柯南圖" : "偵探風背景";
  renderSummary(dashboard);
  renderStrategies(dashboard.strategies || []);
  renderAiPanel(dashboard.ai_panel || {});
  renderTrades(dashboard.recent_trades || []);
  renderLogs(logs || {});
}

document.getElementById("refreshButton").addEventListener("click", refreshDashboard);
refreshDashboard();
setInterval(refreshDashboard, 20000);
