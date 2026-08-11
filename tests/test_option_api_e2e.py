import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

os.environ.setdefault("AUTO_START_BOT", "false")
os.environ.setdefault("ALPACA_STREAMING_ENABLED", "false")
os.environ.setdefault("ALPACA_TRADE_UPDATES_ENABLED", "false")

import api_server


class FakeDashboardState:
    def __init__(self):
        self.option_bot_state = "Stopped"
        self.option_bot_config = {
            "approvalMode": "human",
            "spreadFilter": "",
            "deltaTarget": "",
            "expectedMove": "",
            "contractPolicy": "only_long_call",
            "watchlistSource": "option",
        }

    def dashboard_payload(self):
        return {
            "botState": "Stopped",
            "optionBot": {
                "state": self.option_bot_state,
                "executionMode": self.option_bot_config["approvalMode"],
                "spreadFilter": self.option_bot_config["spreadFilter"],
                "deltaTarget": self.option_bot_config["deltaTarget"],
                "expectedMove": self.option_bot_config["expectedMove"],
                "watchlistSource": self.option_bot_config["watchlistSource"],
                "watchlistLabel": "MAG7-Watchlist Options" if self.option_bot_config["watchlistSource"] == "mag7" else "Option Watchlist",
                "watchlistCount": (
                    len(api_server.DEFAULT_MAG7_OPTION_WATCHLIST)
                    if self.option_bot_config["watchlistSource"] == "mag7"
                    else len(api_server.DEFAULT_OPTION_WATCHLIST)
                ),
                "agents": [
                    {"name": "Signal Agent", "status": self.option_bot_state, "detail": "fake"},
                    {"name": "Runner Agent", "status": "Idle", "detail": "fake"},
                    {"name": "LLM Supervisor Agent", "status": "Review Ready", "detail": "advisory-only"},
                ],
            },
            "optionSupervisorReport": {
                "name": "LLM Supervisor Agent",
                "status": "Review Ready",
                "mode": "advisory_only",
                "summary": "fake supervisor summary",
                "authority": {
                    "canPlaceOrders": False,
                    "canOverrideRules": False,
                    "executionPath": "Rules engine only",
                    "note": "advisory-only",
                },
                "llm": {"mode": "rules_fallback", "model": "fake", "latencyMs": 1, "note": "fake"},
            },
            "optionPositions": [],
        }

    def update_option_bot_config(self, contract_policy=None, approval_mode=None, spread_filter=None, delta_target=None, expected_move=None, watchlist_source=None):
        normalized_watchlist_source = "mag7" if str(watchlist_source or "option").lower().startswith("mag7") else "option"
        self.option_bot_config = {
            "contractPolicy": contract_policy or "only_long_call",
            "approvalMode": approval_mode or "human",
            "spreadFilter": spread_filter or "",
            "deltaTarget": delta_target or "",
            "expectedMove": expected_move or "",
            "watchlistSource": normalized_watchlist_source,
        }
        return self.dashboard_payload()

    def set_option_bot_state(self, value):
        self.option_bot_state = value
        return self.dashboard_payload()

    def manage_option_positions_now(self):
        return {
            "result": {
                "status": "idle",
                "message": "Option stop manager checked Alpaca; no open option position needed an exit.",
                "managed": 0,
                "closed": [],
                "updated": [],
            },
            "dashboard": self.dashboard_payload(),
        }


class OptionApiEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.original_state = api_server.STATE
        api_server.STATE = FakeDashboardState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), api_server.ApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        api_server.STATE = self.original_state

    def request_json(self, path, payload=None, method=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method or ("POST" if payload is not None else "GET"),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_option_settings_and_bot_control_routes_round_trip(self):
        health_status, health = self.request_json("/api/health")
        self.assertEqual(health_status, 200)
        self.assertTrue(health["ok"])

        settings_status, settings_payload = self.request_json(
            "/api/option-bot-settings",
            {
                "contractPolicy": "only_long_call",
                "approvalMode": "automatic",
                "spreadFilter": "10%",
                "deltaTarget": "0.20",
                "expectedMove": ">=2",
                "watchlistSource": "mag7",
            },
        )
        self.assertEqual(settings_status, 200)
        self.assertEqual(settings_payload["optionBot"]["executionMode"], "automatic")
        self.assertEqual(settings_payload["optionBot"]["spreadFilter"], "10%")
        self.assertEqual(settings_payload["optionBot"]["deltaTarget"], "0.20")
        self.assertEqual(settings_payload["optionBot"]["expectedMove"], ">=2")
        self.assertEqual(settings_payload["optionBot"]["watchlistSource"], "mag7")
        self.assertEqual(settings_payload["optionBot"]["watchlistLabel"], "MAG7-Watchlist Options")

        control_status, control_payload = self.request_json("/api/option-bot-control", {"state": "Running"})
        self.assertEqual(control_status, 200)
        self.assertEqual(control_payload["optionBot"]["state"], "Running")

        dashboard_status, dashboard = self.request_json("/api/status")
        self.assertEqual(dashboard_status, 200)
        self.assertEqual(dashboard["optionBot"]["state"], "Running")
        self.assertEqual(dashboard["optionSupervisorReport"]["mode"], "advisory_only")
        self.assertFalse(dashboard["optionSupervisorReport"]["authority"]["canPlaceOrders"])
        self.assertIn("LLM Supervisor Agent", [agent["name"] for agent in dashboard["optionBot"]["agents"]])
        self.assertIn("optionPositions", dashboard)

        manage_status, manage_payload = self.request_json("/api/manage-option-positions", {}, method="POST")
        self.assertEqual(manage_status, 200)
        self.assertEqual(manage_payload["result"]["status"], "idle")
        self.assertEqual(manage_payload["result"]["managed"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
