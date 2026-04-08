"""
run_sensitivity.py
==================
Console runner for CRE sensitivity analysis.

Prints formatted sensitivity tables to stdout, with ANSI color codes
for risk flags (BREACH = red, WARNING = yellow).  Falls back to plain
ASCII formatting when the terminal does not support ANSI escape codes.

Usage
-----
    python scripts/run_sensitivity.py [--no-color]

Flags
-----
    --no-color    Disable ANSI color output (useful for log files / CI)

Output Sections
---------------
  1. Base Case Metrics
  2. Rent Growth Rate Sensitivity
  3. Vacancy Rate Sensitivity
  4. Interest Rate Sensitivity
  5. Cap Rate Sensitivity
  6. OpEx Growth Rate Sensitivity
  7. 2D: Rent Growth × Vacancy (NOI)
  8. 2D: Rent Growth × Vacancy (DSCR)
  9. 2D: Interest Rate × Cap Rate (Max Loan)
"""

from __future__ import annotations

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from models.cre_deal_model import CREDealModel, OperatingExpenses, MarketAssumptions
from models.sensitivity_analysis import SensitivityAnalysis, SensitivityResult


# ---------------------------------------------------------------------------
# ANSI Color Support
# ---------------------------------------------------------------------------

USE_COLOR = "--no-color" not in sys.argv

RESET   = "\033[0m"   if USE_COLOR else ""
RED     = "\033[91m"  if USE_COLOR else ""
YELLOW  = "\033[93m"  if USE_COLOR else ""
GREEN   = "\033[92m"  if USE_COLOR else ""
BOLD    = "\033[1m"   if USE_COLOR else ""
CYAN    = "\033[96m"  if USE_COLOR else ""
BLUE    = "\033[94m"  if USE_COLOR else ""
DIM     = "\033[2m"   if USE_COLOR else ""


