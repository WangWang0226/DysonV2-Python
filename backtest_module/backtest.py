# backtest.py
import random, itertools
import pandas as pd
from price_loader import load_eth_prices
from dyson_pool import DysonPool, INIT_ETH

MAX_USERS = 10
SEED = 42
random.seed(SEED)


class BacktestRunner:
    def __init__(self, days: int, basis=0.5):
        self.days = days
        self.prices_df = load_eth_prices(days)
        first_price = self.prices_df.price_usd.iloc[0]
        self.pool = DysonPool(INIT_ETH, INIT_ETH * first_price, basis)
        self.deposits, self.withdraws, self.daily = [], [], []

    def _simulate_day(self, day_idx, price):
        # 1. withdraw
        for note, amt0, amt1 in self.pool.withdraw_due(day_idx, price):
            self.withdraws.append(
                {
                    "note_id": note.id,
                    "day": day_idx,
                    "amt0": amt0,
                    "amt1": amt1,
                    "price_out": price,
                    "pnl_usd": (amt0 * price + amt1)
                    - (note.in0 * note.price_in + note.in1),
                }
            )
        # 2. deposits
        n_users = random.randint(1, MAX_USERS)
        for _ in range(n_users):
            single = random.choice([True, False])
            if single:
                if random.random() < 0.5:
                    in0, in1 = round(random.uniform(0.01, 0.5), 4), 0
                else:
                    in0, in1 = 0, round(random.uniform(50, 2000), 2)
            else:
                in0 = round(random.uniform(0.01, 0.5), 4)
                in1 = round(in0 * price, 2)
            lock = random.randint(1, 30)
            nid, n0, n1, pr = self.pool.deposit(in0, in1, lock, day_idx, price)
            self.deposits.append(
                {
                    "note_id": nid,
                    "day": day_idx,
                    "in0": in0,
                    "in1": in1,
                    "note0": n0,
                    "note1": n1,
                    "premium_ratio": pr,
                    "lock": lock,
                    "price_in": price,
                }
            )

    def run(self):
        for d, price in enumerate(self.prices_df.price_usd, start=1):
            self._simulate_day(d, price)
            self.daily.append(self.pool.snapshot(d, price))
        return (
            pd.DataFrame(self.deposits),
            pd.DataFrame(self.withdraws),
            pd.DataFrame(self.daily),
        )
        
    def get_start_end_dates(self):
        start_date = self.prices_df.date.iloc[0]
        end_date = self.prices_df.date.iloc[-1]
        return start_date, end_date