"""Tiny dip-ladder bot for the make.fun Solana bars replay.

A long-only paper-trading bot for hourly bars of FARTCOIN, JITOSOL, POPCAT,
RAY, SOL, WETH and WIF. It never touches a wallet, a network, the clock or
the environment: every decision comes from the bars the harness hands it,
and the same bars always give the same orders.

The idea, measured on the kit's dev window before it was written down: this
market mean-reverts on the hourly scale. Sharp drops below a slow moving
average bounce; chasing rallies does not pay here. So the bot buys dips in
three growing steps and lets every position go on a short leash:

  * Probe: a symbol 0.9% or more below its 12-hour EMA gets a small $75
    probe buy, just enough to keep the bot in the market and trading.
  * Ladder: a symbol 2% / 3% / 4% below its 24-hour EMA gets scaled-in
    buys of $500 / $1,000 / $1,000, topped up toward the 25% per-symbol
    weight cap the harness enforces.
  * Exits, per position: take profit at +4% over cost, a trailing stop
    1.5% below the peak value/cost, a stop-loss 2% under cost once the
    ladder is in (5% while only a probe is held), a max hold of 8 bars,
    and an exit when the close recovers to the 24-hour EMA once the
    ladder is in.

Orders are decided on a bar's close and fill at the next bar's open, so
the bot never trades on information it does not have yet. The EMAs are
seeded with the mean of their first span of closes, the way the house
baselines seed theirs, so the first day of a window is spent building
history instead of guessing from a single bar.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

# The universe, in the order the harness lists it.
SYMBOLS = ("FARTCOIN", "JITOSOL", "POPCAT", "RAY", "SOL", "WETH", "WIF")

# Sizing is in micro-USDC (the harness's quote units): $75 / $500 / $1,000.
PROBE_NOTIONAL = 75_000_000
LADDER_NOTIONAL = (75_000_000, 500_000_000, 1_000_000_000, 1_000_000_000)

PROBE_DIP = 0.009   # probe entry: 0.9% below the 12-hour EMA
DIP_1 = 0.02        # ladder step 1: 2% below the 24-hour EMA
DIP_2 = 0.03        # ladder step 2: 3% below the 24-hour EMA
DIP_3 = 0.04        # ladder step 3: 4% below the 24-hour EMA

TAKE_PROFIT = 0.04  # exit at +4% over the position's cost
TRAIL = 0.015       # trailing stop: 1.5% below the peak value/cost
STOP = 0.02         # stop-loss once the ladder is in: 2% under cost
STOP_PROBE = 0.05   # stop-loss while only a probe is held: 5% under cost
MAX_HOLD = 8        # max bars to hold a position
MAX_SLIPPAGE_BPS = 100  # 1% past the close the order was decided on

MAX_POSITIONS = 10
MAX_ORDERS_PER_BAR = 16
MAX_WEIGHT_BPS = 2500
MIN_NOTIONAL = 1_000_000
BPS = 10_000


class Ema:
    """One exponential moving average, seeded like the house baselines:
    the mean of its first span of closes, then 2 / (span + 1) of the way
    to each new close."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def push(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class DipLadder:
    """The bot's whole memory: two EMAs per symbol, and for each open
    position its peak value/cost, the bar it was (last) bought on, and
    which ladder step it has reached."""

    def __init__(self):
        self.fast = {s: Ema(12) for s in SYMBOLS}
        self.slow = {s: Ema(24) for s in SYMBOLS}
        self.peak = {}
        self.entry_bar = {}
        self.tier = {}

    def update(self, bars):
        """Fold a batch's bars into the EMAs. Runs on every batch, warm-up
        included, so the indicators are seeded before the first trade."""
        for bar in bars:
            symbol = bar["symbol"]
            if symbol not in self.fast:
                continue
            close = float(int(bar["c"]))
            self.fast[symbol].push(close)
            self.slow[symbol].push(close)

    def decide(self, state):
        """The orders for this bar's close, sells first, then buys, in
        universe order. Mirrors the replay's own checks (cash, weight cap,
        open positions) so an order the market would refuse is never sent."""
        closes = {}
        ema12 = {}
        ema24 = {}
        for symbol in SYMBOLS:
            close = state.close(symbol)
            if close is None:
                continue
            closes[symbol] = close
            ema12[symbol] = self.fast[symbol].value
            ema24[symbol] = self.slow[symbol].value

        orders = []
        for symbol in SYMBOLS:
            if symbol not in closes:
                continue
            close = closes[symbol]
            slow = ema24[symbol]
            dip24 = (slow - close) / slow if slow is not None else -1.0
            held = state.positions.get(symbol)
            if held is not None:
                self._held(symbol, state, close, slow, dip24, held, orders)
                continue
            if dip24 >= DIP_1:
                orders.append(("BUY", symbol, 1))
            elif ema12[symbol] is not None and (ema12[symbol] - close) / ema12[symbol] >= PROBE_DIP:
                orders.append(("BUY", symbol, 0))
        return self._size(state, orders)

    def _held(self, symbol, state, close, slow, dip24, held, orders):
        """Decide what happens to an open position: exit it, add a ladder
        step to it, or leave it alone."""
        value = state.value(symbol, held.qty)
        ratio = value / held.cost
        peak = self.peak.get(symbol, 0.0)
        if ratio > peak:
            peak = ratio
            self.peak[symbol] = peak
        age = state.seq - self.entry_bar.get(symbol, state.seq)
        tier = self.tier.get(symbol, 0)
        stop = 1.0 - STOP if tier >= 2 else 1.0 - STOP_PROBE
        exits = (
            ratio >= 1.0 + TAKE_PROFIT
            or ratio <= peak * (1.0 - TRAIL)
            or ratio <= stop
            or age >= MAX_HOLD
        )
        if tier >= 1 and slow is not None and close >= slow:
            exits = True
        if exits:
            orders.append(
                {"side": "sell", "symbol": symbol, "qty": held.qty,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}
            )
            self.peak.pop(symbol, None)
            self.entry_bar.pop(symbol, None)
            self.tier.pop(symbol, None)
            return
        if tier == 0 and dip24 >= DIP_1:
            orders.append(("BUY", symbol, 1))
        elif tier == 1 and dip24 >= DIP_2:
            orders.append(("BUY", symbol, 2))
        elif tier == 2 and dip24 >= DIP_3:
            orders.append(("BUY", symbol, 3))

    def _size(self, state, orders):
        """Turn the wanted buys into sized orders the replay accepts: sells
        first, then buys in universe order, each buy capped by the weight
        room, the cash left, and the open-position cap."""
        sells = [o for o in orders if isinstance(o, dict)]
        buys = [o for o in orders if isinstance(o, tuple)]
        out = sells[:MAX_ORDERS_PER_BAR]
        by_weight = MAX_WEIGHT_BPS * state.equity // BPS
        room = MAX_POSITIONS - len(state.positions)
        cash = state.cash
        for _, symbol, tier in buys:
            if room <= 0 or len(out) >= MAX_ORDERS_PER_BAR:
                break
            amount = LADDER_NOTIONAL[tier]
            if tier > 0:
                held = state.positions.get(symbol)
                held_value = state.value(symbol, held.qty) if held is not None else 0
                if by_weight - held_value < amount:
                    amount = by_weight - held_value
            size = amount
            if by_weight < size:
                size = by_weight
            if cash < size:
                size = cash
            if size < MIN_NOTIONAL:
                break
            out.append(
                {"side": "buy", "symbol": symbol, "notional_quote_units": size,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}
            )
            cash -= size
            if symbol not in state.positions:
                room -= 1
            # A buy (re)starts the hold clock and the trailing peak: the
            # peak is the position's value/cost as it stands now, before
            # this buy fills at the next open.
            self.entry_bar[symbol] = state.seq
            held = state.positions.get(symbol)
            if held is not None:
                self.peak[symbol] = state.value(symbol, held.qty) / held.cost
            else:
                self.peak[symbol] = 1.0
            self.tier[symbol] = tier
        return out


BOT = DipLadder()


def on_batch(state, batch):
    """One reply per batch: fold the bars into the EMAs, then decide.

    While warming up every order would be rejected, so the bot only
    watches and lets the indicators seed. Diagnostics, if any ever go
    here, belong on stderr: stdout carries replies only.
    """
    BOT.update(batch["bars"])
    if state.warming_up:
        return []
    return BOT.decide(state)


makefun_bars.run(on_batch)
