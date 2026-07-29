# Synthetic Banking A/B Testing

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Jupyter](https://img.shields.io/badge/Jupyter-2%20notebooks-orange)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-blue)
![Experiments](https://img.shields.io/badge/A%2FB%20tests-3-brightgreen)
![Status](https://img.shields.io/badge/status-ready-success)

A reproducible experimentation project for three synthetic Treasury Products A/B tests built on the existing Synthetic Banking PostgreSQL model.

The repository demonstrates the complete Data Analyst experimentation workflow: business hypothesis, eligible population, customer-level randomisation, experiment validation, power analysis, treatment-effect estimation, guardrail control, and an explicit product decision.

All banking records and experiment outcomes are synthetic.

---

## Portfolio Position

```text
Synthetic Banking Data Generator
              │
              ▼
Synthetic Banking SQL
              │
              ▼
Synthetic Banking Analytics
              │
              ▼
Synthetic Banking Data Quality
              │
              ▼
Synthetic Banking A/B Testing
```

The project reads the local `synthetic_banking_sql` database created by the preceding repositories. It does not create another database or modify source tables.

---

## Experiment Portfolio

| ID | Experiment | Business decision | Primary metric |
|---|---|---|---|
| `EXP-01` | Live FX spread discount | Should a lower FX spread be launched broadly? | Revenue per assigned customer |
| `EXP-02` | DCD cross-sell outreach | Should eligible FX-only customers receive a targeted DCD offer? | DCD conversion rate |
| `EXP-03` | Digital channel migration | Should assisted-channel FX customers receive a digital migration prompt? | Digital adoption rate |

The tests cover three different product decisions: pricing, product cross-sell, and channel behaviour.

---

## EXP-01 — Live FX Spread Discount

**Population:** active customers with at least two Live FX transactions in the canonical 90-day pre-period.

**Treatment:** an 8% relative reduction in effective spread combined with a configured activity response.

**Primary metric:** Live FX revenue USD per assigned customer.

**Secondary metrics:**

- FX conversion rate;
- transactions per assigned customer;
- turnover USD per assigned customer.

**Diagnostics:**

- revenue USD per completed transaction;
- average effective spread.

The pre-specified primary analysis uses CUPED with pre-period customer revenue as the covariate. The experiment launches only when the adjusted revenue effect is positive and statistically significant.

---

## EXP-02 — DCD Cross-Sell Outreach

**Population:** active FX customers who have an effective DCD master agreement and no previous non-cancelled DCD contract.

**Treatment:** a targeted DCD cross-sell message.

**Primary metric:** DCD conversion rate.

**Secondary metric:** DCD fee revenue USD per assigned customer.

**Guardrail:** Live FX revenue USD per assigned customer.

The historical conversion baseline is built from a prior eligible cohort and a separate 45-day outcome window. Launch requires a positive significant conversion effect, non-negative fee revenue impact, and an FX revenue confidence bound above the configured non-inferiority floor.

---

## EXP-03 — Digital Channel Migration

**Population:** active customers whose recent FX activity is dominated by `BRANCH` or `TELEPHONE`.

**Treatment:** a prompt encouraging the next FX transaction through `BANK_ONLINE` or `MOBILE_BANKING`.

**Primary metric:** digital adoption rate.

**Secondary metrics:**

- mean customer digital transaction share;
- transactions per assigned customer.

**Guardrail:** Live FX revenue USD per assigned customer.

Launch requires a positive significant adoption effect and a revenue confidence bound above the configured non-inferiority floor.

---

## Analytical Standards

Every experiment applies:

1. customer-level randomisation;
2. deterministic experiment-specific seeds;
3. stratified 50/50 assignment;
4. sample-ratio-mismatch testing;
5. pre-treatment balance checks;
6. power and minimum-detectable-effect review;
7. intent-to-treat analysis;
8. confidence intervals and effect sizes;
9. Holm correction for secondary metric families;
10. `LAUNCH`, `DO NOT LAUNCH`, or `INCONCLUSIVE` decisions.

Revenue per assigned customer retains zero outcomes for customers who do not transact or convert.

---

## Synthetic Experiment Layer

The source database contains historical banking activity, not real randomised experiments. SQL determines eligibility, customer strata, historical activity, revenue scale, and baseline rates. Python then creates a transparent, reproducible experiment layer containing:

- deterministic assignment;
- configured treatment effects;
- stochastic customer outcomes;
- customer-level analysis datasets.

The project does not present historical observational differences as causal effects.

---

## Repository Structure

```text
synthetic-banking-ab-testing/
│
├── config/
│   └── experiment_config.yml
│
├── data/
│   ├── experiments/
│   ├── source/
│   └── README.md
│
├── notebooks/
│   ├── 01_experiment_design.ipynb
│   └── 02_experiment_analysis.ipynb
│
├── results/
│   └── README.md
│
├── sql/
│   ├── 00_foundation/
│   ├── 01_fx_spread_discount/
│   ├── 02_dcd_cross_sell/
│   └── 03_digital_channel_migration/
│
├── src/
│   ├── build_experiment_data.py
│   └── experiment_statistics.py
│
├── .env.example
├── .gitignore
├── LICENSE
├── requirements.txt
└── README.md
```

---

## Requirements

- Python 3.11 or newer;
- PostgreSQL 17;
- PostgreSQL `psql` command-line client;
- local `synthetic_banking_sql` database;
- analytical views created by Synthetic Banking Analytics.

A virtual environment is recommended because the statistical analysis uses third-party packages.

---

## Local Setup

### 1. Create the virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure PostgreSQL

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```env
PGHOST=localhost
PGPORT=5432
PGUSER=postgres
PGDATABASE=synthetic_banking_sql
PGPASSWORD=change_me
PGSSLMODE=prefer
```

### 3. Build the experiment datasets

```powershell
python src/build_experiment_data.py
```

Build one experiment only:

```powershell
python src/build_experiment_data.py --experiment EXP-01
```

A single-experiment run uses exactly the same experiment seed as a complete run.

### 4. Run the notebooks

```powershell
jupyter lab
```

Run in order:

```text
notebooks/01_experiment_design.ipynb
notebooks/02_experiment_analysis.ipynb
```

---

## Generated Files

The builder writes customer-level extracts and experiment datasets to `data/`. The notebooks write design, metric, and decision summaries to `results/`.

Generated CSV files are excluded from Git. The repository publishes the SQL, simulation logic, statistical methods, and notebooks required to reproduce them.

---

## Reproducibility and Safety

- SQL uses `analytics.v_analysis_parameters.as_of_date`; `CURRENT_DATE` is not used.
- Seeds are fixed separately for each experiment.
- Source tables are read only.
- No schemas, tables, or views are created or modified.
- The same database, configuration, and code produce the same assignment and outcomes.

---

## Deliberate Exclusions

The project does not add Docker, cloud orchestration, an experiment database schema, machine-learning uplift models, sequential testing, multi-armed bandits, or dashboarding.

The scope is three complete and reviewable A/B tests for a Data Analyst portfolio.
