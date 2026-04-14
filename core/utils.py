import time


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def print_pair_argument_error():
    print("Error: PAIR parameter is required.")
    print("Use: python trading/market_analyzer.py PAIR=XBTEUR [ORDER=20] [SHOW_EVENTS] [Volatility=LL|LV|MV|HV|HH|ALL]")


def print_statistics(events, vol_level, title):
    if not events:
        print(f"\nNo events detected for {title}\n")
        return

    k_values = [e['volatility_levels'][vol_level]['k_value'] for e in events if vol_level in e['volatility_levels']]
    if not k_values:
        print(f"\nNo events detected for {title}\n")
        return

    import pandas as pd
    s = pd.Series(k_values)
    print(f"\n=== {title} ===")
    print(f"Events: {len(s)} | Average: {s.mean():.2f} ATR")
    print(f"Percentile 50%: {s.quantile(0.50):.2f} ATR (Very Tight)")
    print(f"Percentile 75%: {s.quantile(0.75):.2f} ATR (Standard)")
    print(f"Percentile 90%: {s.quantile(0.90):.2f} ATR (Safe)")
    print(f"Percentile 95%: {s.quantile(0.95):.2f} ATR (Protected)")
    print(f"Percentile 100%: {s.quantile(1.00):.2f} ATR (Extreme)")


def print_events_detail(events, title, vol_level=None):
    if not events:
        return
    print(f"\n=== {title} ===")

    if vol_level is None:
        print(f"{'From':<20} | {'To':<20} | {'Change %':>10}")
        print("-" * 55)
        for event in events:
            change_pct = event['price_change_pct'] * 100
            print(f"{str(event['start_dtime']):<20} | {str(event['end_dtime']):<20} | {change_pct:>9.2f}%")
    else:
        print(f"{'From':<20} | {'To':<20} | {'Change %':>10} | {'Max Value':>10} | {'ATR at max':>10} | {'K Value':>8}")
        print("-" * 120)
        for event in events:
            if vol_level not in event['volatility_levels']:
                continue
            change_pct = event['price_change_pct'] * 100
            vol_data = event['volatility_levels'][vol_level]
            print(f"{str(event['start_dtime']):<20} | {str(event['end_dtime']):<20} | {change_pct:>9.2f}% "
                  f"| {vol_data['max_value']:>10.1f} | {vol_data['atr_at_max']:>10.1f} | {vol_data['k_value']:>8.2f}")


def print_structural_noise_results(
    uptrend_events,
    downtrend_events,
    min_change_pct,
    atr_percentiles,
    show_events=False,
    volatility_level=None,
):
    print(f"--- Analyzing Market Structure (minimum change {min_change_pct*100:.2f}%) ---")

    if volatility_level is None:
        print_events_detail(uptrend_events, "UPTREND EVENTS")
        print_events_detail(downtrend_events, "DOWNTREND EVENTS")
        return

    print("ATR P20: {p20:.1f} | P50: {p50:.1f} | P80: {p80:.1f} | P95: {p95:.1f}".format(**atr_percentiles))

    if volatility_level == 'ALL':
        vol_levels = ['LL', 'LV', 'MV', 'HV', 'HH']
    else:
        vol_levels = [volatility_level]

    for vol_level in vol_levels:
        uptrend_vol = [e for e in uptrend_events if vol_level in e['volatility_levels']]
        downtrend_vol = [e for e in downtrend_events if vol_level in e['volatility_levels']]

        print(f"\n{'='*60}")
        print(f"VOLATILITY LEVEL: {vol_level}")
        print(f"{'='*60}")
        print_statistics(uptrend_vol, vol_level, "UPTREND NOISE (Stop Loss configuration)")
        print_statistics(downtrend_vol, vol_level, "DOWNTREND NOISE (Reentry Stop configuration)")

        if show_events:
            print_events_detail(uptrend_vol, "UPTREND EVENTS", vol_level)
            print_events_detail(downtrend_vol, "DOWNTREND EVENTS", vol_level)