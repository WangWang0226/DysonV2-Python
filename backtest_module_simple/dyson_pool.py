import itertools
import math
from dataclasses import dataclass

@dataclass
class Note_forward:
    id: int
    in0: float  # Input token0 amount
    in1: float  # Input token1 amount
    note0: float  # Note token0 amount
    note1: float  # Note token1 amount
    price_in: float  # Price at deposit


@dataclass
class Note_reverse:
    id: int
    m: float  # Target token0 amount
    n: float  # Target token1 amount
    delta_x: float  # If positive, means user swap in token0 amount; if negative, means user receive token0 amount
    delta_y: float  # If positive, means user swap in token1 amount; if negative, means user receive token1 amount
    strike: float  # Strike price
    price_in: float  # Price at deposit
    revert_put: tuple[float, float] = (0, 0)  # (put swap in, put swap out)
    revert_call: tuple[float, float] = (0, 0)  # (call swap in, call swap out)


class DysonPool:
    def __init__(
        self, init_eth: float, init_usdc: float, basis: float, w_factor: float
    ):
        self.x = init_eth  # ETH reserve
        self.y = init_usdc  # USDC reserve
        self.k_last = math.sqrt(self.x * self.y)
        self.w = self.k_last * w_factor
        self.basis = basis
        self.notes_forward = {}
        self.notes_reverse = {}
        self._seq = itertools.count()

    def rebalance(self, price: float) -> tuple[float, float]:
        """Rebalance pool to 50/50 asset ratio based on current price"""
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
        self, m: float, n: float
    ) -> tuple[float, float, float, tuple[float, float], tuple[float, float]]:
        """Calculate strike, delta_x, delta_y, and revert options for reverse deposit"""
        strike = (self.y + n) / (self.x + m) 
        delta_x = m - n / strike 
        delta_y = n - m * strike
        # Revert options
        revert_put = (m * strike, m)  # (swap in token1, swap out token0)
        revert_call = (n / strike, n)  # (swap in token0, swap out token1)
        return strike, delta_x, delta_y, revert_put, revert_call

    def deposit(self, in0: float, in1: float, price: float) -> tuple:
        """Handle forward dual deposit"""
        assert in0 > 0 or in1 > 0, "At least one input must be positive"
        nid = next(self._seq)
        k_before = math.sqrt(self.x * self.y)
        k_after = math.sqrt((self.x + in0) * (self.y + in1))
        diff = k_after - k_before
        Q_sq = 4 * diff * diff
        note0, note1 = self._calculate_forward_notes(in0, in1, Q_sq)
        self.notes_forward[nid] = Note_forward(nid, in0, in1, note0, note1, price)
        self.x += in0
        self.y += in1
        self.k_last = k_after
        self.x, self.y = self.rebalance(price)
        return nid

    def withdraw_due(self, price: float, nid: int) -> list:
        n = self.notes_forward[nid]
        num = (
            self.x * n.note1 + n.note0 * n.note1 - self.y * n.note0
        )
        ratio = max(
            0,
            min(
                (
                    num / (2 * n.note0 * n.note1)
                    if n.note0 and n.note1
                    else 0
                ),
                1,
            ),
        )
        amt0, amt1 = ratio * n.note0, (1 - ratio) * n.note1
        self.x -= amt0
        self.y -= amt1
        self.k_last = math.sqrt(self.x * self.y)
        self.x, self.y = self.rebalance(price)
        del self.notes_forward[nid]
        return (n, amt0, amt1)

    def reverse_deposit(self, m: float, n: float, price: float) -> tuple:
        """Handle reverse dual deposit with immediate exercise"""
        assert m >= 0 and n >= 0, "m and n must be non-negative"
        nid = next(self._seq)
        strike, delta_x, delta_y, revert_put, revert_call = (
            self._calculate_reverse_deposit(m, n)
        )

        # Update reserves
        self.x += delta_x
        self.y += delta_y
        self.k_last = math.sqrt(self.x * self.y)
        self.x, self.y = self.rebalance(price)

        # Store note with revert options
        self.notes_reverse[nid] = Note_reverse(
            nid, m, n, delta_x, delta_y, strike, price, revert_put, revert_call
        )
        return nid

    def revert_exercise(self, nid: int, option_type: str) -> tuple:
        """Revert exercise option for reverse deposit based on specified option type"""
        note = self.notes_reverse.get(nid)
        if not note or not isinstance(note, Note_reverse):
            raise ValueError("Invalid note or not a reverse deposit")

        if option_type not in ["put", "call"]:
            raise ValueError("Invalid option type, use 'put' or 'call'")

        if option_type == "put":
            swap_in, swap_out = note.revert_put
            if self.x < swap_out:
                raise ValueError("Insufficient pool reserves for put revert")
            self.y += swap_in  # Pool receive token1
            self.x -= swap_out  # Pool pay token0

        elif option_type == "call":
            swap_in, swap_out = note.revert_call
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
        )  # is_swap_in_token1, swap_in, swap_out

    def snapshot(self, day: float, price: float) -> dict:
        return {
            "day": day,
            "price": price,
            "reserve_eth": self.x,
            "reserve_usdc": self.y,
            "k": self.k_last,
        }
