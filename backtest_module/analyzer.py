# analyzer.py
import os, matplotlib.pyplot as plt, seaborn as sns, pandas as pd

sns.set_style("whitegrid")
plt.rcParams["figure.figsize"] = (10, 4)


class Analyzer:
    def __init__(
        self,
        dep: pd.DataFrame,
        wd: pd.DataFrame,
        reverse_dep: pd.DataFrame,
        snap: pd.DataFrame,
        tag: str,
    ):
        """
        dep  : deposits  DataFrame
        wd   : withdraws DataFrame
        reverse_dep : reverse deposits DataFrame
        snap : daily snapshot DataFrame  (必含 day, price, k)
        tag  : scenario tag for output plots and CSV files
        """
        self.dep, self.wd, self.reverse_dep, self.snap, self.tag = dep, wd, reverse_dep, snap, tag

        # enrich deposits
        self.dep = self.dep.merge(self.snap[["day", "price"]], on="day")
        self.dep["invest_usd"] = self.dep.in0 * self.dep.price + self.dep.in1
        self.dep["side"] = ((self.dep.in0 == 0) | (self.dep.in1 == 0)).map(
            {True: "Single", False: "Dual"}
        )

        # Create reverse_user_pnl_df
        self.reverse_user_pnl_df = self._create_reverse_user_pnl_df()

        # 快速索引用
        self._snap_idx = self.snap.set_index("day")

    def _create_reverse_user_pnl_df(self):
        """Create reverse_user_pnl_df by merging reverse_dep and option_revert"""
        if self.reverse_dep.empty:
            return pd.DataFrame(columns=[])

        # Calculate premium and PnL
        self.reverse_dep["premium_usd"] = self.reverse_dep.apply(
            lambda row: (
                (row["m"] * row["premium_ratio"] * row["price_in"]) + 
                (row["n"] * row["premium_ratio"])
            ),
            axis=1,
        )

        # 若是雙邊，則必為一側行權，一側撤銷。若單邊，可能行權或撤銷
        # 不管雙邊還單邊，
        # pnl_usd ＝
        #   若是有行權： -> 行權的那一側賺的 - 付出的總premium
        #   若是撤銷行權: -> 損失付出的總 premium

        # There are 3 types of exercise result: EXERCISE_CALL, EXERCISE_PUT, NOT_EXERCISED
        def calculate_pnl(row):
            price_in = row["price_in"]
            price_out = row["price_when_exercise"]
            if row["exercise_result"] == "NOT_EXERCISED":
                return -row["premium_usd"]

            elif row["exercise_result"] == "EXERCISE_CALL":
                assert price_out > price_in, "CALL option requires price_out > price_in"
                return (
                    row["m"] * (price_out - price_in) - row["premium_usd"]
                )

            elif row["exercise_result"] == "EXERCISE_PUT":
                assert price_out < price_in, "PUT option requires price_out < price_in"
                unit = row["n"] / price_in
                return (
                    unit * (price_in - price_out) - row["premium_usd"]
                )

        self.reverse_dep["pnl_usd"] = self.reverse_dep.apply(calculate_pnl, axis=1)

        # pnl ratio = 淨賺 / 投入成本
        # 投入成本 = delta_x * eth期初價 + delta_y

        self.reverse_dep["pnl_ratio"] = self.reverse_dep.apply(
            lambda row: (row["pnl_usd"] / (row["delta_x_y"][0] * row["price_in"] + row["delta_x_y"][1])),
            axis=1,
        )

        self.reverse_dep["single_or_double"] = self.reverse_dep.apply(
            lambda row: "DOUBLE SIDE" if (row["m"] > 0 and row["n"] > 0) else "SINGLE SIDE",
            axis=1,
        )

        # Select required columns
        return self.reverse_dep[
            [
                "note_id",
                "day_deposit",
                "day_exercise",
                "lock",
                "price_in",
                "price_when_exercise",
                "m",
                "n",
                "single_or_double",
                "premium_usd",
                "delta_x_y",
                "pnl_usd",
                "pnl_ratio",
                "exercise_result",
            ]
        ]

    # ---------- util ----------
    @staticmethod
    def _save_show(path: str):
        plt.tight_layout()
        plt.savefig(path, dpi=110)
        plt.close()

    def _export_csv(self, out_dir: str, forward_dual_invest_dir: str, reverse_dual_invest_dir: str):
        """把所有 DataFrame 存成 csv"""
        self.dep.to_csv(f"{forward_dual_invest_dir}/deposits.csv", index=False)
        self.wd.to_csv(f"{forward_dual_invest_dir}/withdraws.csv", index=False)
        self.snap.to_csv(f"{out_dir}/snapshots.csv", index=False)
        self.reverse_dep.to_csv(f"{reverse_dual_invest_dir}/reverse_deposits.csv", index=False)
        self.reverse_user_pnl_df.to_csv(f"{reverse_dual_invest_dir}/reverse_user_pnl.csv", index=False)

    # ---------- main ----------
    def all_plots(self, out: str = "results"):
        out_dir = f"{out}/{self.tag}"
        forward_dual_invest_dir = f"{out_dir}/forward_dual_invest"
        reverse_dual_invest_dir = f"{out_dir}/reverse_dual_invest"
        os.makedirs(forward_dual_invest_dir, exist_ok=True)
        os.makedirs(reverse_dual_invest_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)
        self._export_csv(out_dir, forward_dual_invest_dir, reverse_dual_invest_dir)

        ### ----- General Info ----- ###
        # 池子 k 走勢與日變動
        self._snap_idx.k.plot(title=f"{self.tag}: k value")
        self._save_show(f"{out_dir}/k_curve.png")

        # ETH 價格走勢與日變動
        fig, ax = plt.subplots()

        # 市場價格（ETH 現貨價格）
        self._snap_idx["price"].plot(ax=ax, label="Market ETH Price", linewidth=2)
        # 池內 ETH 價格
        self._snap_idx["pool_eth_price"].plot(ax=ax, label="Pool ETH Price", linestyle="--")

        ax.set_title(f"{self.tag}: ETH Price vs Pool ETH Price")
        ax.set_ylabel("Price (USD)")
        ax.set_xlabel("Day")
        ax.legend()
        ax.grid(True)
        self._save_show(f"{out_dir}/eth_vs_pool_price_curve.png")

        # 正向雙幣 vs 反向雙幣筆數
        counts = [len(self.dep), len(self.reverse_dep)]
        labels = ["Forward Deposit", "Reverse Deposit"]
        def label_format(pct, all_vals):
            count = int(round(pct / 100.0 * sum(all_vals)))
            return f"{pct:.1f}%\n({count})"
        fig, ax = plt.subplots()
        ax.pie(
            counts,
            labels=labels,
            autopct=lambda pct: label_format(pct, counts),
            startangle=90,
        )
        ax.set_title(f"{self.tag}: Forward vs Reverse Count")
        self._save_show(f"{out_dir}/forward_vs_reverse.png")

        ### ----- Forward Dual Deposit ----- ###
        deposits_grouped = self.dep.groupby("day")

        # premium
        deposits_grouped.premium_ratio.sum().plot()
        plt.title(f"{self.tag}: Premium ratio")
        self._save_show(f"{forward_dual_invest_dir}/premium.png")

        # 單 / 雙邊 存款比例
        self.dep.side.value_counts().plot.pie(autopct="%.1f%%", ylabel="")
        plt.title(f"{self.tag}: Single vs Dual")
        self._save_show(f"{forward_dual_invest_dir}/side.png")

        # 正向雙幣用戶 PnL
        if not self.wd.empty:
            sns.histplot(self.wd.pnl_usd, bins=30, kde=True)
            plt.title(f"{self.tag}: User PnL (USD)")
            self._save_show(f"{forward_dual_invest_dir}/pnl_usd.png")

            sns.histplot(self.wd.pnl_ratio, bins=30, kde=True)
            plt.title(f"{self.tag}: User PnL %")
            self._save_show(f"{forward_dual_invest_dir}/pnl_ratio.png")

        ### ----- Reverse Dual Deposit ----- ###
        if not self.reverse_user_pnl_df.empty:
            reverse_dep_grouped = self.reverse_dep.groupby("day_deposit")
            reverse_dep_grouped.premium_ratio.sum().plot()
            plt.title(f"{self.tag}: Reverse Premium Ratio")
            self._save_show(f"{reverse_dual_invest_dir}/reverse_premium.png")

            # Deposit option type 圓餅圖
            option_types = []

            for _, row in self.reverse_user_pnl_df.iterrows():
                if row["m"] > 0:
                    option_types.append("CALL")
                if row["n"] > 0:
                    option_types.append("PUT")
            option_df = pd.DataFrame(option_types, columns=["option_type"])

            option_df.option_type.value_counts().plot.pie(autopct="%.1f%%", ylabel="")
            plt.title(f"{self.tag}: Call vs Put")
            self._save_show(f"{reverse_dual_invest_dir}/option_type.png")

            # 單 / 雙邊 存款比例
            self.reverse_user_pnl_df.single_or_double.value_counts().plot.pie(
                autopct="%.1f%%", ylabel=""
            )
            plt.title(f"{self.tag}: Single vs Double Side")
            self._save_show(f"{reverse_dual_invest_dir}/single_vs_double.png")

            # Reverse User PnL (USD) 分布
            sns.histplot(self.reverse_user_pnl_df.pnl_usd, bins=30, kde=True)
            plt.title(f"{self.tag}: Reverse User PnL (USD)")
            self._save_show(f"{reverse_dual_invest_dir}/reverse_pnl_usd.png")

            # Reverse User PnL % 分布
            sns.histplot(self.reverse_user_pnl_df.pnl_ratio, bins=30, kde=True)
            plt.title(f"{self.tag}: Reverse User PnL %")
            self._save_show(f"{reverse_dual_invest_dir}/reverse_pnl_ratio.png")

            # Reverse User 行權結果分布
            self.reverse_user_pnl_df["exercise_result"].value_counts().plot.pie(
                autopct="%.1f%%", ylabel=""
            )
            plt.title(f"{self.tag}: Exercised vs Not Exercised")
            self._save_show(f"{reverse_dual_invest_dir}/exercised_vs_reverted.png")
