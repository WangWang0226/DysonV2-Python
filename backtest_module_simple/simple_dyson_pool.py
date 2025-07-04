import math
import itertools
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class Note_forward:
    id: int
    in0: float  # 存入的 token0 數量
    in1: float  # 存入的 token1 數量
    note0: float  # 產生的 token0 票據數量
    note1: float  # 產生的 token1 票據數量
    Q: float  # 對應的 Q 值


@dataclass
class Note_reverse:
    id: int
    m: float  # 使用者想要取出的 token0 數量
    n: float  # 使用者想要取出的 token1 數量
    strike: float  # 行權價 = (y - n) / (x - m)
    delta_x: float  # 實際池子 token0 變動量
    delta_y: float  # 實際池子 token1 變動量
    revert_put: Tuple[float, float]  # 行使 put 時池子 (接收 token1, 支付 token0)
    revert_call: Tuple[float, float]  # 行使 call 時池子 (接收 token0, 支付 token1)
    Q: float  # 對應的 Q 值 (負值)


class SimpleDysonPool:
    def __init__(self, init_x: float, init_y: float):
        if init_x <= 0 or init_y <= 0:
            raise ValueError("初始儲備 x, y 必須為正數")
        self.x = init_x
        self.y = init_y
        self.q = 0.0
        self.k_last = math.sqrt(self.x * self.y)

        self.notes_forward: Dict[int, Note_forward] = {}
        self.notes_reverse: Dict[int, Note_reverse] = {}
        self._seq = itertools.count()

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
          Q  = 2*(k1 - k0)
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

    def deposit(
        self, in0: float, in1: float
    ) -> Tuple[int, float, float, float, float, float, float]:
        """
        正向存款，返回 (note_id, note0, note1, Q, x, y, q)
        """
        if in0 < 0 or in1 < 0 or (in0 == 0 and in1 == 0):
            raise ValueError("deposit 時 in0, in1 至少要有一個 > 0")

        Q, new_x, new_y, k1 = self._compute_Q_and_k1(in0, in1)
        note_product = 4 * Q * Q

        # 計算票據數量
        if in0 * self.y > in1 * self.x:
            ratio = in1 * self.x / self.y
            note0 = in0 + ratio
            note1 = note_product / note0
        else:
            ratio = in0 * self.y / self.x
            note1 = in1 + ratio
            note0 = note_product / note1

        # 更新儲備 (不做 rebalance)
        self.x, self.y = new_x, new_y
        self.q += Q
        self.k_last = k1

        nid = self._new_id()
        self.notes_forward[nid] = Note_forward(nid, in0, in1, note0, note1, Q)
        return nid, note0, note1, Q, self.x, self.y, self.q

    def withdraw(self, nid: int) -> Tuple[float, float]:
        """
        提領正向存款，返回 (amt0, amt1)
        """
        nf = self.notes_forward.get(nid)
        if not nf:
            raise KeyError(f"正向存款 note_id={nid} 不存在")

        num = self.x * nf.note1 + nf.note0 * nf.note1 - self.y * nf.note0
        denom = 2 * nf.note0 * nf.note1
        ratio = (num / denom) if denom > 0 else 0.0
        ratio = max(0.0, min(ratio, 1.0))
        amt0 = ratio * nf.note0
        amt1 = (1 - ratio) * nf.note1

        self.x -= amt0
        self.y -= amt1
        self.q -= nf.Q
        self.k_last = self._current_k()

        del self.notes_forward[nid]
        return amt0, amt1

    def reverse_deposit(
        self, m: float, n: float
    ) -> Tuple[int, float, float, float, float, float, float, float]:
        """
        反向存款（池子賣出流動性），返回
        (note_id, strike, delta_x, delta_y, Q, x, y, q)
        """
        if m < 0 or n < 0 or m >= self.x or n >= self.y:
            raise ValueError("reverse_deposit 時 m, n 必須 ≥ 0 且 m < x, n < y")

        # 1) 根據移除後儲備計算行權價
        strike = (self.y - n) / (self.x - m)

        # 2) 虛擬移除 (-m, -n) 計算負的 Q
        Q, _, _, _ = self._compute_Q_and_k1(-m, -n)

        # 3) 真實移動量：池子實際收/付資產
        dx = n / strike - m  # 池子 token0 收入 = n/strike，付出 = m
        dy = m * strike - n  # 池子 token1 收入 = m*strike，付出 = n

        if self.q + Q < 0:
            raise ValueError("reverse_deposit 會使 q < 0")

        # 4) 更新儲備與 q (不做 rebalance)
        self.q += Q
        self.x += dx
        self.y += dy
        self.k_last = self._current_k()

        nid = self._new_id()
        revert_call = (n, n / strike)  # 行使 call 時，池子收 token1=n, 付 token0=n/strike
        revert_put = (m, m * strike)  # 行使 put 時，池子收 token0=m, 付 token1=m*strike
        self.notes_reverse[nid] = Note_reverse(
            nid, m, n, strike, dx, dy, revert_put, revert_call, Q
        )        
        # input m,n = (1, 0): call  , exercise call option -> 池子收 token1, 付token0
        # input m,n = (0, 2000): call  , exercise call option -> 池子收 token1, 付token0

        return nid, strike, dx, dy, Q, self.x, self.y, self.q

    def exercise_option(self, nid: int, option_type: str) -> Tuple[float, float]:
        """
        行使反向存款（期權），返回池子支付給使用者的 (out0, out1)
        """
        nr = self.notes_reverse.get(nid)
        if not nr:
            raise KeyError(f"反向存款 note_id={nid} 不存在")

        if option_type == "call":
            # 使用者交回 token1，池子支付 token0
            swap_in, swap_out = nr.revert_call
            if self.x < swap_out:
                raise ValueError(f"行使 call 失敗：池中 token0 不足 (需 {swap_out}, 現有 {self.x})")
            self.y += swap_in
            self.x -= swap_out
            out0, out1 = swap_out, swap_in

        elif option_type == "put":
            # 使用者交回 token0，池子支付 token1
            swap_in, swap_out = nr.revert_put
            if self.y < swap_out:
                raise ValueError(f"行使 put 失敗：池中 token1 不足 (需 {swap_out}, 現有 {self.y})")
            self.x += swap_in
            self.y -= swap_out
            out0, out1 = swap_in, swap_out

        else:
            raise ValueError("exercise_option: option_type 必須是 'put' 或 'call'")

        # 回補 Q（reverse_deposit 時 Q 為負，所以此處減去負值即回補）
        self.q -= nr.Q
        self.k_last = self._current_k()

        del self.notes_reverse[nid]
        return out0, out1

    def expire(self, nid: int) -> None:
        """
        模擬反向存款過期：不做任何資產變動，
        直接把之前扣除的 Q 加回並刪除 note
        """
        nr = self.notes_reverse.get(nid)
        if not nr:
            raise KeyError(f"反向存款 note_id={nid} 不存在")

        # 回補 Q
        self.q -= nr.Q
        # 資產 x,y 不變，重新計算 k_last
        self.k_last = self._current_k()

        del self.notes_reverse[nid]

    def snapshot(self) -> Dict[str, float]:
        """回傳當前池子狀態快照"""
        return {
            "x": self.x,
            "y": self.y,
            "q": self.q,
            "k_last": self.k_last,
            "num_forward": len(self.notes_forward),
            "num_reverse": len(self.notes_reverse),
        }
