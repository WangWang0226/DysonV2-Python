import itertools
import math
from dataclasses import dataclass
from collections import defaultdict
from enum import Enum
from time_utils import TimeManager
from typing import Dict, Tuple

@dataclass
class Note_forward:
    id: int
    in0: float  # 存入的 token0 數量
    in1: float  # 存入的 token1 數量
    note0: float  # 產生的 token0 票據數量
    note1: float  # 產生的 token1 票據數量
    Q: float  # 對應的 Q 值
    due: float

@dataclass
class Note_reverse:
    id: int
    m: float  # 使用者想要取出的 token0 數量
    n: float  # 使用者想要取出的 token1 數量
    strike: float  # 行權價 = (y - n) / (x - m)
    delta_x: float  # 實際池子 token0 變動量
    delta_y: float  # 實際池子 token1 變動量
    exercise_put: Tuple[float, float]  # 行使 put 時池子 (接收 token0, 支付 token1)
    exercise_call: Tuple[float, float]  # 行使 call 時池子 (接收 token1, 支付 token0)
    Q: float  # 對應的 Q 值 (負值)
    due: float


class DepositType(Enum):
    FORWARD = "forward"
    REVERSE = "reverse"


class DysonPool:
    def __init__(
        self, init_eth: float, init_usdc: float, basis: float, w_factor: float, tm: TimeManager
    ):
        self.x = init_eth  # ETH reserve
        self.y = init_usdc  # USDC reserve
        self.k_last = math.sqrt(self.x * self.y)
        self.w = self.k_last * w_factor
        self.basis = basis
        self.q_by_due = defaultdict(float)
        self.notes_forward: Dict[int, Note_forward] = {}
        self.notes_reverse: Dict[int, Note_reverse] = {}
        self._seq = itertools.count()
        self.tm = tm

    def _new_id(self) -> int:
        return next(self._seq)

    def _current_k(self) -> float:
        return math.sqrt(self.x * self.y)

    def _compute_Q_and_k1(
        self, delta_x: float, delta_y: float
    ) -> Tuple[float, float, float, float]:
        """
        計算給定變動下的 Q 及新的 k：
          k0 = sqrt(x*y)
          new_x = x + delta_x
          new_y = y + delta_y
          k1 = sqrt(new_x*new_y)
          Q  = k1 - k0
        返回 (Q, new_x, new_y, k1)
        """
        k0 = self._current_k()
        new_x = self.x + delta_x
        new_y = self.y + delta_y
        k1 = math.sqrt(new_x * new_y)
        Q = k1 - k0
        return Q, new_x, new_y, k1

    def swap(self, new_x: float, new_y: float) -> None:
        """
        任意更新池內 x,y，只要 new_x*new_y >= 原本 x*y
        """
        if new_x <= 0 or new_y <= 0:
            raise ValueError("swap 後的 x, y 必須為正數")
        if new_x * new_y < self.x * self.y:
            raise ValueError("swap 必須滿足 new_x*new_y >= old_x*old_y")
        self.x, self.y = new_x, new_y
        self.k_last = self._current_k() 

    def _calculate_discount(
        self, a: float, b: float, deposit_type: DepositType
    ) -> float:
        """Calculate discount factor based on deposit type"""
        if deposit_type == DepositType.FORWARD:
            adjustment = 1
        elif deposit_type == DepositType.REVERSE:
            adjustment = 0.5

        return (
                (math.log2(b + adjustment) - math.log2(a + adjustment)) * math.log(2) / (b - a or 1e-10)
            )

    def _calculate_reverse_deposit(
        self, m: float, n: float, premium: float
    ) -> tuple:
        """Calculate strike, delta_x, delta_y, and revert options for reverse deposit"""
        strike = (self.y + n) / (self.x + m)
        delta_x = m * (1 + premium) - n / strike
        delta_y = n * (1 + premium) - m * strike
        # Revert options
        return strike, delta_x, delta_y

    def deposit(self, in0: float, in1: float, lock_days: int) -> tuple:
        """Handle forward dual deposit"""
        assert in0 > 0 or in1 > 0, "At least one input must be positive"
        due, duration_sec = self.tm.compute_due_time_and_duration(lock_days)

        Q, new_x, new_y, k1 = self._compute_Q_and_k1(in0, in1)

        note_product = 4 * Q * Q

        if in0 * self.y > in1 * self.x:
            ratio = (in1 * self.x) / self.y if self.y else 0
            note0 = in0 + ratio
            note1 = note_product / note0
        else:
            ratio = (in0 * self.y) / self.x if self.x else 0
            note1 = in1 + ratio
            note0 = note_product / note1

        q_old = self.q_by_due[due]
        q_new = q_old + Q
        a, b = q_old / self.w, q_new / self.w

        discount = self._calculate_discount(a, b, DepositType.FORWARD)
        prem_ratio = (
            0.4 * self.basis * math.sqrt(duration_sec / (365 * 86400)) * discount
        )
        note0_with_prem = note0 * (1 + prem_ratio)
        note1_with_prem = note1 * (1 + prem_ratio)
        self.q_by_due[due] = q_new

        nid = self._new_id()
        self.notes_forward[nid] = Note_forward(nid, in0, in1, note0_with_prem, note1_with_prem, Q, due)

        self.x, self.y = new_x, new_y
        self.k_last = k1
        return nid, note0, note1, note0_with_prem, note1_with_prem, prem_ratio, due, duration_sec, q_old, q_new

    def withdraw(self, utc_date, nid: int) -> list:
        today = utc_date.timestamp() / 86400

        nf = self.notes_forward.get(nid)
        if not nf:
            raise KeyError(f"正向存款 note_id={nid} 不存在")

        if nf.due <= today:
            num = self.x * nf.note1 + nf.note0 * nf.note1 - self.y * nf.note0
            denom = 2 * nf.note0 * nf.note1
            ratio = (num / denom) if denom > 0 else 0.0
            ratio = max(0.0, min(ratio, 1.0))
            amt0 = ratio * nf.note0
            amt1 = (1 - ratio) * nf.note1

            self.x -= amt0
            self.y -= amt1
            self.k_last = self._current_k()
            del self.notes_forward[nid]
            return (amt0, amt1)
        else:
            raise ValueError("Cannot withdraw before due date")

    def reverse_deposit(self, m: float, n: float, lock_days: int) -> tuple:
        """Handle reverse dual deposit with immediate exercise"""
        if m < 0 or n < 0 or m >= self.x or n >= self.y:
            raise ValueError("reverse_deposit 時 m, n 必須 ≥ 0 且 m < x, n < y")

        due, duration_sec = self.tm.compute_due_time_and_duration(lock_days)    

        # 1) 根據移除後儲備計算行權價
        strike = (self.y - n) / (self.x - m)

        # 2) 虛擬移除 (-m, -n) 計算負的 Q
        Q, _, _, _ = self._compute_Q_and_k1(-m, -n)

        q_old = self.q_by_due[due]
        q_new = q_old + Q
        if q_new < 0:
            raise ValueError("reverse_deposit 會使 q < 0")
        a, b = q_old / self.w, q_new / self.w

        discount = self._calculate_discount(a, b, DepositType.REVERSE)
        prem_ratio = (
            0.4 * self.basis * math.sqrt(duration_sec / (365 * 86400)) * discount
        )

        # 3) 真實移動量：池子實際收/付資產
        dx = (n / strike) * (1 + prem_ratio) - m
        dy = (m * strike) * (1 + prem_ratio) - n

        # 4) 更新儲備與 q (不做 rebalance)
        self.x += dx
        self.y += dy
        self.q_by_due[due] = q_new
        self.k_last = self._current_k()

        nid = self._new_id()
        exercise_call = (n, n / strike)  # 行使 call 時，池子收 token1=n, 付 token0=n/strike
        exercise_put = (m, m * strike)  # 行使 put 時，池子收 token0=m, 付 token1=m*strike

        self.notes_reverse[nid] = Note_reverse(
            nid, m, n, strike, dx, dy, exercise_put, exercise_call, Q, due
        )
        # input m,n = (1, 0): put  , exercise put option -> 池子收 token0, 付token1
        # input m,n = (0, 2000): call  , exercise call option -> 池子收 token1, 付token0

        return (
            nid,
            m,
            n,
            strike,
            dx,
            dy,
            exercise_call,
            exercise_put,
            prem_ratio,
            due,
            duration_sec,
            q_old,
            q_new,
        )

    def exercise_option(self, nid: int, option_type: str) -> tuple:
        """Revert exercise option for reverse deposit based on specified option type"""

        nr = self.notes_reverse.get(nid)
        if not nr:
            raise KeyError(f"反向存款 note_id={nid} 不存在")

        if option_type == "call":
            # 使用者交回 token1，池子支付 token0
            swap_in, swap_out = nr.exercise_call  
            if self.x < swap_out:
                raise ValueError(f"行使 call 失敗：池中 token0 不足 (需 {swap_out}, 現有 {self.x})")
            self.y += swap_in  # Pool receive token1
            self.x -= swap_out  # Pool pay token0
            out0, out1 = swap_out, swap_in

        elif option_type == "put":
            # 使用者交回 token0，池子支付 token1
            swap_in, swap_out = nr.exercise_put
            if self.y < swap_out:
                raise ValueError(f"行使 put 失敗：池中 token1 不足 (需 {swap_out}, 現有 {self.y})")
            self.x += swap_in  # Pool receive token0
            self.y -= swap_out  # Pool pay token1
            out0, out1 = swap_in, swap_out

        else:
            raise ValueError("exercise_option: option_type 必須是 'put' 或 'call'")

        # 回補 Q（reverse_deposit 時 Q 為負，所以此處減去負值即回補）
        self.q_by_due[nr.due] -= nr.Q
        self.k_last = self._current_k()
        del self.notes_reverse[nid]

        return (out0, out1)

    def snapshot(self, day: float, price: float) -> dict:
        return {
            "day": day,
            "price": price,
            "reserve_eth": self.x,
            "reserve_usdc": self.y,
            "pool_eth_price": self.y / self.x,
            "k": self.k_last,
        }
