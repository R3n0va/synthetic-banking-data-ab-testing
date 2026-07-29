# Generated Data

`src/build_experiment_data.py` creates local CSV files in:

```text
data/source/
data/experiments/
```

`source/` contains read-only customer-level extracts from PostgreSQL. `experiments/` contains deterministic customer assignment and synthetic experiment-period outcomes.

Generated CSV files are excluded from Git.
