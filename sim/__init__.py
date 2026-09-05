"""Trading-bot simulator: a paper-only operator console.

Runs the plan's daily cadence (signal -> validate -> risk gate -> PENDING
proposal -> approve -> risk gate again -> drift check -> fill at next open ->
bot-managed exits) against synthetic daily bars, and exposes history, trades,
orders, audit log and an "Auto" mode through a small web UI.

Synthetic data tests the *operations*, never the strategy (plan Rule 7).
"""
