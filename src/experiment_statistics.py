from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.power import NormalIndPower, TTestIndPower
from statsmodels.stats.proportion import (
    confint_proportions_2indep,
    proportion_effectsize,
    test_proportions_2indep,
)


@dataclass(frozen=True)
class EffectResult:
    metric: str
    control: float
    treatment: float
    absolute_effect: float
    relative_lift: float
    ci_low: float
    ci_high: float
    p_value: float
    method: str

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)


def _clean_numeric(values: Iterable[float]) -> np.ndarray:
    array = np.asarray(list(values), dtype=float)
    return array[np.isfinite(array)]


def relative_lift(treatment: float, control: float) -> float:
    if control == 0:
        return np.nan
    return treatment / control - 1.0


def srm_test(
    variants: pd.Series,
    expected_treatment_share: float = 0.5,
) -> dict[str, float | int]:
    counts = variants.value_counts()
    control = int(counts.get("control", 0))
    treatment = int(counts.get("treatment", 0))
    total = control + treatment

    if total == 0:
        raise ValueError("SRM test requires at least one assigned customer.")

    expected = np.array(
        [
            total * (1.0 - expected_treatment_share),
            total * expected_treatment_share,
        ]
    )
    result = stats.chisquare([control, treatment], f_exp=expected)

    return {
        "control_n": control,
        "treatment_n": treatment,
        "treatment_share": treatment / total,
        "chi_square": float(result.statistic),
        "p_value": float(result.pvalue),
    }


def standardised_mean_difference(
    control: Iterable[float],
    treatment: Iterable[float],
) -> float:
    control_array = _clean_numeric(control)
    treatment_array = _clean_numeric(treatment)

    if control_array.size < 2 or treatment_array.size < 2:
        return np.nan

    pooled_variance = (
        np.var(control_array, ddof=1)
        + np.var(treatment_array, ddof=1)
    ) / 2.0

    if pooled_variance <= 0:
        return 0.0

    return float(
        (np.mean(treatment_array) - np.mean(control_array))
        / np.sqrt(pooled_variance)
    )


