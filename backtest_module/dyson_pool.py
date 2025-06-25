import itertools
import math
from dataclasses import dataclass
from collections import defaultdict
from enum import Enum
from time_utils import compute_due_time_and_duration

@dataclass
class Note_forward:
    id: int
    note0_with_premium: float  # Note token0 amount
    note1_with_premium: float  # Note token1 amount
    due: float

@dataclass
class Note_reverse:
    id: int
    m: float  # Target token0 amount
    n: float  # Target token1 amount
    strike: float  # Strike price
    due: float


class DepositType(Enum):
    FORWARD = "forward"
    REVERSE = "reverse"


class DysonPool:
    def __init__(
        self, init_eth: float, init_usdc: float, basis: float, w_factor: float
    ):
        self.x = init_eth  # ETH reserve
        self.y = init_usdc  # USDC reserve
        self.k_last = math.sqrt(self.x * self.y)
        self.w = self.k_last * w_factor
        self.basis = basis
        self.q_by_due = defaultdict(float)
        self.notes_forward = {}
        self.notes_reverse = {}
        self._seq = itertools.count()

    def rebalance(self, price: float) -> tuple[float, float]:
        """Rebalance pool to 50/50 asset ratio based on current price, and update internal state."""
        eth_val = self.x * price
        total_val = eth_val + self.y
        target = total_val / 2

        if eth_val > target:
            diff = (eth_val - target) / price
            self.x -= diff
            self.y += eth_val - target
        elif self.y > target:
            diff = self.y - target
            self.x += diff / price
            self.y -= diff
        # 若已經是 50/50 就不變
        return self.x, self.y  
    
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

    def _calculate_forward_notes(
        self, in0: float, in1: float, Q_sq: float
    ) -> tuple[float, float]:
        """Calculate note0 and note1 based on pool reserves and input"""
        if in0 * self.y > in1 * self.x:
            ratio = (in1 * self.x) / self.y if self.y else 0
            note0 = in0 + ratio
            note1 = Q_sq / note0
        else:
            ratio = (in0 * self.y) / self.x if self.x else 0
            note1 = in1 + ratio
            note0 = Q_sq / note1
        return note0, note1

    def _calculate_reverse_deposit(
        self, m: float, n: float, premium: float
    ) -> tuple:
        """Calculate strike, delta_x, delta_y, and revert options for reverse deposit"""
        strike = (self.y + n) / (self.x + m)
        delta_x = m * (1 + premium) - n / strike
        delta_y = n * (1 + premium) - m * strike
        # Revert options
        return strike, delta_x, delta_y

    def deposit(self, in0: float, in1: float, lock_days: int, price: float) -> tuple:
        """Handle forward dual deposit"""
        assert in0 > 0 or in1 > 0, "At least one input must be positive"
        due, duration_sec = compute_due_time_and_duration(lock_days)

        k_before = math.sqrt(self.x * self.y)
        k_after = math.sqrt((self.x + in0) * (self.y + in1))
        diff = k_after - k_before
        Q_sq = 4 * diff * diff
        q_add = math.sqrt(Q_sq) / 2

        note0, note1 = self._calculate_forward_notes(in0, in1, Q_sq)
        q_old = self.q_by_due[due]
        q_new = q_old + q_add
        a, b = q_old / self.w, q_new / self.w

        discount = self._calculate_discount(a, b, DepositType.FORWARD)
        prem_ratio = (
            0.4 * self.basis * math.sqrt(duration_sec / (365 * 86400)) * discount
        )
        note0_with_prem = note0 * (1 + prem_ratio)
        note1_with_prem = note1 * (1 + prem_ratio)
        self.q_by_due[due] = q_new

        nid = next(self._seq)
        self.notes_forward[nid] = Note_forward(nid, note0_with_prem, note1_with_prem, due)

        self.x += in0
        self.y += in1
        self.k_last = k_after
        return nid, note0, note1, note0_with_prem, note1_with_prem, prem_ratio, due, duration_sec, q_old, q_new

    def withdraw_due(self, utc_date, price: float, nid: int) -> list:
        today = utc_date.timestamp() / 86400
        n = self.notes_forward[nid]
        amount0 = n.note0_with_premium
        amount1 = n.note1_with_premium
        if n.due <= today:
            num = self.x * amount1 + amount0 * amount1 - self.y * amount0
            ratio = max(
                0,
                min(
                    (num / (2 * amount0 * amount1) if amount0 and amount1 else 0),
                    1,
                ),
            )
            withdraw0, withdraw1 = ratio * amount0, (1 - ratio) * amount1
            self.x -= withdraw0
            self.y -= withdraw1
            self.k_last = math.sqrt(self.x * self.y)
            del self.notes_forward[nid]
            return (n, withdraw0, withdraw1)

    def reverse_deposit(self, m: float, n: float, lock_days: int, price: float) -> tuple:
        """Handle reverse dual deposit with immediate exercise"""
        assert m >= 0 and n >= 0, "m and n must be non-negative"
        due, duration_sec = compute_due_time_and_duration(lock_days)

        k_before = math.sqrt(self.x * self.y)
        k_after = math.sqrt((self.x + m) * (self.y + n))
        diff = k_after - k_before
        Q_sq = 4 * diff * diff
        q_add = math.sqrt(Q_sq) / 2

        q_old = self.q_by_due[due]
        q_new = q_old - q_add
        if q_new < 0:
            raise ValueError("Insufficient liquidity for reverse deposit")
        a, b = q_old / self.w, q_new / self.w

        discount = self._calculate_discount(a, b, DepositType.REVERSE)
        prem_ratio = (
            0.4 * self.basis * math.sqrt(duration_sec / (365 * 86400)) * discount
        )

        strike, delta_x, delta_y = (
            self._calculate_reverse_deposit(m, n, prem_ratio)
        )

        nid = next(self._seq)
        # Update reserves
        self.x += delta_x
        self.y += delta_y
        self.k_last = math.sqrt(self.x * self.y)

        # Store note with revert options
        self.notes_reverse[nid] = Note_reverse(
            nid, m, n, strike, due
        )

        return (
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
        )

    def exercise_option(self, nid: int, option_type: str) -> tuple:
        """Revert exercise option for reverse deposit based on specified option type"""
        note = self.notes_reverse.get(nid)
        if not note or not isinstance(note, Note_reverse):
            raise ValueError("Invalid note or not a reverse deposit")

        if option_type not in ["put", "call"]:
            raise ValueError("Invalid option type, use 'put' or 'call'")

        strike = note.strike
        m = note.m  
        n = note.n  
        if option_type == "call":
            swap_in, swap_out = (m * strike, m)  # (swap in token1, swap out token0)
            if self.x < swap_out:
                raise ValueError("Insufficient pool reserves for put revert")
            self.y += swap_in  # Pool receive token1
            self.x -= swap_out  # Pool pay token0

        elif option_type == "put":
            swap_in, swap_out = (n / strike, n) # (swap in token0, swap out token1)
            if self.y < swap_out:
                raise ValueError("Insufficient pool reserves for call revert")
            self.x += swap_in  # Pool receive token0
            self.y -= swap_out  # Pool pay token1
        self.k_last = math.sqrt(self.x * self.y)
        del self.notes_reverse[nid]

        return (
            option_type,
            swap_in,
            swap_out,
        )

    def snapshot(self, day: float, price: float) -> dict:
        return {
            "day": day,
            "price": price,
            "reserve_eth": self.x,
            "reserve_usdc": self.y,
            "pool_eth_price": self.y / self.x,
            "k": self.k_last,
        }
