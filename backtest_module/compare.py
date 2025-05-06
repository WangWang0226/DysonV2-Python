import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, List, Tuple, Optional

# Configuration
PLOT_CONFIG = {
    "figsize": (10, 6),
    "dpi": 120,
    "barplot_rotation": 25,
    "facet_cols": 3,
    "facet_figsize": (5, 3.2),
    "bins": 30,
    "linewidth": 1.5,
    "font_size": {"title": 14, "subtitle": 9},
}

sns.set_style("whitegrid")
plt.rcParams["figure.figsize"] = PLOT_CONFIG["figsize"]


class ScenarioComparer:
    """Compare multiple backtest scenarios and generate visualizations."""

    def __init__(
        self,
        results: Dict[
            Tuple[int, float, float],
            Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict],
        ],
        out_dir: str = "results/_compare",
    ):
        """Initialize with results dictionary and output directory."""
        if not results:
            raise ValueError("Results dictionary cannot be empty")
        self.results = results
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.summary = None
        self.k_curves = []
        self.pnls = []
        self.pnl_ratios = []

    def _process_data(self) -> None:
        """Process results to generate summary, k curves, and PnL data."""
        rows = []
        for (horizon, init_eth, scale), (dep, wd, snap, info) in self.results.items():
            tag = f"{horizon}d-{init_eth}ETH-{scale}x"

            # Process k data
            k_series = snap.set_index("day")["k"]
            init_k, final_k = k_series.iloc[0], k_series.iloc[-1]
            k_change_pct = (final_k - init_k) / init_k * 100

            # Process PnL data
            tot_pnl = wd["pnl_usd"].sum() if not wd.empty else 0
            avg_pnl = wd["pnl_usd"].mean() if not wd.empty else 0

            # Store data
            self.k_curves.append((tag, k_series))
            self.pnls.append((tag, wd["pnl_usd"]))
            self.pnl_ratios.append((tag, wd["pnl_ratio"]))
            rows.append(
                {
                    "scenario": tag,
                    "horizon": horizon,
                    "init_eth": init_eth,
                    "k_init": init_k,
                    "k_final": final_k,
                    "k_change_pct": k_change_pct,
                    "pnl_total_usd": tot_pnl,
                    "pnl_avg_usd": avg_pnl,
                    "scale": scale,
                }
            )

        self.summary = pd.DataFrame(rows).sort_values(["horizon", "init_eth"])

    def _clean_unused_axes(self, axes: List, n_plots: int) -> None:
        """Remove unused subplot axes."""
        for ax in axes[n_plots:]:
            ax.remove()

    def _save_plot(self, filename: str) -> None:
        """Save and display plot with error handling."""
        try:
            plt.tight_layout()
            plt.savefig(
                os.path.join(self.out_dir, filename),
                dpi=PLOT_CONFIG["dpi"],
                bbox_inches="tight",
            )
            plt.show()
        except Exception as e:
            print(f"Failed to save {filename}: {e}")
        finally:
            plt.close()

    def _plot_summary_bar(
        self, column: str, ylabel: str, title: str, filename: str
    ) -> None:
        """Plot bar chart for summary metrics."""
        plt.figure()
        sns.barplot(data=self.summary, x="scenario", y=column)
        plt.xticks(rotation=PLOT_CONFIG["barplot_rotation"])
        plt.ylabel(ylabel)
        plt.title(title)
        self._save_plot(filename)

    def _plot_k_curves(self) -> None:
        """Plot k curves (absolute and percentage change)."""
        # Absolute k curves
        plt.figure()
        for tag, ser in self.k_curves:
            ser.plot(label=tag, linewidth=PLOT_CONFIG["linewidth"])
        plt.legend()
        plt.xlabel("day")
        plt.ylabel("k value")
        plt.title("k value – all scenarios (absolute)")
        self._save_plot("k_curve_abs.png")

        # Percentage change k curves
        plt.figure()
        for tag, ser in self.k_curves:
            ((ser / ser.iloc[0] - 1) * 100).plot(
                label=tag, linewidth=PLOT_CONFIG["linewidth"]
            )
        plt.legend()
        plt.xlabel("day")
        plt.ylabel("Δk (%)")
        plt.title("k value – relative change (%)")
        plt.axhline(0, color="gray", lw=1)
        self._save_plot("k_curve_pct.png")

    def _plot_facet_k(self) -> None:
        """Plot faceted k curves with individual scales."""
        n = len(self.k_curves)
        cols = PLOT_CONFIG["facet_cols"]
        rows = (n + cols - 1) // cols
        fig, axes = plt.subplots(
            rows,
            cols,
            figsize=(
                PLOT_CONFIG["facet_figsize"][0] * cols,
                PLOT_CONFIG["facet_figsize"][1] * rows,
            ),
        )
        axes = axes.flatten()

        for ax, (tag, ser) in zip(axes, self.k_curves):
            ser.plot(ax=ax, linewidth=PLOT_CONFIG["linewidth"])
            ax.set_title(tag, fontsize=PLOT_CONFIG["font_size"]["subtitle"])
            ax.set_xlabel("day")
            ax.set_ylabel("k")

        self._clean_unused_axes(axes, n)
        plt.suptitle(
            "k value – each scenario (absolute, individual scale)",
            y=1.02,
            fontsize=PLOT_CONFIG["font_size"]["title"],
        )
        self._save_plot("k_curve_facet.png")

    def _plot_facet_hist(
        self, data: List[Tuple[str, pd.Series]], column: str, title: str, filename: str
    ) -> None:
        """Plot faceted histograms for PnL or PnL ratio."""
        n = len(data)
        if n == 0:
            return

        cols = PLOT_CONFIG["facet_cols"]
        rows = (n + cols - 1) // cols
        fig, axes = plt.subplots(
            rows,
            cols,
            figsize=(
                PLOT_CONFIG["facet_figsize"][0] * cols,
                PLOT_CONFIG["facet_figsize"][1] * rows,
            ),
        )
        axes = axes.flatten()

        for ax, (tag, ser) in zip(axes, data):
            if ser.empty:
                ax.set_visible(False)
                continue
            sns.histplot(ser, bins=PLOT_CONFIG["bins"], kde=True, ax=ax)
            ax.set_title(tag, fontsize=PLOT_CONFIG["font_size"]["subtitle"])
            ax.set_xlabel(column)
            ax.set_ylabel("Count")

        self._clean_unused_axes(axes, n)
        plt.suptitle(title, y=1.02, fontsize=PLOT_CONFIG["font_size"]["title"])
        self._save_plot(filename)

    def compare(self) -> None:
        """Generate all comparisons and save results."""
        self._process_data()
        self.summary.to_csv(os.path.join(self.out_dir, "summary.csv"), index=False)

        # Summary bar plots
        self._plot_summary_bar(
            "k_change_pct", "Δk (%)", "k change (%)", "k_change_pct.png"
        )
        self._plot_summary_bar(
            "pnl_total_usd", "Total PnL (USD)", "Total user PnL", "pnl_total.png"
        )
        self._plot_summary_bar(
            "pnl_avg_usd", "Avg PnL (USD)", "Average user PnL", "pnl_avg.png"
        )

        # k curve plots
        self._plot_k_curves()
        self._plot_facet_k()

        # PnL histograms
        self._plot_facet_hist(
            self.pnls,
            "User PnL (USD)",
            "User PnL distribution – different scales",
            "pnl_hist_facet.png",
        )
        self._plot_facet_hist(
            self.pnl_ratios,
            "User PnL (Ratio)",
            "User PnL ratio distribution – different scales",
            "pnl_ratio_hist_facet.png",
        )

        print(f"[compare] ✅ saved to {self.out_dir}")


def compare_runs(
    results: Dict[
        Tuple[int, float, float], Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]
    ],
    out_dir: str = "results/_compare",
) -> None:
    """Wrapper function to maintain compatibility with existing code."""
    comparer = ScenarioComparer(results, out_dir)
    comparer.compare()
