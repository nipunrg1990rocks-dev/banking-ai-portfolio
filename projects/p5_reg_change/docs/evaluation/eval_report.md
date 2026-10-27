# Regulatory Change Impact Analyzer: evaluation

Mode: offline rules engine. As-of date: 2026-10-26. Answer key: data/eval/answer_key.json.

| Metric | Result |
|---|---|
| Change detection (NEW / AMENDED / UNCHANGED / REMOVED) | 100% of 44 provisions |
| Parameter deltas (old figure, new figure, direction) | 100% of 14 |
| Gap status (COVERED / PARTIAL / CONFLICT / MISSING) | 91% of 33 |
| Gap detection recall / precision | 100% / 100% |
| Unsafe misses (real gap reported as covered) | 0 |
| False alarms (covered reported as a gap) | 0 |
| Policy citation accuracy | 82% |
| Wrong statuses sent to human review by the Challenger | 3 of 3 |

Caveat: the circulars, policies and answer key are synthetic and were written for this project. Thresholds were tuned on the four development circulars.

| Split | Gap status | Citation | Unsafe misses |
|---|---|---|---|
| Development (4 circulars) | 89% of 28 | 87% | 0 |
| Held-out (NPCA-FC-2026-22) | 100% of 5 | 60% | 0 |

## Wrong statuses

- NPCA-AI-2026-03 §5.1: expected PARTIAL, got CONFLICT (sent to human review)
- NPCA-AI-2026-03 §6.1: expected PARTIAL, got MISSING (sent to human review)
- NPCA-FC-2026-19 §5.1: expected PARTIAL, got MISSING (sent to human review)
