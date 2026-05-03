import unittest

from fastapi.testclient import TestClient

from dashboard.orchestrator import DashboardOrchestrator


class DashboardAppTests(unittest.TestCase):
    def test_root_serves_plain_html_dashboard(self) -> None:
        from dashboard.app import create_app

        app = create_app(DashboardOrchestrator(tracker_port=9390, first_peer_port=9391))
        with TestClient(app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Blockchain Dashboard", response.text)
        self.assertIn("/ws/state", response.text)

    def test_state_and_wallet_routes_return_json(self) -> None:
        from dashboard.app import create_app

        app = create_app(DashboardOrchestrator(tracker_port=9400, first_peer_port=9401))
        with TestClient(app) as client:
            state = client.get("/api/state")
            self.assertEqual(state.status_code, 200)
            self.assertEqual(state.json()["tracker"]["port"], 9400)

            wallet = client.post(
                "/api/wallets",
                json={"name": "Alice", "initial_balance": 100},
            )
            self.assertEqual(wallet.status_code, 200)
            self.assertEqual(wallet.json()["name"], "Alice")
            self.assertEqual(wallet.json()["initial_balance"], 100)

    def test_mining_peer_requires_miner_wallet(self) -> None:
        from dashboard.app import create_app

        app = create_app(DashboardOrchestrator(tracker_port=9420, first_peer_port=9421))
        with TestClient(app) as client:
            response = client.post(
                "/api/peers",
                json={"start_mining": True, "miner_wallet_id": None},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "miner_wallet_id is required")

    def test_state_websocket_sends_snapshot(self) -> None:
        from dashboard.app import create_app

        app = create_app(DashboardOrchestrator(tracker_port=9410, first_peer_port=9411))
        with TestClient(app) as client:
            with client.websocket_connect("/ws/state") as websocket:
                state = websocket.receive_json()

        self.assertEqual(state["tracker"]["port"], 9410)
        self.assertEqual(state["peers"], [])


if __name__ == "__main__":
    unittest.main()
