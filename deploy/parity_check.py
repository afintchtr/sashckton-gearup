"""Run in a FRESH interpreter (python -I): loads only the exported bundle, scores a parquet, measures single-request latency."""
import json
import sys
import time

import numpy as np
import pandas as pd

bundle, inp, out = sys.argv[1:4]
sys.path.insert(0, bundle)
from score import FraudScorer  # noqa: E402

sc = FraudScorer(bundle)
df = pd.read_parquet(inp)
res = sc.score_df(df, reasons=False)
np.savez(out, raw=res.score_raw.values, cal=res.score.values, band=res.band.values.astype(str), quality=res.quality.values.astype(str))

head = df.head(200)
recs = head.astype(object).where(head.notna(), None).to_dict("records")     # JSON-like requests, None = unavailable
sc.score_record(recs[0])                                                    # warm-up
lat = []
for r in recs:
    t = time.perf_counter()
    sc.score_record(r)
    lat.append((time.perf_counter() - t) * 1000)
print(json.dumps({"n_requests": len(lat), "p50_ms": round(float(np.percentile(lat, 50)), 1), "p95_ms": round(float(np.percentile(lat, 95)), 1),
                  "max_ms": round(float(max(lat)), 1)}))