def colored(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def risk_color(flag: str) -> str:
    """Return ANSI color string for a risk flag."""
    if flag == "BREACH":
        return RED
    if flag == "WARNING":
        return YELLOW
    return GREEN


# ---------------------------------------------------------------------------
# Formatting Helpers
# ---------------------------------------------------------------------------

def fmt_dollars(v: float) -> str:
    if v < 0:
        return f"(${abs(v):>11,.0f})"
    return f"${v:>12,.0f}"

def fmt_pct(v: float) -> str:
    return f"{v:>8.1%}"

def fmt_x(v: float) -> str:
    return f"{v:>8.2f}x"

def divider(width: int = 85, char: str = "-") -> str:
    return "  " + char * width

def thick_divider(width: int = 85) -> str:
    return "  " + "=" * width

def section_title(title: str) -> None:
    print()
    print(thick_divider())
    print(f"  {colored(BOLD + title, BOLD)}")
    print(thick_divider())


# ---------------------------------------------------------------------------
# Build Deal Model
# ---------------------------------------------------------------------------

def build_model() -> CREDealModel:
    """
    Construct the sample 8-unit NYC mixed-use property.

    Same rent roll used across all scripts for consistency.
    """
    RENT_ROLL = [
        {"unit_id": "R-001", "tenant": "Café Delancey LLC",      "unit_type": "Retail",  "sf": 1_200, "monthly_rent": 7_000,  "lease_start": "2022-02-01", "lease_end": "2027-01-31", "annual_escalation": 0.030, "status": "Occupied"},
        {"unit_id": "R-002", "tenant": "LES Boutique Inc.",       "unit_type": "Retail",  "sf": 800,   "monthly_rent": 4_667,  "lease_start": "2021-06-01", "lease_end": "2026-05-31", "annual_escalation": 0.025, "status": "Occupied"},
        {"unit_id": "R-003", "tenant": "Pharmacare Plus",         "unit_type": "Retail",  "sf": 1_500, "monthly_rent": 9_375,  "lease_start": "2023-03-01", "lease_end": "2033-02-28", "annual_escalation": 0.020, "status": "Occupied"},
        {"unit_id": "O-101", "tenant": "Fintech Partners LP",     "unit_type": "Office",  "sf": 2_500, "monthly_rent": 10_417, "lease_start": "2023-01-01", "lease_end": "2028-12-31", "annual_escalation": 0.030, "status": "Occupied"},
        {"unit_id": "O-102", "tenant": "Creative Studio NYC",     "unit_type": "Office",  "sf": 1_800, "monthly_rent": 7_200,  "lease_start": "2020-09-01", "lease_end": "2025-08-31", "annual_escalation": 0.020, "status": "Occupied"},
        {"unit_id": "O-103", "tenant": "Meridian Advisory Group", "unit_type": "Office",  "sf": 2_200, "monthly_rent": 9_350,  "lease_start": "2022-07-01", "lease_end": "2027-06-30", "annual_escalation": 0.030, "status": "Occupied"},
        {"unit_id": "O-104", "tenant": "",                        "unit_type": "Office",  "sf": 1_600, "monthly_rent": 0,      "lease_start": "2024-01-01", "lease_end": "2024-01-01", "annual_escalation": 0.030, "status": "Vacant"},
        {"unit_id": "S-001", "tenant": "SelfStore Manhattan",     "unit_type": "Storage", "sf": 400,   "monthly_rent": 600,    "lease_start": "2023-06-01", "lease_end": "2026-05-31", "annual_escalation": 0.030, "status": "Occupied"},
    ]
    total_sf = sum(r["sf"] for r in RENT_ROLL)
    opex = OperatingExpenses(
        real_estate_taxes=95_000,
        insurance=18_000,
        repairs_maintenance=22_000,
        management_fee_pct=0.04,
        utilities=14_000,
        general_admin=12_000,
        reserves=total_sf * 0.50,
    )
    market = MarketAssumptions(
        market_rent_per_sf={"Retail": 68.0, "Office": 50.0, "Storage": 18.0},
        market_vacancy_rate=0.05,
        credit_loss_rate=0.01,
        lease_up_months=3,
    )
    return CREDealModel(
        property_name="123 Delancey Street",
        address="123 Delancey St, New York, NY 10002",
        property_type="Mixed-Use (Retail + Office)",
        rent_roll=RENT_ROLL,
        opex=opex,
        market=market,
        analysis_date=date(2025, 1, 1),
    )


# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------

def print_base_metrics(base: dict) -> None:
    section_title("BASE CASE METRICS")
    print()
    print(f"  {'Property':<30}: 123 Delancey Street, New York, NY 10002")
    print(f"  {'Asset Type':<30}: Mixed-Use (Retail + Office)")
    print(f"  {'Analysis Date':<30}: 2025-01-01")
    print(f"  {'Total SF':<30}: {base['total_sf']:>12,.0f} sf")
    print(f"  {'Physical Occupancy':<30}: {base['occupancy']:>11.1%}")
    print()
    print(divider())
    print(f"  {'INCOME METRICS'}")
    print(divider())
    print(f"  {'Gross Potential Rent (GPR)':<35}: {fmt_dollars(base['egi'] + (base['noi'] - base['egi'] + base['total_opex']))}")
    print(f"  {'Effective Gross Income (EGI)':<35}: {fmt_dollars(base['egi'])}")
    print(f"  {'Total Operating Expenses':<35}: ({fmt_dollars(base['total_opex']).strip()})")
    noi_line = colored(f"  {'Net Operating Income (NOI)':<35}: {fmt_dollars(base['noi'])}", BOLD)
    print(noi_line)
    print(f"  {'NOI / SF':<35}: ${base['noi'] / base['total_sf']:>11.2f}/sf")
    print()
    print(divider())
    print(f"  {'VALUATION & DEBT'}")
    print(divider())
    print(f"  {'Going-In Cap Rate':<35}:  {'5.50%':>11}")
    print(f"  {'Property Value (Direct Cap)':<35}: {fmt_dollars(base['property_value'])}")
    print(f"  {'Recommended Lender':<35}: {base['recommended_lender']:>12}")
    print(f"  {'Max Loan':<35}: {fmt_dollars(base['max_loan'])}")
    print(f"  {'Loan-to-Value (LTV)':<35}: {fmt_pct(base['actual_ltv']):>11}")
    dscr_str = fmt_x(base['actual_dscr'])
    dscr_color = GREEN if base['actual_dscr'] >= 1.25 else (YELLOW if base['actual_dscr'] >= 1.0 else RED)
    print(f"  {'Debt Service Coverage (DSCR)':<35}: {colored(dscr_str, dscr_color)}")
    print(f"  {'Debt Yield':<35}: {fmt_pct(base['actual_debt_yield']):>11}")
    print(f"  {'Annual Debt Service':<35}: {fmt_dollars(base['annual_debt_service'])}")


def print_one_way_table(
    results: list[SensitivityResult],
    title: str,
    variable_header: str,
    footnote: str = "",
) -> None:
    """Print a formatted one-way sensitivity table."""
    section_title(title)

    # Count risk scenarios
    breaches = [r for r in results if r.dscr_flag == "BREACH" or r.ltv_flag == "BREACH"]
    warnings = [r for r in results if not r.is_distressed and (r.dscr_flag == "WARNING" or r.ltv_flag == "WARNING")]

    if breaches:
        print(f"  {colored(f'  WARNING: {len(breaches)} scenario(s) in BREACH territory (DSCR < 1.0 or LTV > 80%)', RED)}")
    if warnings:
        print(f"  {colored(f'  CAUTION: {len(warnings)} scenario(s) with elevated risk (DSCR 1.0–1.25 or LTV 75–80%)', YELLOW)}")
    print()

    # Header
    col_w = [18, 16, 18, 16, 10, 10, 12, 10]
    headers = [variable_header, "NOI ($)", "Value ($)", "Max Loan ($)", "DSCR", "LTV", "Debt Yield", "Flag"]
    header_line = "  " + "".join(h.center(w) for h, w in zip(headers, col_w))
    print(colored(header_line, BOLD))
    print(divider(sum(col_w) + 4))

    for r in results:
        flag_str = r.dscr_flag or r.ltv_flag or "OK"
        flag_color = risk_color(flag_str)

        row_color = ""
        if flag_str == "BREACH":
            row_color = RED
        elif flag_str == "WARNING":
            row_color = YELLOW

        cells = [
            r.variable_label.center(col_w[0]),
            f"${r.noi:,.0f}".rjust(col_w[1]),
            f"${r.property_value:,.0f}".rjust(col_w[2]),
            f"${r.max_loan:,.0f}".rjust(col_w[3]),
            f"{r.actual_dscr:.2f}x".rjust(col_w[4]),
            f"{r.actual_ltv:.1%}".rjust(col_w[5]),
            f"{r.actual_debt_yield:.1%}".rjust(col_w[6]),
            flag_str.center(col_w[7]),
        ]
        line = "  " + "".join(cells)
        if row_color:
            print(colored(line, row_color))
        else:
            print(line)

    print(divider(sum(col_w) + 4))
    if footnote:
        print(f"  {colored(footnote, DIM)}")

    # Summary
    ok_count = len([r for r in results if not r.dscr_flag and not r.ltv_flag])
    print(f"\n  Scenarios: {len(results)} total | {colored(str(ok_count), GREEN)} OK | "
          f"{colored(str(len(warnings)), YELLOW)} Warning | "
          f"{colored(str(len(breaches)), RED)} Breach")


def print_two_way_table(
    two_way: dict,
    title: str,
    fmt_func=None,
) -> None:
    """Print a formatted 2D sensitivity heatmap."""
    section_title(title)

    col_labels = two_way["col_labels"]
    row_labels = two_way["row_labels"]
    table = two_way["table"]
    flags = two_way["flags"]
    metric = two_way["metric"]

    row_ax = two_way.get("row_axis", "Row Variable")
    col_ax = two_way.get("col_axis", "Column Variable")

    if fmt_func is None:
        if "dscr" in metric:
            fmt_func = lambda v: f"{v:.2f}x"
        elif "ltv" in metric:
            fmt_func = lambda v: f"{v:.1%}"
        else:
            fmt_func = lambda v: f"${v/1000:.0f}K"

    # Count breaches
    total_breach = sum(f == "BREACH" for row_f in flags for f in row_f)
    total_warn   = sum(f == "WARNING" for row_f in flags for f in row_f)

    print(f"\n  {colored('Row axis:', BOLD)} {row_ax}")
    print(f"  {colored('Col axis:', BOLD)} {col_ax}")
    if total_breach:
        print(f"  {colored(f'  {total_breach} BREACH scenario(s) highlighted in red', RED)}")
    if total_warn:
        print(f"  {colored(f'  {total_warn} WARNING scenario(s) highlighted in yellow', YELLOW)}")
    print()

    cell_w = 14
    label_w = 14

    # Header row
    header = " " * (label_w + 2) + "".join(c.center(cell_w) for c in col_labels)
    print(f"  {colored(header.strip(), BOLD)}")
    print("  " + "-" * (label_w + 2 + cell_w * len(col_labels) + 2))

    for row_label, row_vals, row_flags in zip(row_labels, table, flags):
        cells = []
        for val, flag in zip(row_vals, row_flags):
            if val is None:
                cell_str = "N/A".center(cell_w)
            else:
                cell_str = fmt_func(val).center(cell_w)

            flag_color = risk_color(flag)
            if flag in ("BREACH", "WARNING"):
                cells.append(colored(cell_str, flag_color))
            else:
                cells.append(cell_str)

        label_str = row_label.rjust(label_w)
        print(f"  {colored(label_str, BOLD)}  " + "".join(cells))

    print()
    print(f"  {colored('Legend:', BOLD)}  "
          f"{colored('OK (green)', GREEN)}  |  "
          f"{colored('WARNING (yellow): DSCR 1.0–1.25 or LTV 75–80%', YELLOW)}  |  "
          f"{colored('BREACH (red): DSCR < 1.0 or LTV > 80%', RED)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print()
    print(thick_divider(85))
    print(f"  {colored(BOLD + '  CRE SENSITIVITY ANALYSIS — 123 DELANCEY STREET, NEW YORK, NY', BOLD)}")
    print(f"  {colored('  Institutional CRE Capital Markets Underwriting', DIM)}")
    print(thick_divider(85))
    print()
    print(f"  {colored('Color key:', BOLD)}")
    print(f"  {colored('  GREEN  →  All constraints satisfied (DSCR ≥ 1.25x, LTV ≤ 75%)', GREEN)}")
    print(f"  {colored('  YELLOW →  Elevated risk  (DSCR 1.0–1.25x or LTV 75–80%)', YELLOW)}")
    print(f"  {colored('  RED    →  BREACH territory (DSCR < 1.0x or LTV > 80%)', RED)}")

    # Build model and run analysis
    model = build_model()
    sa = SensitivityAnalysis(
        deal_model=model,
        base_cap_rate=0.055,
        base_interest_rate=0.0575,
        base_opex_growth_rate=0.03,
    )

    # 1. Base Case
    base = sa.base_metrics()
    print_base_metrics(base)

    # 2. One-Way: Rent Growth
    print_one_way_table(
        sa.rent_growth_sensitivity(),
        "ONE-WAY SENSITIVITY: RENT GROWTH RATE",
        "Rent Growth Shift",
        footnote="Rent growth shifts applied uniformly to all in-place lease escalation rates.",
    )

    # 3. One-Way: Vacancy
    print_one_way_table(
        sa.vacancy_sensitivity(),
        "ONE-WAY SENSITIVITY: VACANCY RATE",
        "Vacancy Rate",
        footnote="Vacancy applied as stabilized market assumption; in-place leases preserved.",
    )

    # 4. One-Way: Interest Rate
    print_one_way_table(
        sa.interest_rate_sensitivity(),
        "ONE-WAY SENSITIVITY: INTEREST RATE",
        "Rate Shift",
        footnote="Interest rate shift applied uniformly to all three lender profiles.",
    )

    # 5. One-Way: Cap Rate
    print_one_way_table(
        sa.cap_rate_sensitivity(),
        "ONE-WAY SENSITIVITY: CAP RATE",
        "Cap Rate Shift",
        footnote="Cap rate shift affects property value (and therefore LTV, loan proceeds).",
    )

    # 6. One-Way: OpEx Growth
    print_one_way_table(
        sa.opex_growth_sensitivity(),
        "ONE-WAY SENSITIVITY: OPEX GROWTH RATE",
        "OpEx Growth Shift",
        footnote="Year 1 NOI shown; expense growth shifts compound annually.",
    )

    # 7. 2D: Rent × Vacancy (NOI)
    print_two_way_table(
        sa.two_way_rent_vacancy(metric="noi"),
        "2D SENSITIVITY: RENT GROWTH × VACANCY — NET OPERATING INCOME",
        fmt_func=lambda v: f"${v/1000:.0f}K",
    )

    # 8. 2D: Rent × Vacancy (DSCR)
    print_two_way_table(
        sa.two_way_rent_vacancy(metric="actual_dscr"),
        "2D SENSITIVITY: RENT GROWTH × VACANCY — DSCR",
        fmt_func=lambda v: f"{v:.2f}x",
    )

    # 9. 2D: Interest Rate × Cap Rate (Max Loan)
    print_two_way_table(
        sa.two_way_rate_caprate(metric="max_loan"),
        "2D SENSITIVITY: INTEREST RATE SHIFT × CAP RATE SHIFT — MAX LOAN ($)",
        fmt_func=lambda v: f"${v/1_000_000:.1f}M" if v else "N/A",
    )

    # Final summary
    print()
    print(thick_divider(85))
    print(f"  {colored(BOLD + 'RISK SUMMARY', BOLD)}")
    print(thick_divider(85))

    all_one_way = (
        sa.rent_growth_sensitivity()
        + sa.vacancy_sensitivity()
        + sa.interest_rate_sensitivity()
        + sa.cap_rate_sensitivity()
        + sa.opex_growth_sensitivity()
    )
    all_breach  = [r for r in all_one_way if r.dscr_flag == "BREACH" or r.ltv_flag == "BREACH"]
    all_warning = [r for r in all_one_way if (r.dscr_flag == "WARNING" or r.ltv_flag == "WARNING") and not r.is_distressed]
    all_ok      = [r for r in all_one_way if not r.dscr_flag and not r.ltv_flag]

    print()
    print(f"  One-way scenarios analyzed : {len(all_one_way)}")
    print(f"  {colored(f'  OK           : {len(all_ok)}', GREEN)}")
    print(f"  {colored(f'  WARNING      : {len(all_warning)}', YELLOW)}")
    print(f"  {colored(f'  BREACH       : {len(all_breach)}', RED)}")
    print()

    if all_breach:
        print(f"  {colored('Breach Scenarios:', RED)}")
        for r in all_breach:
            print(f"    {r.variable_name} at {r.variable_label}:")
            print(f"      NOI={fmt_dollars(r.noi).strip()}, DSCR={r.actual_dscr:.2f}x, LTV={r.actual_ltv:.1%}")
    else:
        print(f"  {colored('  No DSCR breaches in base one-way scenarios.', GREEN)}")

    print()
    print(thick_divider(85))
    print()


if __name__ == "__main__":
    main()
