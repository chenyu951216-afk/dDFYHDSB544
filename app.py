import os

from flask import Flask, jsonify, render_template, request

from dashboard_service import dashboard_logs, dashboard_snapshot, startup_message
from learning_db import (
    fetch_ai_profile,
    fetch_closed_trades,
    fetch_learning_overview,
    fetch_strategy_rollups,
    fetch_symbol_stats,
    fetch_weekly_reviews,
    init_learning_db,
)


app = Flask(__name__, template_folder="templates", static_folder="static")
init_learning_db()
startup_message()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/dashboard")
def api_dashboard():
    return jsonify(dashboard_snapshot())


@app.route("/api/logs")
def api_logs():
    return jsonify(dashboard_logs(limit=180))


@app.route("/api/learning/overview")
def api_learning_overview():
    return jsonify(
        {
            "message": "AI 學習資料庫總覽",
            "database": "SQLite",
            "payload": fetch_learning_overview(),
        }
    )


@app.route("/api/learning/trades")
def api_learning_trades():
    strategy_id = str(request.args.get("strategy_id", "") or "").strip() or None
    limit = int(request.args.get("limit", "100") or "100")
    return jsonify(
        {
            "message": "AI 學習成交資料",
            "database": "SQLite",
            "payload": fetch_closed_trades(strategy_id=strategy_id, limit=limit),
        }
    )


@app.route("/api/learning/rollups")
def api_learning_rollups():
    return jsonify(
        {
            "message": "AI 學習策略彙總",
            "database": "SQLite",
            "payload": fetch_strategy_rollups(),
        }
    )


@app.route("/api/learning/symbol-stats")
def api_learning_symbol_stats():
    strategy_id = str(request.args.get("strategy_id", "") or "").strip() or None
    return jsonify(
        {
            "message": "AI 學習商品統計",
            "database": "SQLite",
            "payload": fetch_symbol_stats(strategy_id=strategy_id),
        }
    )


@app.route("/api/learning/reviews")
def api_learning_reviews():
    limit = int(request.args.get("limit", "12") or "12")
    return jsonify(
        {
            "message": "AI 每週學習回報",
            "database": "SQLite",
            "payload": fetch_weekly_reviews(limit=limit),
        }
    )


@app.route("/api/learning/ai-profile")
def api_learning_ai_profile():
    return jsonify(
        {
            "message": "AI 策略格設定",
            "database": "SQLite",
            "payload": fetch_ai_profile(),
        }
    )


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    try:
        from waitress import serve

        serve(app, host="0.0.0.0", port=port)
    except Exception:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
