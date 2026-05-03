# bitcoin_final

Computer Networks Final Project: Bitcoin

## Interface

The `viz` interface runs a local FastAPI server with a plain HTML dashboard for driving the demo network.

```bash
uv sync
uv run blockchain-dashboard
```

Open `http://127.0.0.1:8000` in a browser. From the dashboard, start the tracker, create wallets, add peers, enable mining for selected peers, submit transactions, and watch peer state, mempools, balances, and blocks update live.
