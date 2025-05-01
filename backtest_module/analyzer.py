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

        deposits_grouped = self.dep.groupby("day")

        # 1) premium
        deposits_grouped.premium_ratio.sum().plot()
        plt.title(f"{self.tag}: Premium ratio")
        self._save_show(f"{odir}/premium.png")

        # 2) k 走勢與日變動
        self._snap_idx.k.plot(title=f"{self.tag}: k value")
        self._save_show(f"{odir}/k_curve.png")

        # 3) ETH 價格走勢與日變動
        self._snap_idx.price.plot(title=f"{self.tag}: ETH price (USD)")
        self._save_show(f"{odir}/price_curve.png")

        # 4) 單 / 雙邊 存款
        self.dep.side.value_counts().plot.pie(autopct="%.1f%%", ylabel="")
        plt.title(f"{self.tag}: Single vs Dual")
        self._save_show(f"{odir}/side.png")

        # 5) 用戶 PnL
        if not self.wd.empty:
            sns.histplot(self.wd.pnl_usd, bins=30, kde=True)
            plt.title(f"{self.tag}: User PnL (USD)")
            self._save_show(f"{odir}/pnl_usd.png")

            sns.histplot(self.wd.pnl_ratio, bins=30, kde=True)
            plt.title(f"{self.tag}: User PnL %")
            self._save_show(f"{odir}/pnl_ratio.png")
