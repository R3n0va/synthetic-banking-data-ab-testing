from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config" / "experiment_config.yml"
ENV_FILE = ROOT / ".env"
FOUNDATION_SQL = ROOT / "sql" / "00_foundation" / "01_validate_sources.sql"

REQUIRED_SOURCE_COLUMNS = {
    "EXP-01": {
        "customer_id",
        "segment_code",
        "branch_code",
        "dominant_channel_code",
        "prior_fx_transactions",
        "prior_fx_active_days",
        "prior_fx_turnover_usd",
        "prior_fx_revenue_usd",
        "prior_avg_ticket_usd",
        "prior_avg_spread",
        "prior_revenue_yield_bps",
    },
    "EXP-02": {
        "customer_id",
        "segment_code",
        "branch_code",
        "prior_fx_transactions",
        "prior_fx_turnover_usd",
        "prior_fx_revenue_usd",
        "historical_dcd_conversion_rate",
        "average_dcd_fee_usd",
    },
    "EXP-03": {
        "customer_id",
        "segment_code",
        "branch_code",
        "dominant_assisted_channel_code",
        "prior_fx_transactions",
        "prior_assisted_transactions",
        "prior_digital_transactions",
        "prior_digital_share",
        "prior_fx_revenue_usd",
        "historical_digital_adoption_rate",
    },
}



def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            raise SystemExit(
                f"Invalid .env entry at line {line_number}: expected KEY=VALUE."
            )

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            raise SystemExit(
                f"Invalid .env entry at line {line_number}: empty key."
            )

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        os.environ.setdefault(key, value)


def load_config() -> dict[str, Any]:
    payload = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    if "project" not in payload or "experiments" not in payload:
        raise SystemExit(
            "Configuration must contain project and experiments sections."
        )
    return payload


def require_psql() -> None:
    if shutil.which("psql") is None:
        raise SystemExit(
            "psql was not found in PATH.\n"
            "Add the PostgreSQL bin directory to PATH, for example:\n"
            r"  C:\Program Files\PostgreSQL\17\bin"
        )


def connection_settings() -> dict[str, str]:
    return {
        "host": os.getenv("PGHOST", "localhost"),
        "port": os.getenv("PGPORT", "5432"),
        "user": os.getenv("PGUSER", "postgres"),
        "database": os.getenv("PGDATABASE", "synthetic_banking_sql"),
    }


def psql_base() -> list[str]:
    settings = connection_settings()
    return [
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        settings["host"],
        "-p",
        settings["port"],
        "-U",
        settings["user"],
        "-d",
        settings["database"],
    ]


def print_connection_target() -> None:
    settings = connection_settings()
    print(
        "Connection target: "
        f"host={settings['host']} "
        f"port={settings['port']} "
        f"database={settings['database']} "
        f"user={settings['user']}"
    )
    print("Configuration source: .env and/or operating-system environment")
    print("Password: configured" if os.getenv("PGPASSWORD") else "Password: not set")


def test_connection() -> None:
    command = psql_base()
    command.extend(
        ["-Atqc", "SELECT current_database() || '|' || current_user;"]
    )
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    database, _, user = result.stdout.strip().partition("|")
    print(f"Connected successfully: database={database}, user={user}")


def validate_sources() -> None:
    command = psql_base()
    command.extend(["-f", str(FOUNDATION_SQL)])
    subprocess.run(command, cwd=ROOT, check=True)


