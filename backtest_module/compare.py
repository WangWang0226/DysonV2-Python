# compare.py
import os, pandas as pd, matplotlib.pyplot as plt, seaborn as sns

sns.set_style("whitegrid")
plt.rcParams["figure.figsize"] = (10, 6)


def _plot_k_absolute(k_curves, out_dir):
    """原本的絕對值折線（可保留）"""
    plt.figure(figsize=(11, 5))
    for tag, ser in k_curves:
        ser.plot(label=tag, linewidth=1.5)
    plt.legend()
    plt.xlabel("day")
    plt.ylabel("k value")
    plt.title("k value – all scenarios (absolute)")
    plt.tight_layout()
    plt.savefig(f"{out_dir}/k_curve_abs.png", dpi=120)
    plt.show()


def _plot_k_pct(k_curves, out_dir):
    """把每條線轉為%變化後再畫（重點：線疊在一起比較清楚）"""
    plt.figure(figsize=(11, 5))
    for tag, ser in k_curves:
        ((ser / ser.iloc[0] - 1) * 100).plot(label=tag, linewidth=1.5)
    plt.legend()
    plt.xlabel("day")
    plt.ylabel("Δk  (%)")
    plt.title("k value – relative change (%)")
    plt.axhline(0, color="gray", lw=1)
    plt.tight_layout()
    plt.savefig(f"{out_dir}/k_curve_pct.png", dpi=120)
    plt.show()


def _facet_k(k_curves, out_dir):
    """每個 scenario 一張小圖，自動 y-axis → 看絕對值也能看到波動"""
    n = len(k_curves)
    cols = 3
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(
        rows, cols, figsize=(5 * cols, 3 * rows), sharex=False, sharey=False
    )
    axes = axes.flatten()
    for ax, (tag, ser) in zip(axes, k_curves):
        ser.plot(ax=ax, linewidth=1.4)
        ax.set_title(tag)
        ax.set_xlabel("day")
        ax.set_ylabel("k")
    # 清掉沒用到的子圖
    for ax in axes[len(k_curves) :]:
        ax.remove()
    plt.suptitle(
        "k value – each scenario (absolute, individual scale)", y=1.02, fontsize=14
    )
    plt.tight_layout()
    plt.savefig(f"{out_dir}/k_curve_facet.png", dpi=120)
    plt.show()


def compare_runs(results: dict, out_dir="results/_compare"):
    """
    results  : dict  {(horizon, init_eth): (dep_df, wd_df, snap_df, info_dict)}
    out_dir  : 資料輸出根目錄
    -------------------------------------------------------------

    """
    os.makedirs(out_dir, exist_ok=True)

    rows, k_curves = [], []
    for (h, ie), (dep, wd, snap, info) in results.items():

        tag = f"{h}d-{ie}ETH"
        # -------- k 相關 --------
        k_series = snap.set_index("day").k
        init_k, final_k = k_series.iloc[0], k_series.iloc[-1]
        k_change_pct = (final_k - init_k) / init_k * 100

        # 儲存曲線 (之後畫疊圖)
        k_curves.append((tag, k_series))

        # -------- PnL 相關 --------
        tot_pnl = wd.pnl_usd.sum() if not wd.empty else 0
        avg_pnl = wd.pnl_usd.mean() if not wd.empty else 0

        rows.append(
            dict(
                scenario=tag,
                horizon=h,
                init_eth=ie,
                k_init=init_k,
                k_final=final_k,
                k_change_pct=k_change_pct,
                pnl_total_usd=tot_pnl,
                pnl_avg_usd=avg_pnl,
            )
        )

    summary = pd.DataFrame(rows).sort_values(["horizon", "init_eth"])
    summary.to_csv(f"{out_dir}/summary.csv", index=False)

    # ---------- 圖表 ----------
    # (1) k 曲線圖
    _plot_k_absolute(k_curves, out_dir)  # 原圖 (可選)
    _plot_k_pct(k_curves, out_dir)  # 相對變化 (%)
    _facet_k(k_curves, out_dir)  # 分面小圖

    # (3) Total PnL
    sns.barplot(data=summary, x="scenario", y="pnl_total_usd")
    plt.xticks(rotation=25)
    plt.ylabel("Total PnL  (USD)")
    plt.title("Total user PnL")
    plt.savefig(f"{out_dir}/pnl_total.png", dpi=110)
    plt.show()

    # (4) Average PnL
    sns.barplot(data=summary, x="scenario", y="pnl_avg_usd")
    plt.xticks(rotation=25)
    plt.ylabel("Avg PnL  (USD)")
    plt.title("Average user PnL")
    plt.savefig(f"{out_dir}/pnl_avg.png", dpi=110)
    plt.show()

    print(f"[compare] ✅  saved to {out_dir}")
