# -*- coding: utf-8 -*-
"""Minimal HTTP layer: retries + gzip disk cache. No third-party dependencies."""
import gzip, json, os, random, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "cache")
os.makedirs(CACHE, exist_ok=True)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
STATS = {"http": 0, "retry": 0, "cache": 0, "fail": 0}


def fetch(url, retries=6, timeout=30, data=None, headers=None):
    h = dict(_UA)
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    last = None
    for i in range(retries):
        try:
            # some market-data gateways cache a bad response; add a random
            # param to dodge that
            u = url + ("&" if "?" in url else "?") + "_=%d" % random.randrange(10 ** 13)
            raw = urllib.request.urlopen(
                urllib.request.Request(u, data=body, headers=h), timeout=timeout).read()
            STATS["http"] += 1
            return raw.decode("utf-8", "ignore")
        except Exception as e:                       # noqa: BLE001
            last = e
            STATS["retry"] += 1
            time.sleep(min(0.3 * (i + 1), 3.0) * (0.5 + random.random()))
    STATS["fail"] += 1
    raise RuntimeError("fetch failed %s: %r" % (url[:100], last))


def cached_json(key, build):
    """data/cache/<key>.json.gz -- returned on a hit, otherwise build() fetches and it's persisted."""
    p = os.path.join(CACHE, key + ".json.gz")
    if os.path.exists(p) and os.path.getsize(p) > 1:
        STATS["cache"] += 1
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        except Exception:                            # noqa: BLE001
            os.remove(p)                             # corrupted cache -- refetch
    data = build()
    tmp = p + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)
    return data
