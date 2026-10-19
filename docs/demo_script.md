# SafeSliceSQL — Demo Script

*To be completed in Phase 9.*

## Five-Minute Demonstration Plan

| Time | Step |
|---|---|
| 0:00–0:40 | Explain the problem and show source schema |
| 0:40–1:20 | Run a baseline and show measured query-result loss |
| 1:20–2:20 | Run SafeSliceSQL using the same budget |
| 2:20–3:20 | Inspect the target database and show integrity validation |
| 3:20–4:20 | Show real benchmark plots and raw metric summaries |
| 4:20–5:00 | Explain the contribution, guarantees, and limitations |

## Demo Commands

```bash
# 1. Inspect the source schema
safeslice inspect --database employees

# 2. Run baseline method
safeslice run --method random-repair --budget-percent 20 --seed 42

# 3. Run SafeSliceSQL
safeslice run --method workload-aware --budget-percent 20 --seed 42

# 4. Validate the subset
safeslice validate --source employees --target employees_slice

# 5. Run full benchmark
safeslice benchmark --config benchmarks/benchmarks.yaml
```

## Preparation Checklist

- [ ] Clean demo environment verified
- [ ] All commands tested and working
- [ ] Backup screenshots/terminal recordings prepared
- [ ] No fabricated metrics or unverified claims
