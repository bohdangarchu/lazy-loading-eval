MODE_COLORS: dict[str, str] = {
    "2dfs":                          "#1f77b4",
    "2dfs-stargz":                   "#ff7f0e",
    "2dfs-stargz-zstd":              "#9467bd",
    "2dfs-stargz-with-bg-fetch":     "#e377c2", # refresh-layer
    "2dfs-stargz-zstd-with-bg-fetch":"#8c564b", # refresh-layer
    "stargz":                        "#2ca02c",
    "base":                          "#d62728",
}


def figure_footer(
    fig, model: str, base_image: str, fontsize: int = 8,
    max_allowed_splits: int | None = None,
) -> None:
    """Stamp bottom-left corner of a figure with model/image metadata."""
    text = f"model: {model}\nbase image: {base_image}"
    if max_allowed_splits is not None:
        text += f"\nmax_allowed_splits: {max_allowed_splits}"
    fig.text(
        0.01, 0.01,
        text,
        fontsize=fontsize,
        verticalalignment="bottom",
        family="monospace",
    )


def add_run_dots(ax, x_center: float, values: list[float], s: int = 12) -> None:
    """Scatter individual run dots with jitter around x_center."""
    for k, val in enumerate(values):
        jitter = (k - len(values) / 2) * 0.015
        ax.scatter(x_center + jitter, val, color="black", s=s, zorder=4)


def bar_group_xticks(
    ax, n_groups: int, n_modes: int, bar_width: float, labels: list[str]
) -> None:
    """Set centered xticks for grouped bar charts."""
    center_offset = (n_modes - 1) * bar_width / 2
    ax.set_xticks([j + center_offset for j in range(n_groups)])
    ax.set_xticklabels(labels)


def save_figure(fig, path: str, dpi: int = 150) -> None:
    """savefig + close + log. Caller is responsible for tight_layout."""
    import matplotlib.pyplot as plt
    from shared import log
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    log.result(f"Chart saved to {path}")


def write_csv(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    """Write a DictWriter CSV to path, creating parent dirs. Logs filename."""
    import csv
    import os
    from shared import log
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.result(f"Results saved to {path}")