def export_sql(sql_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = psql_base()
    command.extend(["--csv", "-f", str(sql_path)])

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        subprocess.run(
            command,
            cwd=ROOT,
            stdout=handle,
            check=True,
        )


def normalise_numeric(
    frame: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def cap_population(
    frame: pd.DataFrame,
    sample_cap: int,
    random_seed: int,
) -> pd.DataFrame:
    if len(frame) <= sample_cap:
        return frame.reset_index(drop=True)

    return (
        frame.sample(
            n=sample_cap,
            replace=False,
            random_state=random_seed,
        )
        .sort_values("customer_id")
        .reset_index(drop=True)
    )


def stratified_assignment(
    frame: pd.DataFrame,
    strata: list[str],
    treatment_share: float,
    random_seed: int,
) -> pd.Series:
    rng = np.random.default_rng(random_seed + 3)
    assignment = pd.Series("control", index=frame.index, dtype="object")

    working = frame.copy()
    for column in strata:
        working[column] = working[column].fillna("UNKNOWN").astype(str)

    group_key: str | list[str] = strata[0] if len(strata) == 1 else strata

    for _, group in working.groupby(group_key, sort=True, dropna=False):
        indices = group.index.to_numpy(copy=True)
        rng.shuffle(indices)
        treatment_n = int(round(len(indices) * treatment_share))
        assignment.loc[indices[:treatment_n]] = "treatment"

    return assignment


def mean_one_lognormal(
    rng: np.random.Generator,
    size: int,
    sigma: float,
) -> np.ndarray:
    return rng.lognormal(
        mean=-0.5 * sigma**2,
        sigma=sigma,
        size=size,
    )


def safe_positive(
    series: pd.Series,
    fallback: float,
) -> np.ndarray:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    finite_positive = values[np.isfinite(values) & (values > 0)]
    replacement = (
        float(np.median(finite_positive))
        if finite_positive.size
        else fallback
    )
    values[~np.isfinite(values) | (values <= 0)] = replacement
    return values


def simulate_fx_spread(
    source: pd.DataFrame,
    config: dict[str, Any],
    project: dict[str, Any],
    random_seed: int,
) -> pd.DataFrame:
    numeric_columns = [
        "customer_id",
        "prior_fx_transactions",
        "prior_fx_active_days",
        "prior_fx_turnover_usd",
        "prior_fx_revenue_usd",
        "prior_avg_ticket_usd",
        "prior_avg_spread",
        "prior_revenue_yield_bps",
    ]
    source = normalise_numeric(source.copy(), numeric_columns)
    source = cap_population(source, int(config["sample_cap"]), random_seed + 1)

    source["variant"] = stratified_assignment(
        source,
        list(config["strata"]),
        float(project["expected_treatment_share"]),
        random_seed + 2,
    )
    treatment = (source["variant"] == "treatment").to_numpy(dtype=float)

    rng = np.random.default_rng(random_seed + 3)
    monthly_factor = (
        float(config["experiment_days"])
        / float(config["pre_period_days"])
    )

    prior_transactions = safe_positive(
        source["prior_fx_transactions"],
        fallback=2.0,
    )
    transaction_lambda = np.maximum(
        prior_transactions * monthly_factor,
        0.05,
    )
    transaction_lambda *= (
        1.0
        + treatment
        * float(
            config["treatment"]["transaction_frequency_lift_relative"]
        )
    )
    outcome_transactions = rng.poisson(transaction_lambda)

    prior_avg_ticket = safe_positive(
        source["prior_avg_ticket_usd"],
        fallback=1000.0,
    )
    ticket_multiplier = mean_one_lognormal(
        rng,
        len(source),
        float(config["simulation"]["ticket_noise_sigma"]),
    )
    ticket_multiplier *= (
        1.0
        + treatment
        * float(config["treatment"]["ticket_size_lift_relative"])
    )
    outcome_turnover = (
        outcome_transactions
        * prior_avg_ticket
        * ticket_multiplier
    )

    prior_turnover = safe_positive(
        source["prior_fx_turnover_usd"],
        fallback=1.0,
    )
    prior_revenue = safe_positive(
        source["prior_fx_revenue_usd"],
        fallback=0.01,
    )
    revenue_rate = np.divide(
        prior_revenue,
        prior_turnover,
        out=np.zeros_like(prior_revenue),
        where=prior_turnover > 0,
    )
    revenue_rate *= (
        1.0
        - treatment
        * float(config["treatment"]["spread_discount_relative"])
    )
    revenue_multiplier = mean_one_lognormal(
        rng,
        len(source),
        float(config["simulation"]["revenue_noise_sigma"]),
    )
    outcome_revenue = (
        outcome_turnover
        * revenue_rate
        * revenue_multiplier
    )

    prior_spread = safe_positive(
        source["prior_avg_spread"],
        fallback=0.001,
    )
    effective_spread = prior_spread * (
        1.0
        - treatment
        * float(config["treatment"]["spread_discount_relative"])
    )

    result = source.copy()
    result.insert(0, "experiment_id", "EXP-01")
    result["outcome_fx_conversion"] = (
        outcome_transactions > 0
    ).astype(int)
    result["outcome_transaction_count"] = outcome_transactions
    result["outcome_turnover_usd"] = np.round(outcome_turnover, 2)
    result["outcome_revenue_usd"] = np.round(outcome_revenue, 2)
    result["outcome_revenue_per_transaction_usd"] = np.divide(
        result["outcome_revenue_usd"],
        outcome_transactions,
        out=np.zeros(len(result), dtype=float),
        where=outcome_transactions > 0,
    )
    result["outcome_effective_spread"] = effective_spread
    return result


def logistic_shift(
    baseline_probability: np.ndarray,
    score: np.ndarray,
) -> np.ndarray:
    epsilon = 1e-6
    clipped = np.clip(baseline_probability, epsilon, 1.0 - epsilon)
    log_odds = np.log(clipped / (1.0 - clipped))
    shifted = log_odds + score
    return 1.0 / (1.0 + np.exp(-shifted))


def lognormal_with_mean(
    rng: np.random.Generator,
    means: np.ndarray,
    sigma: float,
) -> np.ndarray:
    safe_means = np.maximum(means, 0.01)
    mu = np.log(safe_means) - 0.5 * sigma**2
    return rng.lognormal(mu, sigma)


def simulate_dcd_cross_sell(
    source: pd.DataFrame,
    config: dict[str, Any],
    project: dict[str, Any],
    random_seed: int,
) -> pd.DataFrame:
    numeric_columns = [
        "customer_id",
        "prior_fx_transactions",
        "prior_fx_turnover_usd",
        "prior_fx_revenue_usd",
        "historical_dcd_conversion_rate",
        "average_dcd_fee_usd",
    ]
    source = normalise_numeric(source.copy(), numeric_columns)
    source = cap_population(source, int(config["sample_cap"]), random_seed + 1)

    source["variant"] = stratified_assignment(
        source,
        list(config["strata"]),
        float(project["expected_treatment_share"]),
        random_seed + 2,
    )
    treatment = (source["variant"] == "treatment").to_numpy(dtype=float)

    rng = np.random.default_rng(random_seed + 3)
    baseline = np.clip(
        source["historical_dcd_conversion_rate"].to_numpy(dtype=float),
        0.005,
        0.80,
    )
    log_revenue = np.log1p(
        np.maximum(
            source["prior_fx_revenue_usd"].to_numpy(dtype=float),
            0.0,
        )
    )
    score = (
        log_revenue - np.mean(log_revenue)
    ) / max(float(np.std(log_revenue)), 1e-9)
    score *= float(
        config["simulation"]["propensity_log_revenue_weight"]
    )
    probability = logistic_shift(baseline, score)
    probability += (
        treatment
        * float(config["treatment"]["conversion_lift_absolute"])
    )
    probability = np.clip(probability, 0.0, 0.95)

    converted = rng.binomial(1, probability)
    average_fee = safe_positive(
        source["average_dcd_fee_usd"],
        fallback=1000.0,
    )
    fee_if_converted = lognormal_with_mean(
        rng,
        average_fee,
        float(config["simulation"]["fee_log_sigma"]),
    )
    fee_revenue = converted * fee_if_converted

    prior_fx_revenue = np.maximum(
        source["prior_fx_revenue_usd"].to_numpy(dtype=float),
        0.0,
    )
    expected_fx_revenue = (
        prior_fx_revenue
        * float(config["experiment_days"])
        / float(config["pre_period_days"])
    )
    fx_revenue_multiplier = mean_one_lognormal(
        rng,
        len(source),
        float(config["simulation"]["fx_revenue_noise_sigma"]),
    )
    outcome_fx_revenue = expected_fx_revenue * fx_revenue_multiplier

    result = source.copy()
    result.insert(0, "experiment_id", "EXP-02")
    result["outcome_dcd_conversion"] = converted
    result["outcome_dcd_contract_count"] = converted
    result["outcome_fee_revenue_usd"] = np.round(fee_revenue, 2)
    result["outcome_fx_revenue_usd"] = np.round(outcome_fx_revenue, 2)
    return result


def simulate_digital_migration(
    source: pd.DataFrame,
    config: dict[str, Any],
    project: dict[str, Any],
    random_seed: int,
) -> pd.DataFrame:
    numeric_columns = [
        "customer_id",
        "prior_fx_transactions",
        "prior_assisted_transactions",
        "prior_digital_transactions",
        "prior_digital_share",
        "prior_fx_revenue_usd",
        "historical_digital_adoption_rate",
    ]
    source = normalise_numeric(source.copy(), numeric_columns)
    source = cap_population(source, int(config["sample_cap"]), random_seed + 1)

    source["variant"] = stratified_assignment(
        source,
        list(config["strata"]),
        float(project["expected_treatment_share"]),
        random_seed + 2,
    )
    treatment = (source["variant"] == "treatment").to_numpy(dtype=float)

    rng = np.random.default_rng(random_seed + 3)
    baseline = np.clip(
        source["historical_digital_adoption_rate"].to_numpy(dtype=float),
        0.01,
        0.90,
    )
    log_transactions = np.log1p(
        np.maximum(
            source["prior_fx_transactions"].to_numpy(dtype=float),
            0.0,
        )
    )
    score = (
        log_transactions - np.mean(log_transactions)
    ) / max(float(np.std(log_transactions)), 1e-9)
    score *= float(
        config["simulation"]["propensity_log_transactions_weight"]
    )
    probability = logistic_shift(baseline, score)
    probability += (
        treatment
        * float(
            config["treatment"]["digital_adoption_lift_absolute"]
        )
    )
    probability = np.clip(probability, 0.0, 0.98)
    intended_adoption = rng.binomial(1, probability)

    prior_transactions = safe_positive(
        source["prior_fx_transactions"],
        fallback=2.0,
    )
    transaction_lambda = np.maximum(
        prior_transactions
        * float(config["experiment_days"])
        / float(config["pre_period_days"]),
        0.05,
    )
    transaction_lambda *= (
        1.0
        + treatment
        * float(
            config["treatment"]["transaction_frequency_lift_relative"]
        )
    )
    outcome_transactions = rng.poisson(transaction_lambda)

    prior_share = np.clip(
        source["prior_digital_share"].to_numpy(dtype=float),
        0.0,
        1.0,
    )
    digital_probability = np.where(
        intended_adoption == 1,
        np.maximum(0.55, prior_share + 0.45),
        0.0,
    )
    digital_probability = np.clip(digital_probability, 0.0, 1.0)
    digital_transactions = rng.binomial(
        outcome_transactions,
        digital_probability,
    )
    force_first_digital = (
        (intended_adoption == 1)
        & (outcome_transactions > 0)
        & (digital_transactions == 0)
    )
    digital_transactions = np.where(
        force_first_digital,
        1,
        digital_transactions,
    )
    adopted = (digital_transactions > 0).astype(int)

    prior_revenue = np.maximum(
        source["prior_fx_revenue_usd"].to_numpy(dtype=float),
        0.0,
    )
    prior_revenue_per_transaction = np.divide(
        prior_revenue,
        prior_transactions,
        out=np.zeros_like(prior_revenue),
        where=prior_transactions > 0,
    )
    revenue_multiplier = mean_one_lognormal(
        rng,
        len(source),
        float(config["simulation"]["revenue_noise_sigma"]),
    )
    outcome_revenue = (
        outcome_transactions
        * prior_revenue_per_transaction
        * revenue_multiplier
    )

    result = source.copy()
    result.insert(0, "experiment_id", "EXP-03")
    result["outcome_digital_adoption"] = adopted
    result["outcome_transaction_count"] = outcome_transactions
    result["outcome_digital_transaction_count"] = digital_transactions
    result["outcome_digital_share"] = np.divide(
        digital_transactions,
        outcome_transactions,
        out=np.zeros(len(result), dtype=float),
        where=outcome_transactions > 0,
    )
    result["outcome_revenue_usd"] = np.round(outcome_revenue, 2)
    return result


SIMULATORS = {
    "EXP-01": simulate_fx_spread,
    "EXP-02": simulate_dcd_cross_sell,
    "EXP-03": simulate_digital_migration,
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export eligible populations and build synthetic banking "
            "A/B experiment datasets."
        )
    )
    parser.add_argument(
        "--experiment",
        choices=sorted(SIMULATORS),
        help="Build one experiment only, for example EXP-01.",
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Reuse existing data/source CSV files.",
    )
    parser.add_argument(
        "--skip-connection-test",
        action="store_true",
        help="Skip only the preliminary connection test.",
    )
    return parser.parse_args()


def validate_source_frame(
    experiment_id: str,
    frame: pd.DataFrame,
) -> None:
    if frame.empty:
        raise SystemExit(
            f"{experiment_id}: eligible population is empty."
        )
    missing_columns = sorted(
        REQUIRED_SOURCE_COLUMNS[experiment_id] - set(frame.columns)
    )
    if missing_columns:
        raise SystemExit(
            f"{experiment_id}: source population is missing columns: "
            + ", ".join(missing_columns)
        )
    if frame["customer_id"].isna().any():
        raise SystemExit(
            f"{experiment_id}: source population contains null customer_id values."
        )
    if frame["customer_id"].duplicated().any():
        duplicate_count = int(frame["customer_id"].duplicated().sum())
        raise SystemExit(
            f"{experiment_id}: source population contains "
            f"{duplicate_count} duplicate customer rows."
        )


def main() -> int:
    load_env_file(ENV_FILE)
    args = parse_arguments()
    config = load_config()
    project = config["project"]

    selected_ids = (
        [args.experiment]
        if args.experiment
        else sorted(config["experiments"])
    )

    if not args.skip_extract:
        require_psql()
        print_connection_target()

        if not args.skip_connection_test:
            test_connection()

        print("Validating upstream objects...")
        validate_sources()

    manifest_rows: list[dict[str, Any]] = []

    for experiment_id in selected_ids:
        experiment = config["experiments"][experiment_id]
        source_path = ROOT / experiment["source_file"]
        output_path = ROOT / experiment["output_file"]
        sql_path = ROOT / experiment["sql_file"]

        if not args.skip_extract:
            print(
                f"Exporting {experiment_id}: "
                f"{sql_path.relative_to(ROOT)} "
                f"-> {source_path.relative_to(ROOT)}",
                flush=True,
            )
            export_sql(sql_path, source_path)

        if not source_path.exists():
            raise SystemExit(
                f"Source population does not exist: "
                f"{source_path.relative_to(ROOT)}"
            )

        source = pd.read_csv(source_path)
        validate_source_frame(experiment_id, source)

        experiment_seed = int(experiment["random_seed"])
        built = SIMULATORS[experiment_id](
            source,
            experiment,
            project,
            experiment_seed,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        built.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        counts = built["variant"].value_counts()
        manifest_rows.append(
            {
                "experiment_id": experiment_id,
                "experiment_name": experiment["name"],
                "source_population_rows": len(source),
                "experiment_rows": len(built),
                "control_rows": int(counts.get("control", 0)),
                "treatment_rows": int(counts.get("treatment", 0)),
                "random_seed": experiment_seed,
                "output_file": str(output_path.relative_to(ROOT)),
            }
        )
        print(
            f"Built {experiment_id}: rows={len(built):,}, "
            f"control={int(counts.get('control', 0)):,}, "
            f"treatment={int(counts.get('treatment', 0)):,}",
            flush=True,
        )

    manifest = pd.DataFrame(manifest_rows)
    manifest_path = ROOT / "results" / "experiment_manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(
        manifest_path,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        "\nSynthetic Banking A/B experiment data completed successfully."
    )
    print(f"Manifest: {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(
            f"\nExperiment data build failed with exit code "
            f"{exc.returncode}.",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode)
    except KeyboardInterrupt:
        print("\nExecution interrupted by user.", file=sys.stderr)
        raise SystemExit(130)
