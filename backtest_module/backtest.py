# backtest.py
import random, itertools
import pandas as pd
from price_loader import load_eth_prices
from dyson_pool import DysonPool, INIT_ETH
from time_utils import tm, random_time_in_day
from datetime import datetime, timezone, time

MIN_ETH = 0.01
MAX_ETH = 0.5
MIN_USDC = 50
MAX_USDC = 2000
MAX_USERS = 10
SEED = 42
random.seed(SEED)


class BacktestRunner:
    def __init__(self, days: int, basis=0.5):
        self.days = days
        self.prices_df = (
            load_eth_prices(days).drop_duplicates("date").reset_index(drop=True)
        )
        first_price = self.prices_df.price_usd.iloc[0]
        self.pool = DysonPool(INIT_ETH, INIT_ETH * first_price, basis)
        self.deposits, self.withdraws, self.daily = [], [], []

    def _simulate_day(self, day_idx, price, utc_date: datetime):

        # 1. withdraw
        for note, amt0, amt1 in self.pool.withdraw_due(utc_date, price):
            cost = (note.in0 * price) + note.in1    # in_eth * current_price + in_usdc
            revenue = (amt0 * price) + amt1    
            self.withdraws.append(
                {
                    "note_id": note.id,
                    "day": day_idx,
                    "amt0": amt0,
                    "amt1": amt1,
                    "price_out": price,
                    "pnl_usd": revenue - cost,
                    "pnl_ratio": (revenue - cost) / cost if cost > 0 else 0,
                }
            )
        # 2. deposits
        n_users = random.randint(1, MAX_USERS)
        for _ in range(n_users):
            single = random.choice([True, False])
            if single:
                if random.random() < 0.5:
                    in0, in1 = round(random.uniform(MIN_ETH, MAX_ETH), 4), 0
                else:
                    in0, in1 = 0, round(random.uniform(50, 2000), 2)
            else:
                in0 = round(random.uniform(MIN_ETH, MAX_ETH), 4)
                in1 = round(in0 * price, 2)
            lock = random.randint(1, 30)
            tm.setCurrentTime(random_time_in_day(utc_date))
            nid, n0, n1, premium, due, duration_sec, q_old, q_new = self.pool.deposit(
                in0, in1, lock, day_idx, price
            )
            self.deposits.append(
                {
                    "note_id": nid,
                    "day": day_idx,
                    "datetime": tm.getCurrentTime(),
                    "lock": lock,
                    "due": due,
                    "duration_sec": duration_sec,
                    "price_in": price,
                    "in0": in0,
                    "in1": in1,
                    "note0": n0,
                    "note1": n1,
                    "premium_ratio": premium,
                    "q_old": q_old,
                    "q_new": q_new,
                    "new_x": self.pool.x,
                    "new_y": self.pool.y,
                }
            )

    def run(self):
        for d, (row_idx, row) in enumerate(self.prices_df.iterrows(), start=1):
            price = row.price_usd
            utc_date = datetime.combine(row.date, time.min, tzinfo=timezone.utc)
            self._simulate_day(d, price, utc_date)
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
