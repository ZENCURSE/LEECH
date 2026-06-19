def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def human_size_pair(done: float, total: float) -> str:
    """
    Compact 'done / total' size string. Drops the repeated unit on the
    first number when both share the same unit (most common case),
    e.g. '18.0/503.7 MB' instead of '18.0 MB / 503.7 MB' — meaningfully
    shorter, which matters on narrow phone screens where the longer
    form wraps onto a second line and breaks the card's border.
    """
    d_str = human_size(done)
    t_str = human_size(total)
    d_num, d_unit = d_str.rsplit(" ", 1)
    t_num, t_unit = t_str.rsplit(" ", 1)
    if d_unit == t_unit:
        return f"{d_num}/{t_num} {t_unit}"
    return f"{d_str} / {t_str}"


def human_speed(bps: float) -> str:
    return human_size(bps) + "/s"


def human_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"


def bar(pct: float, width: int = 14) -> str:
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)
