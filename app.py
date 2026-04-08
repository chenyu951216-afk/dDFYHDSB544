import os

from flask import Flask, jsonify, render_template, request

from background_runner import get_runner_snapshot, start_background_runner
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
start_background_runner()


@app.before_request
def ensure_background_runner():
    start_background_runner()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/dashboard")
def api_dashboard():
    return jsonify(dashboard_snapshot())


@app.route("/api/logs")
def api_logs():
    return jsonify(dashboard_logs(limit=180))


@app.route("/api/runner")
def api_runner():
    return jsonify(
        {
            "message": "\u81ea\u52d5\u8f2a\u5de1\u8207\u6383\u5e63\u72c0\u614b",
            "payload": get_runner_snapshot(),
        }
    )


@app.route("/api/learning/overview")
def api_learning_overview():
    return jsonify(
        {
            "message": "AI \u5b78\u7fd2\u8cc7\u6599\u5eab\u6458\u8981",
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
            "message": "AI \u5b78\u7fd2\u6210\u4ea4\u8cc7\u6599",
            "database": "SQLite",
            "payload": fetch_closed_trades(strategy_id=strategy_id, limit=limit),
        }
    )


@app.route("/api/learning/rollups")
def api_learning_rollups():
    return jsonify(
        {
            "message": "AI \u5b78\u7fd2\u7b56\u7565\u5f59\u7e3d",
            "database": "SQLite",
            "payload": fetch_strategy_rollups(),
        }
    )


@app.route("/api/learning/symbol-stats")
def api_learning_symbol_stats():
    strategy_id = str(request.args.get("strategy_id", "") or "").strip() or None
    return jsonify(
        {
            "message": "AI \u5b78\u7fd2\u5546\u54c1\u7d71\u8a08",
            "database": "SQLite",
            "payload": fetch_symbol_stats(strategy_id=strategy_id),
        }
    )


@app.route("/api/learning/reviews")
def api_learning_reviews():
    limit = int(request.args.get("limit", "12") or "12")
    return jsonify(
        {
            "message": "AI \u6bcf\u9031\u5b78\u7fd2\u56de\u5831",
            "database": "SQLite",
            "payload": fetch_weekly_reviews(limit=limit),
        }
    )


@app.route("/api/learning/ai-profile")
def api_learning_ai_profile():
    return jsonify(
        {
            "message": "AI \u7b56\u7565\u683c\u8a2d\u5b9a",
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