def balance_table(
    data: pd.DataFrame,
    numeric_columns: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    control = data.loc[data["variant"] == "control"]
    treatment = data.loc[data["variant"] == "treatment"]

    for column in numeric_columns:
        rows.append(
            {
                "covariate": column,
                "control_mean": control[column].mean(),
                "treatment_mean": treatment[column].mean(),
                "standardised_mean_difference": standardised_mean_difference(
                    control[column],
                    treatment[column],
                ),
            }
        )

    return pd.DataFrame(rows)


def proportion_effect(
    data: pd.DataFrame,
    metric: str,
    alpha: float = 0.05,
) -> EffectResult:
    grouped = data.groupby("variant")[metric].agg(["sum", "count"])
    control_success = int(grouped.loc["control", "sum"])
    control_n = int(grouped.loc["control", "count"])
    treatment_success = int(grouped.loc["treatment", "sum"])
    treatment_n = int(grouped.loc["treatment", "count"])

    control_rate = control_success / control_n
    treatment_rate = treatment_success / treatment_n

    test = test_proportions_2indep(
        count1=treatment_success,
        nobs1=treatment_n,
        count2=control_success,
        nobs2=control_n,
        compare="diff",
        method="score",
        correction=False,
        return_results=True,
    )
    ci_low, ci_high = confint_proportions_2indep(
        count1=treatment_success,
        nobs1=treatment_n,
        count2=control_success,
        nobs2=control_n,
        compare="diff",
        method="newcomb",
        alpha=alpha,
        correction=False,
    )

    return EffectResult(
        metric=metric,
        control=float(control_rate),
        treatment=float(treatment_rate),
        absolute_effect=float(treatment_rate - control_rate),
        relative_lift=float(relative_lift(treatment_rate, control_rate)),
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        p_value=float(test.pvalue),
        method="two-sample score test for proportions",
    )


def welch_mean_effect(
    data: pd.DataFrame,
    metric: str,
    alpha: float = 0.05,
) -> EffectResult:
    control = _clean_numeric(data.loc[data["variant"] == "control", metric])
    treatment = _clean_numeric(data.loc[data["variant"] == "treatment", metric])

    result = stats.ttest_ind(
        treatment,
        control,
        equal_var=False,
        nan_policy="omit",
    )

    treatment_mean = float(np.mean(treatment))
    control_mean = float(np.mean(control))
    difference = treatment_mean - control_mean

    treatment_variance = np.var(treatment, ddof=1)
    control_variance = np.var(control, ddof=1)
    treatment_n = treatment.size
    control_n = control.size

    standard_error = np.sqrt(
        treatment_variance / treatment_n
        + control_variance / control_n
    )
    numerator = (
        treatment_variance / treatment_n
        + control_variance / control_n
    ) ** 2
    denominator = (
        (treatment_variance / treatment_n) ** 2 / (treatment_n - 1)
        + (control_variance / control_n) ** 2 / (control_n - 1)
    )
    degrees_freedom = numerator / denominator
    critical_value = stats.t.ppf(1.0 - alpha / 2.0, degrees_freedom)

    return EffectResult(
        metric=metric,
        control=control_mean,
        treatment=treatment_mean,
        absolute_effect=float(difference),
        relative_lift=float(relative_lift(treatment_mean, control_mean)),
        ci_low=float(difference - critical_value * standard_error),
        ci_high=float(difference + critical_value * standard_error),
        p_value=float(result.pvalue),
        method="Welch unequal-variance t-test",
    )


def bootstrap_mean_difference(
    data: pd.DataFrame,
    metric: str,
    confidence_level: float = 0.95,
    n_resamples: int = 2000,
    random_seed: int = 20260729,
) -> tuple[float, float]:
    control = _clean_numeric(data.loc[data["variant"] == "control", metric])
    treatment = _clean_numeric(data.loc[data["variant"] == "treatment", metric])

    def statistic(
        treatment_sample: np.ndarray,
        control_sample: np.ndarray,
        axis: int = -1,
    ) -> np.ndarray:
        return (
            np.mean(treatment_sample, axis=axis)
            - np.mean(control_sample, axis=axis)
        )

    result = stats.bootstrap(
        (treatment, control),
        statistic,
        vectorized=True,
        paired=False,
        confidence_level=confidence_level,
        n_resamples=n_resamples,
        method="basic",
        batch=50,
        rng=np.random.default_rng(random_seed),
    )
    return (
        float(result.confidence_interval.low),
        float(result.confidence_interval.high),
    )


def cuped_adjust(
    outcome: pd.Series,
    preperiod_covariate: pd.Series,
) -> tuple[pd.Series, float, float]:
    outcome_array = outcome.astype(float).to_numpy()
    covariate_array = preperiod_covariate.astype(float).to_numpy()

    covariate_variance = np.var(covariate_array, ddof=1)
    if covariate_variance <= 0:
        return outcome.astype(float).copy(), 0.0, 0.0

    theta = (
        np.cov(outcome_array, covariate_array, ddof=1)[0, 1]
        / covariate_variance
    )
    adjusted = outcome_array - theta * (
        covariate_array - np.mean(covariate_array)
    )

    raw_variance = np.var(outcome_array, ddof=1)
    adjusted_variance = np.var(adjusted, ddof=1)
    variance_reduction = (
        1.0 - adjusted_variance / raw_variance
        if raw_variance > 0
        else 0.0
    )

    return (
        pd.Series(adjusted, index=outcome.index, name=f"{outcome.name}_cuped"),
        float(theta),
        float(variance_reduction),
    )


def holm_adjust(p_values: Iterable[float], alpha: float = 0.05) -> np.ndarray:
    values = np.asarray(list(p_values), dtype=float)
    _, adjusted, _, _ = multipletests(
        values,
        alpha=alpha,
        method="holm",
    )
    return adjusted


def required_sample_size_proportion(
    baseline_rate: float,
    absolute_mde: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    treatment_rate = baseline_rate + absolute_mde
    if not 0 < baseline_rate < 1:
        raise ValueError("Baseline rate must be between zero and one.")
    if not 0 < treatment_rate < 1:
        raise ValueError("Baseline plus MDE must be between zero and one.")

    effect_size = abs(proportion_effectsize(treatment_rate, baseline_rate))
    n_per_arm = NormalIndPower().solve_power(
        effect_size=effect_size,
        power=power,
        alpha=alpha,
        ratio=1.0,
        alternative="two-sided",
    )
    return int(ceil(n_per_arm))


def required_sample_size_continuous(
    standardised_mde: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    n_per_arm = TTestIndPower().solve_power(
        effect_size=standardised_mde,
        power=power,
        alpha=alpha,
        ratio=1.0,
        alternative="two-sided",
    )
    return int(ceil(n_per_arm))
