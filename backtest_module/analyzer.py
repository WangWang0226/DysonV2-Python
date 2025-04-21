# analyzer.py
import os, matplotlib.pyplot as plt, seaborn as sns, pandas as pd

sns.set_style("whitegrid")
plt.rcParams["figure.figsize"] = (10, 4)


class Analyzer:
    def __init__(
        self, dep: pd.DataFrame, wd: pd.DataFrame, snap: pd.DataFrame, tag: str
    ):
        """
        dep  : deposits  DataFrame
        wd   : withdraws DataFrame
        snap : daily snapshot DataFrame  (必含 day, price, k)
        tag  : 輸出資料夾 / 圖表標題用字
        """
        self.dep, self.wd, self.snap, self.tag = dep, wd, snap, tag

        # enrich deposits
        self.dep = self.dep.merge(self.snap[["day", "price"]], on="day")
        self.dep["invest_usd"] = self.dep.in0 * self.dep.price + self.dep.in1
        self.dep["premium_usd"] = self.dep.invest_usd * self.dep.premium_ratio
        self.dep["apr"] = (
            self.dep.premium_usd / self.dep.invest_usd * 365 / self.dep.lock
        )
        self.dep["side"] = ((self.dep.in0 == 0) | (self.dep.in1 == 0)).map(
            {True: "Single", False: "Dual"}
        )

        # 快速索引用
        self._snap_idx = self.snap.set_index("day")

    # ---------- util ----------
    @staticmethod
    def _save_show(path: str):
        plt.tight_layout()
        plt.savefig(path, dpi=110)
        plt.show()
        plt.close()

    def _export_csv(self, odir: str):
        """把三個 DataFrame 存成 csv"""
        self.dep.to_csv(f"{odir}/deposits.csv", index=False)
        self.wd.to_csv(f"{odir}/withdraws.csv", index=False)
        self.snap.to_csv(f"{odir}/snapshots.csv", index=False)

    # ---------- main ----------
    def all_plots(self, out: str = "results"):
        odir = f"{out}/{self.tag}"
        os.makedirs(odir, exist_ok=True)
        self._export_csv(odir)

        # 1) 投資人數
        self.dep.groupby("day").size().plot.bar(title=f"{self.tag}: #Investors / day")
        self._save_show(f"{odir}/users.png")

        # 2) 每日投入 & premium
        g = self.dep.groupby("day")
        g.invest_usd.sum().plot(label="Invest $")
        g.premium_usd.sum().plot(label="Premium $")
        plt.legend()
        plt.title(f"{self.tag}: Invest vs Premium")
        self._save_show(f"{odir}/amt_prem.png")

        # 3) premium / k
        ratio = g.premium_usd.sum() / self._snap_idx.k
        ratio.plot()
        plt.title(f"{self.tag}: Premium / k")
        self._save_show(f"{odir}/prem_k.png")

        # 4) k 走勢與日變動
        self._snap_idx.k.plot(title=f"{self.tag}: k value")
        self._save_show(f"{odir}/k_curve.png")

        (self._snap_idx.k.pct_change() * 100).iloc[1:].plot.bar(
            title=f"{self.tag}: Δk % per day"
        )
        self._save_show(f"{odir}/k_change.png")

        # 5) ETH 價格走勢與日變動
        self._snap_idx.price.plot(title=f"{self.tag}: ETH price (USD)")
        self._save_show(f"{odir}/price_curve.png")

        (self._snap_idx.price.pct_change() * 100).iloc[1:].plot.bar(
            title=f"{self.tag}: Δprice % per day"
        )
        self._save_show(f"{odir}/price_change.png")

        # 6) APR 分布
        sns.histplot(self.dep.apr, bins=30, kde=True)
        plt.title(f"{self.tag}: APR")
        self._save_show(f"{odir}/apr.png")

        # 7) Lock days 分布
        sns.histplot(self.dep.lock, bins=30)
        plt.title(f"{self.tag}: Lock Days")
        self._save_show(f"{odir}/lock.png")

        # 8) 單 / 雙邊 存款
        self.dep.side.value_counts().plot.pie(autopct="%.1f%%", ylabel="")
        plt.title(f"{self.tag}: Single vs Dual")
        self._save_show(f"{odir}/side.png")

        # 9) 用戶 PnL
        if not self.wd.empty:
            sns.histplot(self.wd.pnl_usd, bins=30, kde=True)
            plt.title(f"{self.tag}: User PnL (USD)")
            self._save_show(f"{odir}/pnl.png")
