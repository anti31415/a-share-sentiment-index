# -*- coding: utf-8 -*-
"""One-off pull of everything the backtest needs into data/cache/. Safe to re-run (idempotent)."""
import sys, time
import fetch, net

N = int(sys.argv[1]) if len(sys.argv) > 1 else 900

t0 = time.time()
print("[1/4] Index daily bars ...", flush=True)
ix = fetch.index_daily()
print("      %d bars, %s -> %s" % (len(ix), ix[0][0], ix[-1][0]), flush=True)

print("[2/4] Full market listing ...", flush=True)
u = fetch.universe()
print("      %d A-shares" % len(u), flush=True)

smp = fetch.sample_universe(N)
print("[3/4] Sampled per-stock daily bars, n=%d ..." % len(smp), flush=True)
fetch.bulk(lambda x: fetch.stock_daily(fetch.sina_symbol(x[0], x[1])), smp, workers=8, label="kline")

print("[4/4] Sampled per-stock annual-report BPS ...", flush=True)
fetch.bulk(lambda x: fetch.stock_bps(x[0], x[1]), smp, workers=8, label="bps")

print("Done in %.0fs  %s" % (time.time() - t0, net.STATS), flush=True)
