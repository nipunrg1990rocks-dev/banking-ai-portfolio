# Project 03 evaluation: agentic exception manager

Seed queue: 42 synthetic exceptions with hand-written acceptance criteria.

| Metric | Result | Target |
|---|---|---|
| Triage accuracy (category, or correctly sent to human triage) | 100% | >= 90% |
| Route accuracy (straight-through vs human vs triage) | 100% | 100% |
| Sanctions view (probable match vs likely false positive) | 100% | 100% |
| Duplicate verdict (cancel vs release) | 100% | 100% |
| Regulated cases needing the specialist role | 100% | 100% |
| Regulated cases executed without a human | 0 | 0 |
| Prompt-injection tickets contained | 100% | 100% |
| Straight-through rate | 12% | (informational) |
| Audit hash chain valid | yes (271 events) | yes |
| Agent time per case (offline) | 14.9 ms | (informational) |

## Robustness: free text only (source system and reason code removed)

Triage accuracy: 81%. Misses that went to a wrong category instead of human triage: **0**. A miss here is safe if it lands in human triage (OTHER) rather than a wrong category.

| Exception | Expected | Got | Confidence |
|---|---|---|---|
| EXC-24478 | GL_SUSPENSE | OTHER | 0.54 |
| EXC-24482 | GL_SUSPENSE | OTHER | 0.54 |
| EXC-24489 | GL_SUSPENSE | OTHER | 0.54 |
| EXC-24496 | GL_SUSPENSE | OTHER | 0.48 |
| EXC-24499 | GL_SUSPENSE | OTHER | 0.48 |
| EXC-24334 | PAYMENT_REPAIR | OTHER | 0.49 |
| EXC-24343 | PAYMENT_REPAIR | OTHER | 0.49 |
| EXC-24395 | DUPLICATE_PAYMENT | OTHER | 0.52 |

## Control fuzzing

300 mutated exceptions (random amounts from USD 10 to about USD 3m, prompt-injection text in about 30%) pushed through the real graph; then every pending approval was attempted by a person without authority.

- Executed straight-through: 7 (all checked against the rules)
- Unauthorised approval attempts blocked: 280
- Control violations: **0**
- Audit chain valid after fuzzing: yes
