"""
debt_sizing_engine.py
=====================
CRE Debt Sizing Engine — Institutional Capital Stack Underwriting

Sizes commercial real estate loans across three institutional lender types
(Bank, Debt Fund, Agency) using three simultaneous constraints:
  1. DSCR  — Debt Service Coverage Ratio (NOI / Annual Debt Service)
  2. LTV   — Loan-to-Value (Loan Amount / Property Value)
  3. Debt Yield — NOI / Loan Amount (lender's unlevered yield on their capital)

The binding constraint (i.e., the one that produces the smallest loan) is the
maximum loan a lender will approve.  This is standard institutional practice.

Lender Profiles (as configured, adjustable)
--------------------------------------------
  Bank (Commercial Bank / Life Company):
    - DSCR floor     : 1.25×
    - LTV ceiling    : 65%
    - Debt Yield min : 8.5%
    - Rate           : 5.75% (typical 5-yr fixed with 25-yr amortization)
    - IO Period      : 0 years (fully amortizing)

  Debt Fund (Bridge / Mezz / Debt Fund):
    - DSCR floor     : 1.15×
    - LTV ceiling    : 75%
    - Debt Yield min : 7.5%
    - Rate           : 8.50% (floating / higher coupon)
    - IO Period      : Interest-only (no amortization)

  Agency (Fannie DUS / Freddie CME — multifamily-oriented, adaptable):
    - DSCR floor     : 1.25×
    - LTV ceiling    : 80%
    - Debt Yield min : 7.0%
    - Rate           : 5.25% (agency pricing advantage)
    - IO Period      : 0 years (30-yr amortizing)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def monthly_payment(
    principal: float,
    annual_rate: float,
    amortization_years: int,
    interest_only: bool = False,
) -> float:
    """
    Calculate the monthly debt service payment.

    For amortizing loans, uses the standard annuity formula:
        P = L × [r(1+r)^n] / [(1+r)^n − 1]

    For interest-only loans, monthly payment = Principal × (r/12).

    Parameters
    ----------
    principal : float
        Loan amount ($).
    annual_rate : float
        Annual interest rate (e.g. 0.0575 = 5.75%).
    amortization_years : int
        Amortization term in years (ignored if interest_only=True).
    interest_only : bool
        If True, returns interest-only monthly payment.

    Returns
    -------
    float
        Monthly payment ($).
    """
    if principal <= 0:
        return 0.0

    monthly_rate = annual_rate / 12

    if interest_only or amortization_years <= 0:
        return principal * monthly_rate

    n = amortization_years * 12
    if monthly_rate == 0:
        return principal / n

    return principal * (monthly_rate * (1 + monthly_rate) ** n) / ((1 + monthly_rate) ** n - 1)


def annual_debt_service(
    principal: float,
    annual_rate: float,
    amortization_years: int,
    interest_only: bool = False,
) -> float:
    """
    Calculate annual debt service.

    Annual Debt Service (ADS) = Monthly Payment × 12.
    ADS is the key input into DSCR calculation.

    Parameters
    ----------
    principal : float
        Loan amount ($).
    annual_rate : float
        Annual interest rate (decimal).
    amortization_years : int
        Amortization period in years.
    interest_only : bool
        Interest-only flag.

    Returns
    -------
    float
        Annual debt service ($).
    """
    return monthly_payment(principal, annual_rate, amortization_years, interest_only) * 12


def dscr(noi: float, annual_ds: float) -> float:
    """
    Debt Service Coverage Ratio.

    DSCR = NOI / Annual Debt Service

    A DSCR of 1.0× means NOI exactly covers debt service.
    Lenders typically require 1.20–1.35× minimum to maintain cushion.

    Parameters
    ----------
    noi : float
        Net Operating Income ($).
    annual_ds : float
        Annual debt service ($).

    Returns
    -------
    float
        DSCR multiple (e.g. 1.35 = 1.35×).
    """
    if annual_ds <= 0:
        return float("inf")
    return noi / annual_ds


def ltv(loan_amount: float, property_value: float) -> float:
    """
    Loan-to-Value ratio.

    LTV = Loan Amount / Property Value

    Parameters
    ----------
    loan_amount : float
        Loan amount ($).
    property_value : float
        As-is or as-stabilized property value ($).

    Returns
    -------
    float
        LTV as a decimal (e.g. 0.65 = 65%).
    """
    if property_value <= 0:
        return 0.0
    return loan_amount / property_value


def debt_yield(noi: float, loan_amount: float) -> float:
    """
    Debt Yield.

    Debt Yield = NOI / Loan Amount

    Represents the unlevered return on the lender's capital, independent
    of interest rate or amortization.  Became the primary sizing metric
    post-2008 to prevent over-leverage at low interest rates.

    Parameters
    ----------
    noi : float
        Net Operating Income ($).
    loan_amount : float
        Loan amount ($).

    Returns
    -------
    float
        Debt yield as a decimal (e.g. 0.085 = 8.5%).
    """
    if loan_amount <= 0:
        return float("inf")
    return noi / loan_amount


# ---------------------------------------------------------------------------
# Lender Profile
# ---------------------------------------------------------------------------

@dataclass
class LenderProfile:
    """
    Defines underwriting parameters for a single lender type.

    Attributes
    ----------
    name : str
        Lender type label (e.g. "Bank", "Debt Fund", "Agency").
    min_dscr : float
        Minimum required DSCR (e.g. 1.25 = 1.25×).
    max_ltv : float
        Maximum LTV ratio (e.g. 0.65 = 65%).
    min_debt_yield : float
        Minimum required debt yield (e.g. 0.085 = 8.5%).
    interest_rate : float
        Annual coupon / note rate (e.g. 0.0575 = 5.75%).
    amortization_years : int
        Amortization period in years (0 if interest-only).
    interest_only : bool
        If True, loan is interest-only (no amortization).
    loan_term_years : int
        Loan term / maturity (years).
    max_loan_cap : float, optional
        Hard cap on loan amount in dollars (e.g. Fannie DUS limit).
    description : str
        Brief description for reporting.
    """
    name: str
    min_dscr: float
    max_ltv: float
    min_debt_yield: float
    interest_rate: float
    amortization_years: int
    interest_only: bool
    loan_term_years: int
    max_loan_cap: float = float("inf")
    description: str = ""

    def max_loan_by_dscr(self, noi: float) -> float:
        """
        Maximum loan amount constrained by DSCR.

        Solves: NOI / ADS = min_dscr  →  ADS = NOI / min_dscr
        Then back-solves from ADS to loan amount.

        For amortizing loans, inverts the annuity formula:
            L = ADS_monthly × [(1+r)^n − 1] / [r × (1+r)^n]

        For I/O:
            L = ADS / annual_rate
        """
        target_ads = noi / self.min_dscr
        target_monthly = target_ads / 12
        r = self.interest_rate / 12

        if self.interest_only or self.amortization_years <= 0:
            if r == 0:
                return float("inf")
            return target_monthly / r

        n = self.amortization_years * 12
        if r == 0:
            return target_monthly * n

        # Invert annuity formula
        pv_factor = ((1 + r) ** n - 1) / (r * (1 + r) ** n)
        return target_monthly * pv_factor

    def max_loan_by_ltv(self, property_value: float) -> float:
        """Maximum loan amount constrained by LTV."""
        return self.max_ltv * property_value

    def max_loan_by_debt_yield(self, noi: float) -> float:
        """
        Maximum loan amount constrained by Debt Yield.

        Solves: NOI / Loan = min_debt_yield  →  Loan = NOI / min_debt_yield
        """
        if self.min_debt_yield <= 0:
            return float("inf")
        return noi / self.min_debt_yield

    def size_loan(self, noi: float, property_value: float) -> dict[str, Any]:
        """
        Size the maximum loan subject to all three constraints.

        The binding constraint (minimum of the three limits) governs.

        Parameters
        ----------
        noi : float
            Net Operating Income ($).
        property_value : float
            Property value ($).

        Returns
        -------
        dict
            Full debt sizing results including max loan, binding constraint,
            actual DSCR, LTV, debt yield, and annual debt service.
        """
        loan_dscr = self.max_loan_by_dscr(noi)
        loan_ltv = self.max_loan_by_ltv(property_value)
        loan_dy = self.max_loan_by_debt_yield(noi)
        loan_cap = self.max_loan_cap

        # Max loan = minimum of all constraints (most restrictive)
        max_loan = min(loan_dscr, loan_ltv, loan_dy, loan_cap)
        max_loan = max(max_loan, 0)  # floor at $0

        # Determine binding constraint
        constraints = {
            "DSCR": loan_dscr,
            "LTV": loan_ltv,
            "Debt Yield": loan_dy,
        }
        if loan_cap < float("inf"):
            constraints["Hard Cap"] = loan_cap
        binding = min(constraints, key=lambda k: constraints[k])

        # Compute actual metrics at max loan
        ads = annual_debt_service(max_loan, self.interest_rate, self.amortization_years, self.interest_only)
        actual_dscr = dscr(noi, ads)
        actual_ltv = ltv(max_loan, property_value)
        actual_dy = debt_yield(noi, max_loan)

        return {
            "lender": self.name,
            "max_loan": max_loan,
            "binding_constraint": binding,
            "loan_by_dscr": loan_dscr,
            "loan_by_ltv": loan_ltv,
            "loan_by_debt_yield": loan_dy,
            "annual_debt_service": ads,
            "actual_dscr": actual_dscr,
            "actual_ltv": actual_ltv,
            "actual_debt_yield": actual_dy,
            "interest_rate": self.interest_rate,
            "amortization_years": self.amortization_years if not self.interest_only else None,
            "interest_only": self.interest_only,
            "loan_term_years": self.loan_term_years,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Standard Lender Presets
# ---------------------------------------------------------------------------

BANK_LENDER = LenderProfile(
    name="Bank",
    min_dscr=1.25,
    max_ltv=0.65,
    min_debt_yield=0.085,
    interest_rate=0.0575,
    amortization_years=25,
    interest_only=False,
    loan_term_years=5,
    description="Commercial Bank / Life Company — conservative sizing, 25yr amort",
)

DEBT_FUND_LENDER = LenderProfile(
    name="Debt Fund",
    min_dscr=1.15,
    max_ltv=0.75,
    min_debt_yield=0.075,
    interest_rate=0.0850,
    amortization_years=0,
    interest_only=True,
    loan_term_years=3,
    description="Private Debt Fund / Bridge Lender — higher proceeds, I/O, higher rate",
)

AGENCY_LENDER = LenderProfile(
    name="Agency (Fannie/Freddie)",
    min_dscr=1.25,
    max_ltv=0.80,
    min_debt_yield=0.070,
    interest_rate=0.0525,
    amortization_years=30,
    interest_only=False,
    loan_term_years=10,
    description="Agency DUS / CME — highest LTV, best rate, 30yr amort, multifamily focus",
)

DEFAULT_LENDERS = [BANK_LENDER, DEBT_FUND_LENDER, AGENCY_LENDER]


# ---------------------------------------------------------------------------
# Debt Sizing Engine
# ---------------------------------------------------------------------------

class DebtSizingEngine:
    """
    Multi-lender debt sizing and comparison engine.

    Runs all three institutional lender profiles against a single deal
    (defined by NOI and property value) and produces a comparison table
    with a recommendation for maximum proceeds.

    Parameters
    ----------
    noi : float
        Stabilized Net Operating Income ($).
    property_value : float
        As-is or as-stabilized property value ($).
    lenders : list[LenderProfile], optional
        List of lender profiles to evaluate. Defaults to the three standard
        lenders (Bank, Debt Fund, Agency).
    """

    def __init__(
        self,
        noi: float,
        property_value: float,
        lenders: list[LenderProfile] | None = None,
    ) -> None:
        if noi <= 0:
            raise ValueError("NOI must be positive for debt sizing.")
        if property_value <= 0:
            raise ValueError("Property value must be positive.")
        self.noi = noi
        self.property_value = property_value
        self.lenders = lenders if lenders is not None else list(DEFAULT_LENDERS)

    def run(self) -> list[dict[str, Any]]:
        """
        Run debt sizing for all lenders.

        Returns
        -------
        list[dict]
            One result dict per lender, sorted by max_loan descending.
        """
        results = [lender.size_loan(self.noi, self.property_value) for lender in self.lenders]
        return sorted(results, key=lambda r: r["max_loan"], reverse=True)

    def comparison_table(self) -> list[dict[str, Any]]:
        """
        Returns a clean comparison table for all lenders.

        Each row contains key metrics suitable for a deal memo or Excel output.

        Returns
        -------
        list[dict]
            List of formatted comparison rows.
        """
        results = self.run()
        table = []
        for r in results:
            amort = "Interest Only" if r["interest_only"] else f"{r['amortization_years']}yr Amort"
            table.append({
                "Lender": r["lender"],
                "Max Loan ($)": r["max_loan"],
                "Interest Rate": r["interest_rate"],
                "Amortization": amort,
                "Loan Term (Yrs)": r["loan_term_years"],
                "Annual Debt Service ($)": r["annual_debt_service"],
                "Actual DSCR": r["actual_dscr"],
                "Actual LTV": r["actual_ltv"],
                "Actual Debt Yield": r["actual_debt_yield"],
                "Binding Constraint": r["binding_constraint"],
                "Description": r["description"],
            })
        return table

    def recommend(self) -> dict[str, Any]:
        """
        Recommend the optimal lender based on maximum loan proceeds while
        meeting all constraint thresholds.

        Logic:
        1. Filter lenders that pass all three constraints (DSCR ≥ min,
           LTV ≤ max, Debt Yield ≥ min).
        2. Among qualifying lenders, pick the one with the highest max loan.
        3. If no lender qualifies (e.g. very low NOI), return the one with
           the best DSCR (lowest risk).

        Returns
        -------
        dict
            Recommended lender result with a rationale string.
        """
        results = self.run()

        qualifying = []
        for r in results:
            lender = next(l for l in self.lenders if l.name == r["lender"])
            passes_dscr = r["actual_dscr"] >= lender.min_dscr - 0.001  # small tolerance
            passes_ltv = r["actual_ltv"] <= lender.max_ltv + 0.001
            passes_dy = r["actual_debt_yield"] >= lender.min_debt_yield - 0.001
            if passes_dscr and passes_ltv and passes_dy:
                qualifying.append(r)

        if not qualifying:
            # No lender passes — return highest DSCR (most conservative)
            best = max(results, key=lambda r: r["actual_dscr"])
            best["rationale"] = (
                f"No lender meets all constraints at this NOI/Value combination. "
                f"{best['lender']} recommended as most conservative (DSCR {best['actual_dscr']:.2f}×)."
            )
            return best

        # Among qualifying, max proceeds
        best = max(qualifying, key=lambda r: r["max_loan"])
        best["rationale"] = (
            f"{best['lender']} recommended — maximizes loan proceeds at "
            f"${best['max_loan']:,.0f} with DSCR {best['actual_dscr']:.2f}× / "
            f"LTV {best['actual_ltv']:.1%} / Debt Yield {best['actual_debt_yield']:.1%}. "
            f"Binding constraint: {best['binding_constraint']}."
        )
        return best

    def print_comparison(self) -> None:
        """Print formatted comparison table and recommendation to console."""
        table = self.comparison_table()
        rec = self.recommend()

        print("\n" + "=" * 90)
        print("  CRE DEBT SIZING — LENDER COMPARISON")
        print(f"  NOI: ${self.noi:,.0f}   |   Property Value: ${self.property_value:,.0f}")
        print("=" * 90)

        col_w = [25, 16, 12, 16, 12, 22, 12, 12, 14, 18]
        headers = [
            "Lender", "Max Loan ($)", "Rate", "Amortization",
            "Term (Yr)", "Annual Debt Svc ($)", "DSCR", "LTV",
            "Debt Yield", "Binding Constraint"
        ]
        header_line = "  " + "".join(h.ljust(w) for h, w in zip(headers, col_w))
        print(header_line)
        print("  " + "-" * (sum(col_w)))

        for row in table:
            line = "  " + "".join([
                f"${row['Max Loan ($)']:,.0f}".ljust(col_w[1]),
                f"{row['Interest Rate']:.2%}".ljust(col_w[2]),
                row['Amortization'].ljust(col_w[3]),
                f"{row['Loan Term (Yrs)']}yr".ljust(col_w[4]),
                f"${row['Annual Debt Service ($)']:,.0f}".ljust(col_w[5]),
                f"{row['Actual DSCR']:.2f}x".ljust(col_w[6]),
                f"{row['Actual LTV']:.1%}".ljust(col_w[7]),
                f"{row['Actual Debt Yield']:.1%}".ljust(col_w[8]),
                row['Binding Constraint'].ljust(col_w[9]),
            ])
            # Prepend lender name
            print(f"  {row['Lender']:<25}" + line.lstrip())

        print("\n  RECOMMENDATION:")
        print(f"  {rec['rationale']}")
        print("=" * 90)


# ---------------------------------------------------------------------------
# Sample Usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # -----------------------------------------------------------
    # Deal parameters — stabilized NYC mixed-use property
    # -----------------------------------------------------------
    NOI = 1_500_000       # Stabilized NOI
    VALUE = 22_000_000    # Appraised / purchase price

    engine = DebtSizingEngine(noi=NOI, property_value=VALUE)

    # Print comparison table
    engine.print_comparison()

    # Detailed results
    print("\nDETAILED RESULTS BY LENDER:")
    print("-" * 50)
    results = engine.run()
    for r in results:
        print(f"\n  [{r['lender']}]")
        print(f"    Max Loan          : ${r['max_loan']:>12,.0f}")
        print(f"    Binding by DSCR   : ${r['loan_by_dscr']:>12,.0f}")
        print(f"    Binding by LTV    : ${r['loan_by_ltv']:>12,.0f}")
        print(f"    Binding by DY     : ${r['loan_by_debt_yield']:>12,.0f}")
        print(f"    Annual Debt Svc   : ${r['annual_debt_service']:>12,.0f}")
        print(f"    Actual DSCR       :  {r['actual_dscr']:>11.2f}x")
        print(f"    Actual LTV        :  {r['actual_ltv']:>11.1%}")
        print(f"    Actual Debt Yield :  {r['actual_debt_yield']:>11.1%}")
        print(f"    Binding Constraint:  {r['binding_constraint']}")

    # Custom lender example
    print("\n\nCUSTOM LENDER EXAMPLE — Life Insurance Company:")
    life_co = LenderProfile(
        name="Life Company",
        min_dscr=1.30,
        max_ltv=0.60,
        min_debt_yield=0.090,
        interest_rate=0.0545,
        amortization_years=30,
        interest_only=False,
        loan_term_years=10,
        description="Life Insurance Company — lowest rate, conservative sizing, 10yr term",
    )
    custom_engine = DebtSizingEngine(noi=NOI, property_value=VALUE, lenders=[life_co])
    result = custom_engine.run()[0]
    print(f"  Max Loan    : ${result['max_loan']:,.0f}")
    print(f"  Actual DSCR : {result['actual_dscr']:.2f}x")
    print(f"  Actual LTV  : {result['actual_ltv']:.1%}")
    print(f"  Debt Yield  : {result['actual_debt_yield']:.1%}")
