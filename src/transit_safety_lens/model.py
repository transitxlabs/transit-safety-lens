"""Exposure-offset Poisson model with an empirical gamma hierarchy by mode and size."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import beta, betaprime, nbinom


@dataclass(frozen=True)
class Prior:
    shape: float
    rate: float
    groups: int
    status: str


def fit_prior(counts: np.ndarray, exposure: np.ndarray) -> Prior:
    """Fit gamma mixing parameters to training-only agency totals via marginal likelihood.

    Exposure is in millions of vehicle revenue miles. With too few peers, use a
    weak proper prior centred on the training aggregate and declare that fallback.
    """
    if len(counts) == 0 or np.any(exposure <= 0) or np.any(counts < 0):
        raise ValueError("Prior requires non-negative counts and positive exposure")
    mean = max(float(counts.sum()) / float(exposure.sum()), 1e-8)
    if len(counts) < 5 or counts.sum() == 0:
        return Prior(0.5, 0.5 / mean, len(counts), "weak_training_centred_fallback")

    def objective(log_parameters: np.ndarray) -> float:
        a, b = np.exp(log_parameters)
        logp = (gammaln(counts + a) - gammaln(a) - gammaln(counts + 1)
                + a * np.log(b / (b + exposure))
                + counts * np.log(exposure / (b + exposure)))
        return -float(logp.sum())

    fit = minimize(objective, np.log([1.0, 1.0 / mean]), method="L-BFGS-B",
                   bounds=[(-9, 12), (-16, 20)])
    a, b = np.exp(fit.x)
    if not fit.success:
        return Prior(0.5, 0.5 / mean, len(counts), "optimisation_fallback")
    return Prior(float(a), float(b), len(counts), "marginal_likelihood")


def compare(y0: float, e0: float, y1: float, e1: float, prior: Prior) -> dict[str, float]:
    if min(e0, e1) <= 0 or min(y0, y1) < 0:
        raise ValueError("Invalid comparison")
    a0, b0 = prior.shape + y0, prior.rate + e0
    a1, b1 = prior.shape + y1, prior.rate + e1
    # Independent gamma posteriors conditional on the training hyperparameters.
    scale = b0 / b1
    lower, median, upper = betaprime.ppf([0.025, 0.5, 0.975], a1, a0, scale=scale)
    p_up = float(1 - beta.cdf(b1 / (b0 + b1), a1, a0))
    return {"rate0": a0 / b0, "rate1": a1 / b1,
            "ratio_lower": float(lower), "ratio_median": float(median),
            "ratio_upper": float(upper), "p_increase": p_up,
            "false_sign_probability": min(p_up, 1 - p_up)}


def select_flags(errors: np.ndarray, interval_excludes_one: np.ndarray,
                 q: float = 0.05) -> np.ndarray:
    """Control conditional posterior expected false-sign proportion in the selected set.

    This is a Bayesian model-conditional criterion, not a distribution-free guarantee.
    Also require that each selected 95% ratio interval excludes one.
    """
    if not 0 < q < 1 or np.any(~np.isfinite(errors)) or np.any((errors < 0) | (errors > 1)):
        raise ValueError("Invalid error probabilities or target")
    eligible = np.flatnonzero(interval_excludes_one)
    order = eligible[np.argsort(errors[eligible], kind="stable")]
    means = np.cumsum(errors[order]) / np.arange(1, len(order) + 1)
    allowed = np.flatnonzero(means <= q)
    flags = np.zeros(len(errors), dtype=bool)
    if len(allowed):
        flags[order[:allowed[-1] + 1]] = True
    return flags


def predictive_interval(y: float, exposure: float, future_exposure: float,
                        prior: Prior) -> tuple[float, float]:
    shape = prior.shape + y
    rate = prior.rate + exposure
    low, high = nbinom.ppf([0.025, 0.975], shape, rate / (rate + future_exposure))
    return float(low), float(high)
