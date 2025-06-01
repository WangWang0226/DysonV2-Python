import itertools, math
from collections import defaultdict
from time_utils import compute_due_time_and_duration, tm
from dataclasses import dataclass
from enum import Enum


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
    option_type: str = None  # "call" or "put" for reverse deposits


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
        self.notes = {}
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

    def _calculate_discount(
        self, a: float, b: float, deposit_type: DepositType
    ) -> float:
        """Calculate discount factor based on deposit type"""
        if deposit_type == DepositType.FORWARD:
            return (
                (math.log2(b + 1) - math.log2(a + 1)) * math.log(2) / (b - a or 1e-10)
            )
        elif deposit_type == DepositType.REVERSE:
            return (
                (math.log2(b + 0.5) - math.log2(a + 0.5))
                * math.log(2)
                / (b - a or 1e-10)
            )

    def _calculate_forward_notes(self, in0: float, in1: float, Q_sq: float) -> tuple[float, float]:
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

    def _calculate_reverse_notes(self, in0: float, in1: float) -> tuple[float, float]:
        """in0 and in1 must be one of them is zero for reverse deposit"""
        if in0 == 0:
            note1 = in1
            note0 = self.virtual_swap(in1, is_input_0=False)
        elif in1 == 0:
            note0 = in0
            note1 = self.virtual_swap(in0, is_input_0=True)
        else:
            raise ValueError("Reverse deposit must have only one input (in0 or in1)")
        return note0, note1

    def _process_deposit(
        self,
        in0: float,
        in1: float,
        lock_days: int,
        today: float,
        price: float,
        deposit_type: DepositType,
        option_type: str = None,
    ) -> tuple:
        """Common deposit logic for both forward and reverse deposits"""
        assert in0 > 0 or in1 > 0
        due, duration_sec = compute_due_time_and_duration(lock_days)

        k_before = math.sqrt(self.x * self.y)
        k_after = math.sqrt((self.x + in0) * (self.y + in1))
        diff = k_after - k_before
        Q_sq = 4 * diff * diff
        Q = math.sqrt(Q_sq)

        # Delegate note calculation based on deposit type
        if deposit_type == DepositType.FORWARD:
            note0, note1 = self._calculate_forward_notes(in0, in1, Q_sq)
        elif deposit_type == DepositType.REVERSE:
            note0, note1 = self._calculate_reverse_notes(in0, in1)
        else:
            raise ValueError("Invalid deposit type")

        # Adjust q_by_due based on deposit type
        q_adjustment = 1.0 if deposit_type == DepositType.FORWARD else -1.0
        q_old, q_new = self.q_by_due[due], self.q_by_due[due] + q_adjustment * Q
        a, b = q_old / self.w, q_new / self.w

        discount = self._calculate_discount(a, b, deposit_type)
        prem_ratio = (
            0.4 * self.basis * math.sqrt(duration_sec / (365 * 86400)) * discount
        )
        note0_with_prem = note0 * (1 + prem_ratio)
        note1_with_prem = note1 * (1 + prem_ratio)
        self.q_by_due[due] = q_new

        nid = next(self._seq)
        self.notes[nid] = Note(
            nid, note0_with_prem, note1_with_prem, due, today, in0, in1, price, option_type
        )
        return nid, note0, note1, note0_with_prem, note1_with_prem, prem_ratio, due, duration_sec, q_old, q_new, k_after

    def deposit(
        self, in0: float, in1: float, lock_days: int, today: float, price: float
    ) -> tuple:
        nid, note0, note1, note0_with_prem, note1_with_prem, prem_ratio, due, duration_sec, q_old, q_new, k_after = (
            self._process_deposit(
                in0, in1, lock_days, today, price, deposit_type=DepositType.FORWARD
            )
        )

        self.x += in0
        self.y += in1
        self.k_last = k_after
        self.x, self.y = self.rebalance(price)

        return nid, note0, note1, note0_with_prem, note1_with_prem, prem_ratio, due, duration_sec, q_old, q_new

    def withdraw_due(self, utc_date, price: float) -> list:
        today = utc_date.timestamp() / 86400
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
                amt0, amt1 = ratio * n.token0Amt, (1 - ratio) * n.token1Amt
                self.x -= amt0
                self.y -= amt1
                self.k_last = math.sqrt(self.x * self.y)
                self.x, self.y = self.rebalance(price)
                paid.append((n, amt0, amt1))
                del self.notes[nid]
        return paid

    def reverse_deposit(
        self,
        in0: float,
        in1: float,
        lock_days: int,
        today: float,
        option_type: str,
        price: float,
    ) -> tuple:
        assert option_type in ["call", "put"]
        
        if option_type == "put":
            assert in1 == 0, "For put options, in1 must be zero"
        elif option_type == "call":
            assert in0 == 0, "For call options, in0 must be zero"
            
        nid, note0, note1, note0_with_prem, note1_with_prem, prem_ratio, due, duration_sec, q_old, q_new, k_after = (
            self._process_deposit(
                in0,
                in1,
                lock_days,
                today,
                price,
                deposit_type=DepositType.REVERSE,
                option_type=option_type,
            )
        )

        # The swap_amount
        if option_type == "put":
            swap_amount = note1
            if self.y < swap_amount:
                raise ValueError("Insufficient pool reserves")
            self.x += note0_with_prem
            self.y -= swap_amount
        elif option_type == "call":
            swap_amount = note0
            if self.x < swap_amount:
                raise ValueError("Insufficient pool reserves")
            self.x -= swap_amount
            self.y += note1_with_prem

        self.k_last = math.sqrt(self.x * self.y)
        return nid, note0, note1, note0_with_prem, note1_with_prem, prem_ratio, due, duration_sec, q_old, q_new

    def exercise_option(self, nid: int, utc_date, price: float):
        today = utc_date.timestamp() / 86400
        n = self.notes.get(nid)
        if not n or n.option_type not in ["call", "put"] or n.due <= today:
            raise ValueError("Invalid note, not a reverse option, or already expired")

        if n.option_type == "put":
            if self.y < n.token1Amt or self.x < n.token0Amt:
                raise ValueError("Insufficient pool reserves")
            self.y += n.token1Amt
            self.x -= n.token0Amt
        elif n.option_type == "call":
            if self.x < n.token0Amt or self.y < n.token1Amt:
                raise ValueError("Insufficient pool reserves")
            self.x += n.token0Amt
            self.y -= n.token1Amt

        self.k_last = math.sqrt(self.x * self.y)
        del self.notes[nid]

    def snapshot(self, day: float, price: float) -> dict:
        return {
            "day": day,
            "price": price,
            "reserve_eth": self.x,
            "reserve_usdc": self.y,
            "k": self.k_last,
        }

    def virtual_swap(self, input_amount: float, is_input_0: bool = True) -> float:
        """Calculate virtual swap output based on constant product formula (xy = k)"""
        assert self.x > 0 and self.y > 0, "Pool reserves must be positive"
        assert input_amount > 0, "Input amount must be positive"

        if is_input_0:
            numerator = self.y * input_amount
            denominator = self.x + input_amount
            return numerator / denominator
        else:
            numerator = self.x * input_amount
            denominator = self.y + input_amount
            return numerator / denominator
