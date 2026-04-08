"""
sensitivity_analysis.py
========================
CRE Sensitivity Analysis Module

Performs one-way and two-way sensitivity analysis on CRE deal metrics,
covering the five key risk dimensions used in institutional underwriting:

  1. Rent Growth Rate     — revenue driver sensitivity
  2. Vacancy Rate         — occupancy / absorption risk
  3. Interest Rate        — refinance / debt cost risk
  4. Cap Rate             — valuation / exit risk
  5. OpEx Growth Rate     — expense inflation risk

Output includes:
  - One-way sensitivity tables (each variable vs NOI, DSCR, LTV, Value, Max Loan)
  - Two-way sensitivity tables (e.g. rent growth × vacancy)
  - Heat map data arrays (ready for Excel conditional formatting)
  - Risk flags for DSCR < 1.0x and LTV > 80%

CRE Risk Framework
------------------
  DSCR < 1.0x  → Loan in default territory; lender likely triggers reserves/cure
  DSCR < 1.20x → Below most lender minimums; refinancing risk high
  LTV > 75%    → Above bank/life company threshold
  LTV > 80%    → Above agency ceiling; technically non-conforming
"""

from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass, field
from typing import Any

# Allow running as standalone script or importing as a module
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from models.cre_deal_model import (
    CREDealModel,
    OperatingExpenses,
    MarketAssumptions,
)
from models.debt_sizing_engine import DebtSizingEngine, DEFAULT_LENDERS


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SensitivityConfig:
    """
    Defines the sensitivity ranges and step sizes for each variable.

    All ranges are expressed as absolute changes from the base case
    (e.g. rent_growth_bps_range=[-200, -100, 0, 100, 200] means
    the rent growth rate is shifted by those many basis points).
    """
    # Rent growth sensitivity (basis points from base)
    rent_growth_bps: list[int] = field(
        default_factory=lambda: [-200, -100, -50, 0, 50, 100, 200]
    )
    # Vacancy rate — absolute levels (not shifts)
    vacancy_rates: list[float] = field(
        default_factory=lambda: [0.03, 0.05, 0.07, 0.10, 0.12, 0.15]
    )
    # Interest rate shifts (basis points from base)
    interest_rate_bps: list[int] = field(
        default_factory=lambda: [-150, -100, -50, 0, 50, 100, 150]
    )
    # Cap rate shifts (basis points from base)
    cap_rate_bps: list[int] = field(
        default_factory=lambda: [-100, -50, -25, 0, 25, 50, 100]
    )
    # OpEx growth rate shifts (basis points from base)
    opex_growth_bps: list[int] = field(
        default_factory=lambda: [-200, -100, 0, 100, 200]
    )
    # Risk thresholds
    dscr_warning_threshold: float = 1.20  # Below this → yellow flag
    dscr_breach_threshold: float = 1.00   # Below this → red flag (default territory)
    ltv_warning_threshold: float = 0.75
    ltv_breach_threshold: float = 0.80


# ---------------------------------------------------------------------------
# Result Structures
# ---------------------------------------------------------------------------

@dataclass
class SensitivityResult:
    """Single data point in a sensitivity table."""
    variable_name: str
    variable_value: float
    variable_label: str
    noi: float
    property_value: float
    max_loan: float
    actual_dscr: float
    actual_ltv: float
    actual_debt_yield: float
    annual_debt_service: float
    dscr_flag: str = ""   # "", "WARNING", "BREACH"
    ltv_flag: str = ""    # "", "WARNING", "BREACH"

    @property
    def is_distressed(self) -> bool:
        return self.dscr_flag == "BREACH" or self.ltv_flag == "BREACH"


# ---------------------------------------------------------------------------
# Core Engine
# ---------------------------------------------------------------------------

