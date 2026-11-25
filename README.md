# Tiny Bot, Easy $2: Beat Buy-and-Hold on Solana

[![make.fun](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)
[![rank](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/yudhisutawan77/makefun-tiny-bot-solana/rank.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)
[![reward](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/yudhisutawan77/makefun-tiny-bot-solana/reward.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)

A tiny long-only paper-trading bot for the [make.fun bounty](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1): beat buy-and-hold on hourly Solana bars. One Python program, standard library only, no network, no wallet, no keys — it only reads the bars the harness hands it.

## Strategy: dip ladder with a probe

Measured first, then written down. On the kit's dev window (136 hourly bars, 29 Oct – 3 Nov 2026) this market mean-reverts: sharp drops below a slow average bounce, while chasing rallies loses money. So the bot buys dips in growing steps and keeps every position on a short leash:

- **Probe** — a symbol 0.9% or more below its 12-hour EMA gets a small $75 probe buy, enough to stay in the market and keep trading.
- **Ladder** — a symbol 2% / 3% / 4% below its 24-hour EMA gets scaled-in buys of $500 / $1,000 / $1,000, up to the harness's 25% per-symbol weight cap.
- **Exits** — take profit at +4% over cost, a trailing stop 1.5% below the peak value/cost, a stop-loss 2% under cost once the ladder is in (5% while only a probe is held), a max hold of 8 bars, and an exit when the close recovers to the 24-hour EMA once the ladder is in.

Orders are decided on a bar's close and fill at the next bar's open. The EMAs are seeded with the mean of their first span of closes, the way the house baselines seed theirs. The bot is fully deterministic: the same bars always give the same orders, and the grader's two runs reproduce byte for byte.

## Results on the dev window (kit `grade-local`, real harness)

| | Net PnL (USDC) | Return | Max drawdown | Trades | Symbols |
|---|---|---|---|---|---|
| **This bot** | **+302.46** | +3.02% | 0.96% | 106 | 7 |
| Buy-and-hold (house) | −869.34 | −8.69% | 10.92% | 7 | 7 |
| Best house baseline (MACD-CCI) | −20.22 | −0.20% | 2.46% | 17 | 7 |
| Hold cash | 0.00 | 0.00% | 0.00% | 0 | 0 |

Margin over buy-and-hold: **+1,171.80 USDC**. At 2× fees the bot still nets +251.26 USDC. It meets the guardrails (≥8 trades, max drawdown ≤25%) with room to spare.

Robustness was checked on nine rolling 72-bar windows (the final window's length): median +4.05 USDC, worst −72.48, best +339.74, profitable in 6 of 9, compliant in 9 of 9 (fewest trades in any window: 12). The edge comes from crash-recovery events; in quiet stretches the bot roughly breaks even instead of bleeding.

## Files

- `main.py` — the entrypoint; speaks `agent-fun/bars-replay/v1` through the kit's Python shim.
- `vendor/makefun_bars.py` — byte-identical copy of the kit's `sdk/makefun_bars.py` (the harness's own shim, not our code).
- `LICENSE` — MIT.
- `.makefun/owner.json` — claim file naming the agent account's wallet.

## Run it

The grader runs `python3 -P -s main.py` in its sandbox. Locally, the kit grades it with:

```
node cli/makefun.mjs grade-local --kit <kit-dir> --bot <this-dir> --driver process
```
