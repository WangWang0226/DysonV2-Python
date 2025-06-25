import random, itertools
import pandas as pd
from price_loader import load_eth_prices
from dyson_pool import DysonPool
from time_utils import tm, random_time_in_day
from datetime import datetime, timezone, time

class BacktestRunner:

    def __init__(
        self,
        init_eth,
        main_days: int,
        basis=0.5,
        w_factor=1,
        scale: int = 1,
        forward_or_reverse_prob=0.5,
        forward_single_side_prob=0.5,
        forward_eth_side_prob=0.5,
        reverse_single_side_prob=0.5,
        reverse_eth_side_prob=0.5,
        rebalance_interval=1,  # days
    ):
        self.hp = {  # ←① 超參數集中放在一個 dict
            "MIN_ETH": 0.01,  # 單筆最小 ETH 存款
            "MAX_ETH": 0.5,  # 單筆最大 ETH 存款
            "MIN_USDC": 50,  # 單筆最小 USDC 存款
            "MAX_USDC": 2000,  # 單筆最大 USDC 存款
            "MAX_USERS": 10,  # 單日最大存款人數
            "FORWARD_OR_REVERSE_PROB": forward_or_reverse_prob,
            "FORWARD_SINGLE_SIDE_PROB": forward_single_side_prob,
            "FORWARD_ETH_SIDE_PROB": forward_eth_side_prob,  # 正向雙幣存款時，ETH 存款的機率
            "REVERSE_SINGLE_SIDE_PROB": reverse_single_side_prob,
            "REVERSE_ETH_SIDE_PROB": reverse_eth_side_prob,  # 反向雙幣存款時，ETH 存款的機率
            "REBALANCE_INTERVAL": rebalance_interval,  # days
            "BASIS": basis,
            "W_FACTOR": w_factor,
            "SCALE": scale,
            "SEED": 42,
        }
        random.seed(self.hp["SEED"])

        self.scale = max(1, int(scale))
        self.main_days = main_days
        self.cool_down = 30
        total_days = main_days + self.cool_down

        self.prices_df = (
            load_eth_prices(total_days).drop_duplicates("date").reset_index(drop=True)
        )
        first_price = self.prices_df.price_usd.iloc[0]
        self.INIT_ETH = init_eth
        self.INIT_USDC = init_eth * first_price
        self.pool = DysonPool(init_eth, self.INIT_USDC, basis, w_factor)

        # Initialize as empty DataFrames with specified columns
        self.deposits = pd.DataFrame(
            columns=[
                "note_id",
                "day",
                "datetime",
                "lock",
                "due",
                "duration_sec",
                "price_in",
                "in0",
                "in1",
                "note0",
                "note1",
                "note0(+Premium)",
                "note1(+Premium)",
                "premium_ratio",
                "q_old",
                "q_new",
                "new_x",
                "new_y",
            ]
        )
        self.withdraws = pd.DataFrame(
            columns=[
                "note_id",
                "day",
                "amt0",
                "amt1",
                "price_out",
                "pnl_usd",
                "pnl_ratio",
            ]
        )
        self.reverse_deposits = pd.DataFrame(
            columns=[
                "note_id",
                "day_deposit",
                "datetime",
                "lock",
                "duration_sec",
                "due",
                "due_idx",
                "price_in",
                "m_n",
                "strike",
                "delta_x_y",
                "revert_put",
                "revert_call",
                "premium_ratio",
                "q_old_new",
                "new_x_y",
                "exercise_result"
                "day_revert_exercising",
                "price_when_revert_exercising",
                "swap_in_revert_exercising",
                "swap_out_revert_exercising",
            ]
        )

        self.daily = pd.DataFrame(
            columns=["day", "price", "reserve_eth", "reserve_usdc", "k"]
        )

    def get_info(self) -> dict:
        return {
            # 回測區間
            "start_date": self.prices_df.date.iloc[0],
            "deposit_end_date": self.prices_df.date.iloc[-self.cool_down],
            "total_end_date": self.prices_df.date.iloc[-1],
            # 池子初始值
            "init_eth": self.INIT_ETH,
            "init_usdc": self.INIT_USDC,
            # 超參數
            **self.hp,
        }

    def _simulate_day(self, day_idx, price, utc_date: datetime, allow_deposit: bool):

        # 1. Exercise option of Reverse deposits
        today = utc_date.timestamp() / 86400

        for nid in list(self.pool.notes_reverse.keys()):
            note = self.pool.notes_reverse[nid]
            if note and note.due == today - 1:  # Exercise one day before due
                # 根據 nid 更新 reverse_deposits 中的欄位
                idx = self.reverse_deposits.index[self.reverse_deposits["note_id"] == nid][0]
                price_in = self.reverse_deposits.loc[idx, "price_in"]
                price_go_up = price > price_in
                is_double_side = note.m > 0 and note.n > 0
                if is_double_side:
                    # 雙邊存款必為一側行權，一側不行權
                    option_type = "call" if price_go_up else "put"
                    _, swap_in, swap_out = self.pool.exercise_option(nid, option_type)

                    self.reverse_deposits.loc[idx, "exercise_result"] = "EXERCISE_" + option_type.upper()

                else:
                    # 單邊存款可能行權或不行權
                    if note.m > 0:
                        # 單邊 ETH 存款，看漲
                        if price_go_up:
                            # 行權
                            _, swap_in, swap_out = self.pool.exercise_option(nid, "call")
                            self.reverse_deposits.loc[idx, "exercise_result"] = "EXERCISE_CALL"
                        else :
                            # 不行權
                            self.reverse_deposits.loc[idx, "exercise_result"] = "NOT_EXERCISED"

                    elif note.n > 0:
                        # 單邊 USDC 存款
                        if not price_go_up:
                            # 行權
                            _, swap_in, swap_out = self.pool.exercise_option(nid, "put")
                            self.reverse_deposits.loc[idx, "exercise_result"] = "EXERCISE_PUT"
                        else:
                            # 不行權
                            self.reverse_deposits.loc[idx, "exercise_result"] = "NOT_EXERCISED"

                # 記錄行權結果
                if self.reverse_deposits.loc[idx, "exercise_result"] != "NOT_EXERCISED":
                    self.reverse_deposits.loc[idx, "day_exercise"] = day_idx
                    self.reverse_deposits.loc[idx, "price_when_exercise"] = price
                    self.reverse_deposits.loc[idx, "swap_in_exercise"] = swap_in
                    self.reverse_deposits.loc[idx, "swap_out_exercise"] = swap_out

        # 2. Forward dual investors withdraw positions
        for nid in list(self.pool.notes_forward.keys()):
            note = self.pool.notes_forward[nid]
            if note.due <= today:
                note, amt0, amt1 = self.pool.withdraw_due(utc_date, price, nid)
                idx = self.deposits.index[self.deposits["note_id"] == nid][0]
                in0 = self.deposits.loc[idx, "in0"]
                in1 = self.deposits.loc[idx, "in1"]

                # Calculate User PnL
                cost = (in0 * price) + in1
                revenue = (amt0 * price) + amt1
                new_withdraw = pd.DataFrame(
                    [
                        {
                            "note_id": note.id,
                            "day": day_idx,
                            "amt0": amt0,
                            "amt1": amt1,
                            "price_out": price,
                            "pnl_usd": revenue - cost,
                            "pnl_ratio": (revenue - cost) / cost if cost > 0 else 0,
                        }
                    ]
                )
                self.withdraws = pd.concat(
                    [self.withdraws, new_withdraw], ignore_index=True
                )

        if allow_deposit:
            n_users = random.randint(1, self.hp["MAX_USERS"]) * self.scale
            min_eth = self.hp["MIN_ETH"]
            max_eth = self.hp["MAX_ETH"]
            min_usdc = self.hp["MIN_USDC"]
            max_usdc = self.hp["MAX_USDC"]
            for _ in range(n_users):
                forward_or_reverse = (
                    random.random() < self.hp["FORWARD_OR_REVERSE_PROB"]
                )

                tm.setCurrentTime(random_time_in_day(utc_date))
                lock = random.randint(1, 30)

                # 3. Forward deposits
                if forward_or_reverse:
                    single = random.random() < self.hp["FORWARD_SINGLE_SIDE_PROB"]
                    eth_side = random.random() < self.hp["FORWARD_ETH_SIDE_PROB"]
                    if single:
                        if eth_side:
                            in0 = round(random.uniform(min_eth, max_eth) / self.scale, 4)
                            in1 = 0
                        else:
                            in0 = 0
                            in1 = round(random.uniform(min_usdc, max_usdc) / self.scale, 2)
                    else:
                        in0 = round(random.uniform(min_eth, max_eth) / self.scale, 4)
                        in1 = round(in0 * price, 2)

                    (
                        nid,
                        note0,
                        note1,
                        note0_with_prem,
                        note1_with_prem,
                        premium,
                        due,
                        duration_sec,
                        q_old,
                        q_new,
                    ) = self.pool.deposit(in0, in1, lock, price)
                    new_deposit = pd.DataFrame(
                        [
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
                                "note0": note0,
                                "note1": note1,
                                "note0(+Premium)": note0_with_prem,
                                "note1(+Premium)": note1_with_prem,
                                "premium_ratio": premium,
                                "q_old": q_old,
                                "q_new": q_new,
                                "new_x": self.pool.x,
                                "new_y": self.pool.y,
                            }
                        ]
                    )
                    self.deposits = pd.concat(
                        [self.deposits, new_deposit], ignore_index=True
                    )

                else:  # Reverse dual deposits
                    single = random.random() < self.hp["REVERSE_SINGLE_SIDE_PROB"]
                    eth_side = random.random() < self.hp["REVERSE_ETH_SIDE_PROB"]
                    if single:
                        if eth_side:
                            m = round(random.uniform(min_eth, max_eth) / self.scale, 4)
                            n = 0
                        else:
                            m = 0
                            n = round(
                                random.uniform(min_usdc, max_usdc) / self.scale, 2
                            )
                    else:
                        m = round(random.uniform(min_eth, max_eth) / self.scale, 4)
                        n = round(random.uniform(min_usdc, max_usdc) / self.scale, 2)

                    try:
                        (
                            nid,
                            m,
                            n,
                            strike,
                            delta_x,
                            delta_y,
                            prem_ratio,
                            due,
                            duration_sec,
                            q_old,
                            q_new,
                        ) = self.pool.reverse_deposit(m, n, lock, price)

                        revert_put = (m * strike, m)
                        revert_call = (n / strike, n)

                        new_reverse_deposit = pd.DataFrame(
                            [
                                {
                                    "note_id": nid,
                                    "day_deposit": day_idx,
                                    "datetime": tm.getCurrentTime(),
                                    "lock": lock,
                                    "duration_sec": duration_sec,
                                    "due": due,
                                    "due_idx": day_idx + lock,
                                    "price_in": price,
                                    "m_n": (m, n),
                                    "strike": strike,
                                    "delta_x_y": (delta_x, delta_y),
                                    "revert_put": revert_put,
                                    "revert_call": revert_call,
                                    "premium_ratio": prem_ratio,
                                    "q_old_new": (q_old, q_new),
                                    "new_x_y": (self.pool.x, self.pool.y,)
                                }
                            ]
                        )
                        self.reverse_deposits = pd.concat(
                            [self.reverse_deposits, new_reverse_deposit],
                            ignore_index=True,
                        )
                    except ValueError as e:
                        if "Insufficient liquidity for reverse deposit" in str(e):
                            # Skip this deposit if pool has insufficient liquidity
                            continue

        # 4. Rebalance pool reserves
        rebalance_interval = self.hp["REBALANCE_INTERVAL"]
        if day_idx % rebalance_interval == 0:
            # Rebalance pool reserves every rebalance_interval days
            self.pool.rebalance(price)
        
    def run(self):
        for d, (row_idx, row) in enumerate(self.prices_df.iterrows(), start=1):
            price = row.price_usd
            utc_date = datetime.combine(row.date, time.min, tzinfo=timezone.utc)
            allow_deposit = d <= self.main_days
            self._simulate_day(d, price, utc_date, allow_deposit)
            new_daily = pd.DataFrame([self.pool.snapshot(d, price)])
            self.daily = pd.concat([self.daily, new_daily], ignore_index=True)

        return (
            self.deposits,
            self.withdraws,
            self.reverse_deposits,
            self.daily,
        )

    def get_start_end_dates(self):
        start_date = self.prices_df.date.iloc[0]
        end_date = self.prices_df.date.iloc[-self.cool_down]
        return start_date, end_date