class SensitivityAnalysis:
    """
    CRE sensitivity analysis engine.

    Parameters
    ----------
    deal_model : CREDealModel
        Fully configured base-case deal model.
    base_cap_rate : float
        Going-in cap rate for direct capitalization valuation (e.g. 0.055).
    base_interest_rate : float
        Base interest rate used for debt sizing (e.g. 0.0575).
    base_opex_growth_rate : float
        Base operating expense growth rate (e.g. 0.03 = 3%).
    config : SensitivityConfig, optional
        Sensitivity ranges and risk thresholds.
    """

    def __init__(
        self,
        deal_model: CREDealModel,
        base_cap_rate: float = 0.055,
        base_interest_rate: float = 0.0575,
        base_opex_growth_rate: float = 0.03,
        config: SensitivityConfig | None = None,
    ) -> None:
        self.base_model = deal_model
        self.base_cap_rate = base_cap_rate
        self.base_interest_rate = base_interest_rate
        self.base_opex_growth_rate = base_opex_growth_rate
        self.config = config or SensitivityConfig()

    # ------------------------------------------------------------------
    # Base Case Metrics
    # ------------------------------------------------------------------

    def base_metrics(self) -> dict[str, Any]:
        """
        Compute base-case underwriting metrics.

        Returns
        -------
        dict
            Base NOI, value, debt sizing metrics.
        """
        noi = self.base_model.net_operating_income()
        value = self.base_model.direct_cap_value(self.base_cap_rate)

        # Use default lenders but scale interest rate
        lenders = _scale_lender_rates(DEFAULT_LENDERS, 0)  # no shift = base
        engine = DebtSizingEngine(noi=noi, property_value=value, lenders=lenders)
        rec = engine.recommend()

        return {
            "noi": noi,
            "property_value": value,
            "cap_rate": self.base_cap_rate,
            "max_loan": rec["max_loan"],
            "annual_debt_service": rec["annual_debt_service"],
            "actual_dscr": rec["actual_dscr"],
            "actual_ltv": rec["actual_ltv"],
            "actual_debt_yield": rec["actual_debt_yield"],
            "recommended_lender": rec["lender"],
            "egi": self.base_model.effective_gross_income(),
            "total_opex": self.base_model.total_operating_expenses(),
            "occupancy": self.base_model.physical_occupancy,
            "total_sf": self.base_model.total_sf,
        }

    def _flag(
        self,
        dscr_val: float,
        ltv_val: float,
    ) -> tuple[str, str]:
        """Return (dscr_flag, ltv_flag) strings for risk annotation."""
        cfg = self.config
        dscr_flag = ""
        if dscr_val < cfg.dscr_breach_threshold:
            dscr_flag = "BREACH"
        elif dscr_val < cfg.dscr_warning_threshold:
            dscr_flag = "WARNING"

        ltv_flag = ""
        if ltv_val > cfg.ltv_breach_threshold:
            ltv_flag = "BREACH"
        elif ltv_val > cfg.ltv_warning_threshold:
            ltv_flag = "WARNING"

        return dscr_flag, ltv_flag

    def _run_scenario(
        self,
        noi: float,
        cap_rate: float,
        interest_rate_shift_bps: int = 0,
    ) -> dict[str, Any]:
        """
        Run a single scenario (NOI + cap rate + interest rate shift).

        Returns debt sizing results using all lenders.
        """
        if noi <= 0 or cap_rate <= 0:
            return {
                "max_loan": 0.0,
                "annual_debt_service": 0.0,
                "actual_dscr": 0.0,
                "actual_ltv": 0.0,
                "actual_debt_yield": 0.0,
                "property_value": 0.0,
            }
        value = noi / cap_rate
        lenders = _scale_lender_rates(DEFAULT_LENDERS, interest_rate_shift_bps)
        engine = DebtSizingEngine(noi=noi, property_value=value, lenders=lenders)
        rec = engine.recommend()
        return {
            "max_loan": rec["max_loan"],
            "annual_debt_service": rec["annual_debt_service"],
            "actual_dscr": rec["actual_dscr"],
            "actual_ltv": rec["actual_ltv"],
            "actual_debt_yield": rec["actual_debt_yield"],
            "property_value": value,
        }

    # ------------------------------------------------------------------
    # One-Way Sensitivities
    # ------------------------------------------------------------------

    def rent_growth_sensitivity(self) -> list[SensitivityResult]:
        """
        One-way sensitivity: rent growth rate.

        Shifts the annual escalation rate on ALL units by the specified bps
        and recalculates NOI, value, and debt metrics.

        Returns
        -------
        list[SensitivityResult]
        """
        base_noi = self.base_model.net_operating_income()
        results = []

        for shift_bps in self.config.rent_growth_bps:
            shift = shift_bps / 10_000  # convert bps to decimal

            # Apply escalation shift to all units
            modified_model = _clone_model_with_escalation_shift(self.base_model, shift)
            noi = modified_model.net_operating_income()
            scenario = self._run_scenario(noi, self.base_cap_rate)

            dscr_flag, ltv_flag = self._flag(scenario["actual_dscr"], scenario["actual_ltv"])

            results.append(SensitivityResult(
                variable_name="Rent Growth Rate",
                variable_value=shift_bps,
                variable_label=f"{'+' if shift_bps >= 0 else ''}{shift_bps}bps",
                noi=noi,
                property_value=scenario["property_value"],
                max_loan=scenario["max_loan"],
                actual_dscr=scenario["actual_dscr"],
                actual_ltv=scenario["actual_ltv"],
                actual_debt_yield=scenario["actual_debt_yield"],
                annual_debt_service=scenario["annual_debt_service"],
                dscr_flag=dscr_flag,
                ltv_flag=ltv_flag,
            ))

        return results

    def vacancy_sensitivity(self) -> list[SensitivityResult]:
        """
        One-way sensitivity: vacancy rate.

        Tests absolute vacancy levels from 3% to 15%.

        Returns
        -------
        list[SensitivityResult]
        """
        results = []

        for vac_rate in self.config.vacancy_rates:
            # Temporarily override vacancy in market assumptions
            modified_model = _clone_model_with_vacancy(self.base_model, vac_rate)
            noi = modified_model.net_operating_income()
            scenario = self._run_scenario(noi, self.base_cap_rate)

            dscr_flag, ltv_flag = self._flag(scenario["actual_dscr"], scenario["actual_ltv"])

            results.append(SensitivityResult(
                variable_name="Vacancy Rate",
                variable_value=vac_rate,
                variable_label=f"{vac_rate:.0%}",
                noi=noi,
                property_value=scenario["property_value"],
                max_loan=scenario["max_loan"],
                actual_dscr=scenario["actual_dscr"],
                actual_ltv=scenario["actual_ltv"],
                actual_debt_yield=scenario["actual_debt_yield"],
                annual_debt_service=scenario["annual_debt_service"],
                dscr_flag=dscr_flag,
                ltv_flag=ltv_flag,
            ))

        return results

    def interest_rate_sensitivity(self) -> list[SensitivityResult]:
        """
        One-way sensitivity: interest rate (applied uniformly to all lenders).

        Returns
        -------
        list[SensitivityResult]
        """
        base_noi = self.base_model.net_operating_income()
        base_value = self.base_model.direct_cap_value(self.base_cap_rate)
        results = []

        for shift_bps in self.config.interest_rate_bps:
            scenario = self._run_scenario(base_noi, self.base_cap_rate, shift_bps)
            dscr_flag, ltv_flag = self._flag(scenario["actual_dscr"], scenario["actual_ltv"])

            results.append(SensitivityResult(
                variable_name="Interest Rate",
                variable_value=shift_bps,
                variable_label=f"{'+' if shift_bps >= 0 else ''}{shift_bps}bps",
                noi=base_noi,
                property_value=base_value,
                max_loan=scenario["max_loan"],
                actual_dscr=scenario["actual_dscr"],
                actual_ltv=scenario["actual_ltv"],
                actual_debt_yield=scenario["actual_debt_yield"],
                annual_debt_service=scenario["annual_debt_service"],
                dscr_flag=dscr_flag,
                ltv_flag=ltv_flag,
            ))

        return results

    def cap_rate_sensitivity(self) -> list[SensitivityResult]:
        """
        One-way sensitivity: cap rate (affects property value, LTV, and debt yield).

        Returns
        -------
        list[SensitivityResult]
        """
        base_noi = self.base_model.net_operating_income()
        results = []

        for shift_bps in self.config.cap_rate_bps:
            shifted_cap = self.base_cap_rate + shift_bps / 10_000
            if shifted_cap <= 0.001:
                continue  # Skip degenerate cases
            scenario = self._run_scenario(base_noi, shifted_cap)
            dscr_flag, ltv_flag = self._flag(scenario["actual_dscr"], scenario["actual_ltv"])

            results.append(SensitivityResult(
                variable_name="Cap Rate",
                variable_value=shift_bps,
                variable_label=f"{'+' if shift_bps >= 0 else ''}{shift_bps}bps ({shifted_cap:.2%})",
                noi=base_noi,
                property_value=scenario["property_value"],
                max_loan=scenario["max_loan"],
                actual_dscr=scenario["actual_dscr"],
                actual_ltv=scenario["actual_ltv"],
                actual_debt_yield=scenario["actual_debt_yield"],
                annual_debt_service=scenario["annual_debt_service"],
                dscr_flag=dscr_flag,
                ltv_flag=ltv_flag,
            ))

        return results

    def opex_growth_sensitivity(self) -> list[SensitivityResult]:
        """
        One-way sensitivity: operating expense growth rate.

        Applies to the year-1 projected NOI with shifted opex growth.

        Returns
        -------
        list[SensitivityResult]
        """
        results = []

        for shift_bps in self.config.opex_growth_bps:
            shifted_growth = self.base_opex_growth_rate + shift_bps / 10_000

            # Project Year 1 NOI under shifted opex growth
            pf = self.base_model.five_year_pro_forma(opex_growth_rate=max(shifted_growth, 0.0))
            noi = pf[0]["noi"]  # Year 1 NOI
            scenario = self._run_scenario(noi, self.base_cap_rate)
            dscr_flag, ltv_flag = self._flag(scenario["actual_dscr"], scenario["actual_ltv"])

            results.append(SensitivityResult(
                variable_name="OpEx Growth Rate",
                variable_value=shift_bps,
                variable_label=f"{'+' if shift_bps >= 0 else ''}{shift_bps}bps",
                noi=noi,
                property_value=scenario["property_value"],
                max_loan=scenario["max_loan"],
                actual_dscr=scenario["actual_dscr"],
                actual_ltv=scenario["actual_ltv"],
                actual_debt_yield=scenario["actual_debt_yield"],
                annual_debt_service=scenario["annual_debt_service"],
                dscr_flag=dscr_flag,
                ltv_flag=ltv_flag,
            ))

        return results

    # ------------------------------------------------------------------
    # Two-Way Sensitivities
    # ------------------------------------------------------------------

    def two_way_rent_vacancy(
        self,
        metric: str = "noi",
    ) -> dict[str, Any]:
        """
        Two-way sensitivity: rent growth rate (rows) × vacancy rate (columns).

        Parameters
        ----------
        metric : str
            The metric to display in the table.  One of:
            "noi", "property_value", "max_loan", "actual_dscr", "actual_ltv".

        Returns
        -------
        dict
            - row_labels (list[str]): Rent growth labels
            - col_labels (list[str]): Vacancy rate labels
            - table (list[list[float]]): 2D values
            - flags (list[list[str]]): "" / "WARNING" / "BREACH" per cell
            - metric (str)
        """
        row_shifts = self.config.rent_growth_bps
        col_vacancies = self.config.vacancy_rates

        table = []
        flags = []

        for shift_bps in row_shifts:
            shift = shift_bps / 10_000
            modified_model = _clone_model_with_escalation_shift(self.base_model, shift)
            row = []
            row_flags = []
            for vac in col_vacancies:
                vac_model = _clone_model_with_vacancy(modified_model, vac)
                noi = vac_model.net_operating_income()
                scenario = self._run_scenario(noi, self.base_cap_rate)

                val = {
                    "noi": noi,
                    "property_value": scenario["property_value"],
                    "max_loan": scenario["max_loan"],
                    "actual_dscr": scenario["actual_dscr"],
                    "actual_ltv": scenario["actual_ltv"],
                }.get(metric, noi)

                d_flag, l_flag = self._flag(scenario["actual_dscr"], scenario["actual_ltv"])
                row.append(val)
                cell_flag = "BREACH" if "BREACH" in (d_flag, l_flag) else (
                    "WARNING" if "WARNING" in (d_flag, l_flag) else ""
                )
                row_flags.append(cell_flag)
            table.append(row)
            flags.append(row_flags)

        return {
            "row_labels": [f"{'+' if s >= 0 else ''}{s}bps" for s in row_shifts],
            "col_labels": [f"{v:.0%}" for v in col_vacancies],
            "row_axis": "Rent Growth Rate (bps)",
            "col_axis": "Vacancy Rate",
            "table": table,
            "flags": flags,
            "metric": metric,
        }

    def two_way_rate_caprate(
        self,
        metric: str = "max_loan",
    ) -> dict[str, Any]:
        """
        Two-way sensitivity: interest rate shift (rows) × cap rate shift (columns).

        Parameters
        ----------
        metric : str
            The metric to populate. One of:
            "noi", "property_value", "max_loan", "actual_dscr", "actual_ltv".

        Returns
        -------
        dict
            Same structure as two_way_rent_vacancy.
        """
        row_shifts = self.config.interest_rate_bps
        col_cap_shifts = self.config.cap_rate_bps

        base_noi = self.base_model.net_operating_income()
        table = []
        flags = []

        for rate_shift in row_shifts:
            row = []
            row_flags = []
            for cap_shift in col_cap_shifts:
                shifted_cap = self.base_cap_rate + cap_shift / 10_000
                if shifted_cap <= 0:
                    row.append(None)
                    row_flags.append("")
                    continue
                scenario = self._run_scenario(base_noi, shifted_cap, rate_shift)
                val = {
                    "noi": base_noi,
                    "property_value": scenario["property_value"],
                    "max_loan": scenario["max_loan"],
                    "actual_dscr": scenario["actual_dscr"],
                    "actual_ltv": scenario["actual_ltv"],
                }.get(metric, scenario["max_loan"])

                d_flag, l_flag = self._flag(scenario["actual_dscr"], scenario["actual_ltv"])
                row.append(val)
                cell_flag = "BREACH" if "BREACH" in (d_flag, l_flag) else (
                    "WARNING" if "WARNING" in (d_flag, l_flag) else ""
                )
                row_flags.append(cell_flag)
            table.append(row)
            flags.append(row_flags)

        return {
            "row_labels": [f"{'+' if s >= 0 else ''}{s}bps" for s in row_shifts],
            "col_labels": [f"{'+' if s >= 0 else ''}{s}bps ({self.base_cap_rate + s/10000:.2%})" for s in col_cap_shifts],
            "row_axis": "Interest Rate Shift (bps)",
            "col_axis": "Cap Rate Shift (bps)",
            "table": table,
            "flags": flags,
            "metric": metric,
        }

    # ------------------------------------------------------------------
    # Heat Map Data
    # ------------------------------------------------------------------

    def heatmap_data(self, two_way_result: dict) -> dict[str, Any]:
        """
        Convert a two-way result to heat map-ready data for Excel.

        Normalizes values to 0–1 scale for conditional formatting.
        Low = red (worst), High = green (best) for DSCR / NOI / value / loan.
        Inverted for LTV (lower LTV = better).

        Parameters
        ----------
        two_way_result : dict
            Output from two_way_rent_vacancy() or two_way_rate_caprate().

        Returns
        -------
        dict
            - normalized (list[list[float]]): 0–1 scale
            - min_val (float)
            - max_val (float)
            - inverted (bool): True if higher = worse (LTV)
        """
        metric = two_way_result["metric"]
        table = two_way_result["table"]

        flat = [v for row in table for v in row if v is not None]
        if not flat:
            return {"normalized": table, "min_val": 0, "max_val": 1, "inverted": False}

        min_val = min(flat)
        max_val = max(flat)
        inverted = metric in ("actual_ltv",)

        normalized = []
        for row in table:
            norm_row = []
            for v in row:
                if v is None:
                    norm_row.append(None)
                elif max_val == min_val:
                    norm_row.append(0.5)
                else:
                    raw = (v - min_val) / (max_val - min_val)
                    norm_row.append(1 - raw if inverted else raw)
            normalized.append(norm_row)

        return {
            "normalized": normalized,
            "min_val": min_val,
            "max_val": max_val,
            "inverted": inverted,
            "metric": metric,
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def full_sensitivity_report(self) -> dict[str, Any]:
        """
        Run all one-way and two-way sensitivities.

        Returns
        -------
        dict
            All sensitivity results keyed by analysis name.
        """
        return {
            "base_metrics": self.base_metrics(),
            "rent_growth": self.rent_growth_sensitivity(),
            "vacancy": self.vacancy_sensitivity(),
            "interest_rate": self.interest_rate_sensitivity(),
            "cap_rate": self.cap_rate_sensitivity(),
            "opex_growth": self.opex_growth_sensitivity(),
            "two_way_rent_vacancy_noi": self.two_way_rent_vacancy(metric="noi"),
            "two_way_rent_vacancy_dscr": self.two_way_rent_vacancy(metric="actual_dscr"),
            "two_way_rate_cap_loan": self.two_way_rate_caprate(metric="max_loan"),
            "two_way_rate_cap_dscr": self.two_way_rate_caprate(metric="actual_dscr"),
        }


# ---------------------------------------------------------------------------
# Private Helpers
# ---------------------------------------------------------------------------

def _scale_lender_rates(lenders, shift_bps: int):
    """Return a new list of lenders with interest rates shifted by shift_bps."""
    import copy as _copy
    scaled = []
    for l in lenders:
        new_l = _copy.deepcopy(l)
        new_l.interest_rate = max(0.001, new_l.interest_rate + shift_bps / 10_000)
        scaled.append(new_l)
    return scaled


def _clone_model_with_escalation_shift(model: CREDealModel, shift: float) -> CREDealModel:
    """Clone a deal model with all unit escalation rates shifted by `shift`."""
    import copy as _copy
    new_model = _copy.deepcopy(model)
    for u in new_model.units:
        u.annual_escalation = max(0.0, u.annual_escalation + shift)
    return new_model


def _clone_model_with_vacancy(model: CREDealModel, vacancy_rate: float) -> CREDealModel:
    """Clone a deal model with market vacancy rate overridden."""
    import copy as _copy
    new_model = _copy.deepcopy(model)
    new_model.market.market_vacancy_rate = vacancy_rate
    return new_model


# ---------------------------------------------------------------------------
# Sample Usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import date

    # Build a sample model (same as cre_deal_model.py demo)
    RENT_ROLL = [
        {"unit_id": "R-001", "tenant": "Café Delancey LLC",   "unit_type": "Retail",  "sf": 1_200, "monthly_rent": 7_000,  "lease_start": "2022-02-01", "lease_end": "2027-01-31", "annual_escalation": 0.03,  "status": "Occupied"},
        {"unit_id": "R-002", "tenant": "LES Boutique Inc.",   "unit_type": "Retail",  "sf": 800,   "monthly_rent": 4_667,  "lease_start": "2021-06-01", "lease_end": "2026-05-31", "annual_escalation": 0.025, "status": "Occupied"},
        {"unit_id": "O-101", "tenant": "Fintech Partners LP", "unit_type": "Office",  "sf": 2_500, "monthly_rent": 10_417, "lease_start": "2023-01-01", "lease_end": "2028-12-31", "annual_escalation": 0.03,  "status": "Occupied"},
        {"unit_id": "O-102", "tenant": "Creative Studio NYC", "unit_type": "Office",  "sf": 1_800, "monthly_rent": 7_200,  "lease_start": "2020-09-01", "lease_end": "2025-08-31", "annual_escalation": 0.02,  "status": "Occupied"},
        {"unit_id": "S-001", "tenant": "",                    "unit_type": "Storage", "sf": 400,   "monthly_rent": 0,      "lease_start": "2024-01-01", "lease_end": "2024-01-01", "annual_escalation": 0.03,  "status": "Vacant"},
    ]
    total_sf = sum(r["sf"] for r in RENT_ROLL)
    opex = OperatingExpenses(
        real_estate_taxes=95_000, insurance=18_000, repairs_maintenance=22_000,
        management_fee_pct=0.04, utilities=14_000, general_admin=12_000,
        reserves=total_sf * 0.50,
    )
    market = MarketAssumptions(
        market_rent_per_sf={"Retail": 68.0, "Office": 50.0, "Storage": 18.0},
        market_vacancy_rate=0.05, credit_loss_rate=0.01,
    )
    model = CREDealModel(
        property_name="123 Delancey Street",
        address="123 Delancey St, New York, NY 10002",
        property_type="Mixed-Use",
        rent_roll=RENT_ROLL, opex=opex, market=market,
        analysis_date=date(2025, 1, 1),
    )

    sa = SensitivityAnalysis(
        deal_model=model,
        base_cap_rate=0.055,
        base_interest_rate=0.0575,
        base_opex_growth_rate=0.03,
    )

    # Base metrics
    base = sa.base_metrics()
    print("BASE CASE METRICS")
    print(f"  NOI            : ${base['noi']:>12,.0f}")
    print(f"  Property Value : ${base['property_value']:>12,.0f}")
    print(f"  Max Loan       : ${base['max_loan']:>12,.0f}")
    print(f"  DSCR           :  {base['actual_dscr']:>11.2f}x")
    print(f"  LTV            :  {base['actual_ltv']:>11.1%}")
    print(f"  Debt Yield     :  {base['actual_debt_yield']:>11.1%}")
    print(f"  Rec. Lender    :  {base['recommended_lender']}")

    # Vacancy sensitivity
    print("\nVACANCY SENSITIVITY")
    print(f"  {'Vacancy':>10}  {'NOI':>12}  {'DSCR':>8}  {'LTV':>8}  {'Max Loan':>14}  {'Flag'}")
    print("  " + "-" * 65)
    for r in sa.vacancy_sensitivity():
        flag = r.dscr_flag or r.ltv_flag or "-"
        print(f"  {r.variable_label:>10}  ${r.noi:>11,.0f}  {r.actual_dscr:>7.2f}x  {r.actual_ltv:>7.1%}  ${r.max_loan:>13,.0f}  {flag}")

    # Two-way rent × vacancy (NOI)
    tw = sa.two_way_rent_vacancy(metric="noi")
    print("\n2D SENSITIVITY: RENT GROWTH (rows) × VACANCY (cols) — NOI ($)")
    header = f"  {'':>12}" + "".join(f"  {c:>12}" for c in tw["col_labels"])
    print(header)
    for row_label, row_vals, row_flags in zip(tw["row_labels"], tw["table"], tw["flags"]):
        cells = "".join(
            f"  {(str(round(v/1000, 0)) + 'K'):>12}" if v else f"  {'N/A':>12}"
            for v in row_vals
        )
        print(f"  {row_label:>12}{cells}")
