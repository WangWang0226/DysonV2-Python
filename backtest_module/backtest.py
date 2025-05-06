# backtest.py
import random, itertools
import pandas as pd
from price_loader import load_eth_prices
from dyson_pool import DysonPool
from time_utils import tm, random_time_in_day
from datetime import datetime, timezone, time


class BacktestRunner:

    def __init__(self, init_eth, main_days: int, basis=0.5, w_factor=1, scale: int = 1):
        self.hp = {  # ←① 超參數集中放在一個 dict
            "MIN_ETH": 0.01, # 單筆最小 ETH 存款
            "MAX_ETH": 0.5, # 單筆最大 ETH 存款
            "MIN_USDC": 50, # 單筆最小 USDC 存款
            "MAX_USDC": 2000, # 單筆最大 USDC 存款
            "MAX_USERS": 10, # 單日最大存款人數
            "BASIS": basis,
            "W_FACTOR": w_factor,
            "SCALE": scale,
            "SEED": 42,
        }
        random.seed(self.hp["SEED"])

        """
        scale = x ：平均金額 ÷ x，筆數 × x
        """
        self.scale = max(1, int(scale))

        """
        main_days : 真正要評估的存款期間
        cool_down : 只允許 withdraw 的天數
        """
        self.main_days = main_days
        self.cool_down = 30
        total_days = main_days + self.cool_down  # 多抓 30 天價格

        self.prices_df = (
            load_eth_prices(total_days).drop_duplicates("date").reset_index(drop=True)
        )
        first_price = self.prices_df.price_usd.iloc[0]
        self.INIT_ETH = init_eth
        self.INIT_USDC = init_eth * first_price
        self.pool = DysonPool(init_eth, self.INIT_USDC, basis, w_factor)
        self.deposits, self.withdraws, self.daily = [], [], []

    def get_info(self) -> dict:
        return {
            # 回測區間
            "start_date"       : self.prices_df.date.iloc[0],
            "deposit_end_date" : self.prices_df.date.iloc[-self.cool_down],
            "total_end_date"   : self.prices_df.date.iloc[-1],
            # 池子初始值
            "init_eth"  : self.INIT_ETH,
            "init_usdc" : self.INIT_USDC,
            # 超參數
            **self.hp,                       
        }

    def _simulate_day(self, day_idx, price, utc_date: datetime, allow_deposit: bool):

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
        if not allow_deposit:
            return

        n_users = random.randint(1, self.hp["MAX_USERS"]) * self.scale
        for _ in range(n_users):
            single = random.choice([True, False])
            if single:
                if random.random() < 0.5:
                    in0, in1 = (
                        round(
                            random.uniform(self.hp["MIN_ETH"], self.hp["MAX_ETH"])
                            / self.scale,
                            4,
                        ),
                        0,
                    )
                else:
                    in0, in1 = 0, round(
                        random.uniform(self.hp["MIN_USDC"], self.hp["MAX_USDC"]) / self.scale, 2
                    )
            else:
                in0 = round(
                    random.uniform(self.hp["MIN_ETH"], self.hp["MAX_ETH"]) / self.scale,
                    4,
                )
                in1 = round(in0 * price, 2)

            if in0 == 0 and in1 == 0:  # 避免極小數被 round 成 0
                continue
            
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
            allow_deposit = d <= self.main_days
            self._simulate_day(d, price, utc_date, allow_deposit)
            self.daily.append(self.pool.snapshot(d, price))
        return (
            pd.DataFrame(self.deposits),
            pd.DataFrame(self.withdraws),
            pd.DataFrame(self.daily),
        )

    def get_start_end_dates(self):
        start_date = self.prices_df.date.iloc[0]
        end_date = self.prices_df.date.iloc[-self.cool_down]
        return start_date, end_date
