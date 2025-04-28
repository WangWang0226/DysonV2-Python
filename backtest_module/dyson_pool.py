# dyson_pool.py
import itertools, math
from collections import defaultdict
from time_utils import compute_due_time_and_duration, tm
from dataclasses import dataclass

INIT_ETH = 10
W_FACTOR = 1  # w = k * W_FACTOR


@dataclass
class Note:
    id: int
    token0Amt: float
    token1Amt: float
    due: float
    day_create: float
    in0: float
    in1: float
    price_in: float


class DysonPool:
    def __init__(self, init_eth: float, init_usdc: float, basis: float):
        self.x, self.y = init_eth, init_usdc
        self.k_last = math.sqrt(self.x * self.y)
        self.w = self.k_last * W_FACTOR
        self.basis = basis

        self.q_by_due: defaultdict[int, float] = defaultdict(float)
        self.notes: dict[int, Note] = {}
        self._seq = itertools.count()

    def rebalance(self, price):
        """50 / 50 資產再平衡"""
        eth_val = self.x * price
        total_val = eth_val + self.y
        target = total_val / 2
        if eth_val > target:
            diff = (eth_val - target) / price
            return self.x - diff, self.y + (eth_val - target)
        elif self.y > target:
            diff = self.y - target
            return self.x + diff / price, self.y - diff
        return self.x, self.y

    def deposit(self, in0, in1, lock_days, today, price):
        assert in0 > 0 or in1 > 0

        due, duration_sec = compute_due_time_and_duration(lock_days)

        k_before = math.sqrt(self.x * self.y)
        k_after = math.sqrt((self.x + in0) * (self.y + in1))
        diff = k_after - k_before
        Q_sq = 4 * diff * diff
        Q = math.sqrt(Q_sq)

        # note 份額
        if in0 * self.y > in1 * self.x:
            ratio = (in1 * self.x) / self.y if self.y else 0
            note0 = in0 + ratio
            note1 = Q_sq / note0
        else:
            ratio = (in0 * self.y) / self.x if self.x else 0
            note1 = in1 + ratio
            note0 = Q_sq / note1

        # premium
        q_old, q_new = self.q_by_due[due], self.q_by_due[due] + Q
        a, b = q_old / self.w, q_new / self.w
        discount = (math.log2(b + 1) - math.log2(a + 1)) * math.log(2) / (b - a or 1)
        prem_ratio = 0.4 * self.basis * math.sqrt(duration_sec / (365 * 86400)) * discount
        note0 *= 1 + prem_ratio
        note1 *= 1 + prem_ratio
        self.q_by_due[due] = q_new

        # 更新池子
        self.x += in0
        self.y += in1
        self.k_last = k_after
        self.x, self.y = self.rebalance(price)

        nid = next(self._seq)
        self.notes[nid] = Note(nid, note0, note1, due, today, in0, in1, price)
        return nid, note0, note1, prem_ratio, due, duration_sec, q_old, q_new

    def withdraw_due(self, utc_date, price):
        today = utc_date.timestamp() / 86400
        """領回所有到期 Note"""
        paid = []
        for nid in list(self.notes):
            n = self.notes[nid]
            if n.due <= today:
                num = (
                    self.x * n.token1Amt
                    + n.token0Amt * n.token1Amt
                    - self.y * n.token0Amt
                )
                ratio = max(
                    0,
                    min(
                        (
                            num / (2 * n.token0Amt * n.token1Amt)
                            if n.token0Amt and n.token1Amt
                            else 0
                        ),
                        1,
                    ),
                )
                amt0 = ratio * n.token0Amt
                amt1 = (1 - ratio) * n.token1Amt

                self.x -= amt0
                self.y -= amt1
                self.k_last = math.sqrt(self.x * self.y)
                self.x, self.y = self.rebalance(price)

                paid.append((n, amt0, amt1))
                del self.notes[nid]
        return paid

    def snapshot(self, day, price):
        return {
            "day": day,
            "price": price,
            "reserve_eth": self.x,
            "reserve_usdc": self.y,
            "k": math.sqrt(self.x * self.y),
        }
