"""
cre_deal_model.py
=================
Comprehensive Commercial Real Estate (CRE) Deal Model

Implements institutional-grade underwriting for income-producing CRE assets,
following standard capital markets practices used by REITs, private equity
sponsors, and debt funds.

Key Concepts
------------
- Gross Potential Rent (GPR): Maximum rent if 100% occupied at market/contract rents
- Vacancy & Credit Loss: Allowance for physical vacancy and bad debt (typically 5-10%)
- Effective Gross Income (EGI): GPR less vacancy/credit loss plus ancillary income
- Operating Expenses (OpEx): All property-level expenses before debt service
- Net Operating Income (NOI): EGI minus OpEx — the fundamental CRE value driver
- Cap Rate: NOI / Property Value; the market-based yield used for direct capitalization
- DCF: Discounted Cash Flow model projecting levered or unlevered cash flows over a hold period
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import copy


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class Unit:
    """Represents a single rentable unit within a property."""
    unit_id: str
    tenant: str
    unit_type: str          # e.g. "Retail", "Office", "Residential"
    sf: float               # Rentable square footage
    monthly_rent: float     # Current in-place monthly rent ($)
    lease_start: date       # Lease commencement date
    lease_end: date         # Lease expiration date
    annual_escalation: float  # Annual rent escalation rate (e.g. 0.03 = 3%)
    status: str = "Occupied"  # "Occupied", "Vacant", "Month-to-Month"

    @property
    def annual_rent(self) -> float:
        """Annualized in-place rent."""
        return self.monthly_rent * 12

    @property
    def rent_per_sf(self) -> float:
        """In-place rent per rentable square foot (annualized)."""
        if self.sf > 0:
            return self.annual_rent / self.sf
        return 0.0

    @property
    def months_remaining(self) -> float:
        """Months remaining on lease from today."""
        today = date.today()
        if self.lease_end <= today:
            return 0.0
        delta = self.lease_end - today
        return delta.days / 30.44

    @property
    def is_vacant(self) -> bool:
        return self.status.lower() in ("vacant", "available")


@dataclass
class OperatingExpenses:
    """
    Property-level operating expense breakdown.

    Follows NCREIF/BOMA standard expense categories used across
    institutional CRE underwriting.
    """
    real_estate_taxes: float   # Annual RE tax burden ($)
    insurance: float           # Property & liability insurance ($)
    repairs_maintenance: float # R&M — excludes capital expenditures ($)
    management_fee_pct: float  # Property management fee as % of EGI (e.g. 0.04)
    utilities: float           # Common area utilities, water/sewer ($)
    general_admin: float       # G&A — legal, accounting, marketing ($)
    reserves: float            # Capital reserves / replacement reserves ($/sf/yr × total SF)

    def fixed_expenses(self) -> float:
        """Sum of all fixed (non-management) operating expenses."""
        return (
            self.real_estate_taxes
            + self.insurance
            + self.repairs_maintenance
            + self.utilities
            + self.general_admin
            + self.reserves
        )

    def total_expenses(self, egi: float) -> float:
        """
        Total operating expenses including management fee.

        Parameters
        ----------
        egi : float
            Effective Gross Income used to compute the management fee.
        """
        mgmt_fee = self.management_fee_pct * egi
        return self.fixed_expenses() + mgmt_fee


@dataclass
class MarketAssumptions:
    """
    Market-level rent and vacancy assumptions used for pro forma projection.

    Attributes
    ----------
    market_rent_per_sf : dict
        Market rent assumptions by unit type {unit_type: $/sf/yr}.
    market_vacancy_rate : float
        Stabilized market vacancy rate (e.g. 0.05 = 5%).
    credit_loss_rate : float
        Bad debt / credit loss allowance as % of GPR (e.g. 0.01 = 1%).
    lease_up_months : int
        Assumed re-lease period (months of downtime) upon lease rollover.
    tenant_improvement_allowance : dict
        TI allowance per new lease by unit type {unit_type: $/sf}.
    leasing_commission_pct : float
        Leasing commission as % of total lease value (e.g. 0.04 = 4%).
    """
    market_rent_per_sf: dict[str, float] = field(
        default_factory=lambda: {"Retail": 65.0, "Office": 50.0, "Storage": 18.0}
    )
    market_vacancy_rate: float = 0.05
    credit_loss_rate: float = 0.01
    lease_up_months: int = 3
    tenant_improvement_allowance: dict[str, float] = field(
        default_factory=lambda: {"Retail": 40.0, "Office": 60.0, "Storage": 5.0}
    )
    leasing_commission_pct: float = 0.04


# ---------------------------------------------------------------------------
# Core Model
# ---------------------------------------------------------------------------

class CREDealModel:
    """
    Institutional-grade CRE deal underwriting model.

    Performs static (current-year) and dynamic (5-year pro forma) underwriting
    for income-producing commercial real estate assets.  Supports direct
    capitalization and discounted cash flow (DCF) valuation.

    Parameters
    ----------
    property_name : str
        Descriptive name for the asset.
    address : str
        Property address.
    property_type : str
        Asset class (e.g. "Mixed-Use", "Office", "Retail", "Multifamily").
    rent_roll : list[dict]
        Each dict must contain:
        - unit_id (str)
        - tenant (str)
        - unit_type (str)
        - sf (float)
        - monthly_rent (float)
        - lease_start (str | date)  format YYYY-MM-DD if str
        - lease_end (str | date)
        - annual_escalation (float)
        - status (str, optional, default "Occupied")
    opex : OperatingExpenses
        Operating expense structure for the property.
    market : MarketAssumptions
        Market-level rent and vacancy assumptions.
    analysis_date : date, optional
        As-of date for the underwriting (default: today).
    """

    def __init__(
        self,
        property_name: str,
        address: str,
        property_type: str,
        rent_roll: list[dict],
        opex: OperatingExpenses,
        market: MarketAssumptions,
        analysis_date: date | None = None,
    ) -> None:
        self.property_name = property_name
        self.address = address
        self.property_type = property_type
        self.analysis_date = analysis_date or date.today()
        self.opex = opex
        self.market = market
        self.units: list[Unit] = self._parse_rent_roll(rent_roll)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(d: Any) -> date:
        if isinstance(d, date):
            return d
        if isinstance(d, datetime):
            return d.date()
        return datetime.strptime(str(d), "%Y-%m-%d").date()

    def _parse_rent_roll(self, rent_roll: list[dict]) -> list[Unit]:
        units = []
        for row in rent_roll:
            units.append(
                Unit(
                    unit_id=str(row["unit_id"]),
                    tenant=str(row.get("tenant", "Vacant")),
                    unit_type=str(row.get("unit_type", "Office")),
                    sf=float(row["sf"]),
                    monthly_rent=float(row.get("monthly_rent", 0)),
                    lease_start=self._parse_date(row["lease_start"]),
                    lease_end=self._parse_date(row["lease_end"]),
                    annual_escalation=float(row.get("annual_escalation", 0.03)),
                    status=str(row.get("status", "Occupied")),
                )
            )
        return units

    # ------------------------------------------------------------------
    # Portfolio Metrics
    # ------------------------------------------------------------------

    @property
    def total_sf(self) -> float:
        """Total rentable area across all units (sq ft)."""
        return sum(u.sf for u in self.units)

    @property
    def occupied_sf(self) -> float:
        """Currently occupied square footage."""
        return sum(u.sf for u in self.units if not u.is_vacant)

    @property
    def physical_occupancy(self) -> float:
        """Physical occupancy rate (occupied SF / total SF)."""
        if self.total_sf == 0:
            return 0.0
        return self.occupied_sf / self.total_sf

    @property
    def physical_vacancy(self) -> float:
        """Physical vacancy rate (1 − occupancy)."""
        return 1.0 - self.physical_occupancy

    # ------------------------------------------------------------------
    # Income Statement — Current Year (Static Underwriting)
    # ------------------------------------------------------------------

    def gross_potential_rent(self) -> float:
        """
        Gross Potential Rent (GPR).

        Sum of all in-place annualized rents, treating vacant units at
        their market rent equivalent.  GPR represents the maximum rental
        income achievable at 100% occupancy.

        Returns
        -------
        float
            Annual GPR ($).
        """
        gpr = 0.0
        for u in self.units:
            if u.is_vacant:
                # Underwrite vacant units at market rent
                market_rate = self.market.market_rent_per_sf.get(u.unit_type, 50.0)
                gpr += u.sf * market_rate
            else:
                gpr += u.annual_rent
        return gpr

    def vacancy_and_credit_loss(self) -> float:
        """
        Vacancy & Credit Loss allowance.

        Computed as: (market vacancy rate × GPR) + (credit loss rate × GPR).
        Even if current occupancy is higher than the market vacancy rate, the
        stabilized market vacancy is applied to reflect long-run underwriting
        discipline.

        Returns
        -------
        float
            Annual vacancy and credit loss deduction ($).
        """
        gpr = self.gross_potential_rent()
        vacancy_loss = self.market.market_vacancy_rate * gpr
        credit_loss = self.market.credit_loss_rate * gpr
        return vacancy_loss + credit_loss

    def effective_gross_income(self) -> float:
        """
        Effective Gross Income (EGI).

        EGI = GPR − Vacancy & Credit Loss + Other Income.
        Other income (e.g. parking, laundry, late fees) is not modeled here
        but can be added via subclass extension.

        Returns
        -------
        float
            Annual EGI ($).
        """
        return self.gross_potential_rent() - self.vacancy_and_credit_loss()

    def operating_expense_detail(self) -> dict[str, float]:
        """
        Itemized operating expense breakdown.

        Returns
        -------
        dict[str, float]
            Dictionary mapping expense line item to annual $ amount.
        """
        egi = self.effective_gross_income()
        mgmt_fee = self.opex.management_fee_pct * egi
        return {
            "Real Estate Taxes": self.opex.real_estate_taxes,
            "Insurance": self.opex.insurance,
            "Repairs & Maintenance": self.opex.repairs_maintenance,
            "Management Fee": mgmt_fee,
            "Utilities": self.opex.utilities,
            "General & Administrative": self.opex.general_admin,
            "Reserves": self.opex.reserves,
        }

    def total_operating_expenses(self) -> float:
        """
        Total Operating Expenses (OpEx).

        Returns
        -------
        float
            Total annual operating expenses ($).
        """
        return sum(self.operating_expense_detail().values())

    def net_operating_income(self) -> float:
        """
        Net Operating Income (NOI).

        NOI = EGI − Total Operating Expenses.

        NOI is the primary value metric in CRE — it represents the property's
        income-producing capacity before debt service, depreciation, and taxes.

        Returns
        -------
        float
            Annual NOI ($).
        """
        return self.effective_gross_income() - self.total_operating_expenses()

    def expense_ratio(self) -> float:
        """Operating expense ratio (OpEx / EGI)."""
        egi = self.effective_gross_income()
        if egi == 0:
            return 0.0
        return self.total_operating_expenses() / egi

    def noi_per_sf(self) -> float:
        """NOI per rentable square foot."""
        if self.total_sf == 0:
            return 0.0
        return self.net_operating_income() / self.total_sf

    # ------------------------------------------------------------------
    # 5-Year Pro Forma
    # ------------------------------------------------------------------

    def _project_unit_rent(self, unit: Unit, year: int) -> float:
        """
        Project in-place rent for a unit in a future year.

        Lease-expiry logic:
        - While the lease is in force, apply in-place escalations.
        - On rollover (lease expiry), assume lease-up period then re-lease
          at market rent with continued market escalations.

        Parameters
        ----------
        unit : Unit
            The unit being projected.
        year : int
            Projection year (1 = first full year of hold).

        Returns
        -------
        float
            Projected annual rent for that year ($).
        """
        projection_date = date(self.analysis_date.year + year, self.analysis_date.month, 1)

        if unit.is_vacant:
            # Vacant unit: assume re-lease at market at start of hold
            market_rate = self.market.market_rent_per_sf.get(unit.unit_type, 50.0)
            return unit.sf * market_rate * ((1 + 0.03) ** year)

        if projection_date <= unit.lease_end:
            # In-place lease still active — apply cumulative escalation
            return unit.annual_rent * ((1 + unit.annual_escalation) ** year)
        else:
            # Lease has expired — project market rent with lease-up gap
            years_since_expiry = (projection_date.year - unit.lease_end.year) + max(
                0, (projection_date.month - unit.lease_end.month) / 12
            )
            market_rate = self.market.market_rent_per_sf.get(unit.unit_type, 50.0)
            # Apply market rent escalation from base year
            return unit.sf * market_rate * ((1 + 0.03) ** year)

    def _unit_is_leased_in_year(self, unit: Unit, year: int) -> bool:
        """
        Determine if a unit is occupied during a given projection year.

        During the lease-up period after expiry, the unit is treated as
        temporarily vacant.

        Parameters
        ----------
        unit : Unit
            The unit being assessed.
        year : int
            Projection year (1-based).
        """
        projection_date = date(self.analysis_date.year + year, self.analysis_date.month, 1)

        if unit.is_vacant:
            # Vacant units assumed to lease up within lease_up_months from start
            lease_up_date = date(
                self.analysis_date.year,
                self.analysis_date.month + self.market.lease_up_months
                if self.analysis_date.month + self.market.lease_up_months <= 12
                else self.analysis_date.month + self.market.lease_up_months - 12,
                1,
            )
            return projection_date >= lease_up_date

        if projection_date <= unit.lease_end:
            return True

        # After lease expiry: assume lease_up_months of downtime
        rollover_date = date(
            unit.lease_end.year + (1 if unit.lease_end.month + self.market.lease_up_months > 12 else 0),
            (unit.lease_end.month + self.market.lease_up_months - 1) % 12 + 1,
            1,
        )
        return projection_date >= rollover_date

    def five_year_pro_forma(
        self,
        opex_growth_rate: float = 0.03,
        additional_vacancy_rate: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Generate a 5-year pro forma income statement.

        Parameters
        ----------
        opex_growth_rate : float
            Annual growth rate applied to fixed operating expenses (e.g. 0.03 = 3%).
            Management fee auto-adjusts as a % of EGI.
        additional_vacancy_rate : float, optional
            Override the market vacancy rate for the projection. If None, uses
            the MarketAssumptions.market_vacancy_rate.

        Returns
        -------
        list[dict]
            One dict per year (Year 1–5), each containing:
            - year (int)
            - gpr (float)
            - vacancy_credit_loss (float)
            - egi (float)
            - opex_detail (dict)
            - total_opex (float)
            - noi (float)
            - occupancy (float)
        """
        vac_rate = additional_vacancy_rate if additional_vacancy_rate is not None else self.market.market_vacancy_rate
        results = []

        for yr in range(1, 6):
            # --- GPR ---
            gpr = 0.0
            occupied_sf = 0.0
            for u in self.units:
                is_leased = self._unit_is_leased_in_year(u, yr)
                if is_leased:
                    gpr += self._project_unit_rent(u, yr)
                    occupied_sf += u.sf
                else:
                    # Vacant — GPR at market (for vacancy tracking), not collected
                    market_rate = self.market.market_rent_per_sf.get(u.unit_type, 50.0)
                    gpr += u.sf * market_rate * ((1 + 0.03) ** yr)

            occupancy = occupied_sf / self.total_sf if self.total_sf > 0 else 0.0

            # --- Vacancy & Credit Loss ---
            vcl = (vac_rate + self.market.credit_loss_rate) * gpr
            egi = gpr - vcl

            # --- Operating Expenses (fixed grow at opex_growth_rate) ---
            growth = (1 + opex_growth_rate) ** yr
            mgmt_fee = self.opex.management_fee_pct * egi
            opex_detail = {
                "Real Estate Taxes": self.opex.real_estate_taxes * growth,
                "Insurance": self.opex.insurance * growth,
                "Repairs & Maintenance": self.opex.repairs_maintenance * growth,
                "Management Fee": mgmt_fee,
                "Utilities": self.opex.utilities * growth,
                "General & Administrative": self.opex.general_admin * growth,
                "Reserves": self.opex.reserves * growth,
            }
            total_opex = sum(opex_detail.values())
            noi = egi - total_opex

            results.append({
                "year": yr,
                "calendar_year": self.analysis_date.year + yr,
                "gpr": gpr,
                "vacancy_credit_loss": vcl,
                "egi": egi,
                "opex_detail": opex_detail,
                "total_opex": total_opex,
                "noi": noi,
                "occupancy": occupancy,
                "noi_per_sf": noi / self.total_sf if self.total_sf > 0 else 0.0,
            })

        return results

    # ------------------------------------------------------------------
    # Valuation
    # ------------------------------------------------------------------

    def direct_cap_value(self, cap_rate: float) -> float:
        """
        Direct Capitalization Value.

        The direct cap method converts a single year's stabilized NOI into
        a property value using a market-derived capitalization rate.

            Value = NOI / Cap Rate

        This is the most common initial pricing metric in CRE transactions.

        Parameters
        ----------
        cap_rate : float
            Market capitalization rate (e.g. 0.055 = 5.5%).

        Returns
        -------
        float
            Implied property value ($).
        """
        if cap_rate <= 0:
            raise ValueError("Cap rate must be positive.")
        return self.net_operating_income() / cap_rate

    def dcf_valuation(
        self,
        discount_rate: float,
        terminal_cap_rate: float,
        hold_period: int = 5,
        opex_growth_rate: float = 0.03,
        selling_costs_pct: float = 0.01,
    ) -> dict[str, Any]:
        """
        Discounted Cash Flow (DCF) Valuation.

        Projects unlevered free cash flows (NOI) over the hold period,
        computes a terminal value at sale using the terminal cap rate, and
        discounts all cash flows to present value.

        Parameters
        ----------
        discount_rate : float
            Unlevered discount rate / target IRR (e.g. 0.08 = 8%).
        terminal_cap_rate : float
            Exit cap rate applied to Year 6 NOI to derive terminal value
            (typically 25–50 bps above entry cap rate).
        hold_period : int
            Hold period in years (default 5).
        opex_growth_rate : float
            Annual operating expense growth rate (default 3%).
        selling_costs_pct : float
            Transaction costs at exit as % of gross sale price (default 1%).

        Returns
        -------
        dict
            - pv_cash_flows (float): PV of annual NOI cash flows
            - gross_terminal_value (float): Year N+1 NOI / terminal cap rate
            - net_terminal_value (float): Gross terminal value less selling costs
            - pv_terminal_value (float): PV of net terminal value
            - dcf_value (float): Total DCF value (PV CFs + PV terminal)
            - annual_flows (list[dict]): Year-by-year detail
            - implied_cap_rate (float): Entry cap rate on DCF value
        """
        if hold_period < 1:
            raise ValueError("Hold period must be at least 1 year.")
        if discount_rate <= 0 or terminal_cap_rate <= 0:
            raise ValueError("Discount rate and terminal cap rate must be positive.")

        pro_forma = self.five_year_pro_forma(opex_growth_rate=opex_growth_rate)
        # If hold_period > 5, extend with conservative flat NOI
        if hold_period > 5:
            last = pro_forma[-1]
            for yr in range(6, hold_period + 1):
                extended = copy.deepcopy(last)
                extended["year"] = yr
                extended["calendar_year"] = self.analysis_date.year + yr
                pro_forma.append(extended)

        annual_flows = []
        pv_sum = 0.0

        for yr_data in pro_forma[:hold_period]:
            yr = yr_data["year"]
            noi = yr_data["noi"]
            pv = noi / ((1 + discount_rate) ** yr)
            pv_sum += pv
            annual_flows.append({
                "year": yr,
                "calendar_year": yr_data["calendar_year"],
                "noi": noi,
                "pv_noi": pv,
            })

        # Terminal value: Year (N+1) NOI / terminal cap rate
        # Year N+1 NOI estimated as Year N NOI grown by 1 year of opex growth (proxy)
        year_n_noi = pro_forma[hold_period - 1]["noi"]
        year_n1_noi = year_n_noi * (1 + opex_growth_rate)
        gross_tv = year_n1_noi / terminal_cap_rate
        net_tv = gross_tv * (1 - selling_costs_pct)
        pv_tv = net_tv / ((1 + discount_rate) ** hold_period)

        dcf_value = pv_sum + pv_tv
        entry_noi = self.net_operating_income()
        implied_cap = entry_noi / dcf_value if dcf_value > 0 else 0.0

        return {
            "pv_cash_flows": pv_sum,
            "gross_terminal_value": gross_tv,
            "net_terminal_value": net_tv,
            "pv_terminal_value": pv_tv,
            "dcf_value": dcf_value,
            "annual_flows": annual_flows,
            "implied_cap_rate": implied_cap,
            "terminal_cap_rate": terminal_cap_rate,
            "discount_rate": discount_rate,
            "hold_period": hold_period,
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def underwriting_summary(self, cap_rate: float = 0.055) -> dict[str, Any]:
        """
        Consolidated underwriting summary for deal memo / IC presentation.

        Parameters
        ----------
        cap_rate : float
            Going-in cap rate for direct cap valuation (default 5.5%).

        Returns
        -------
        dict
            All key underwriting metrics in a structured dictionary.
        """
        return {
            "property_name": self.property_name,
            "address": self.address,
            "property_type": self.property_type,
            "analysis_date": self.analysis_date.isoformat(),
            "total_sf": self.total_sf,
            "occupied_sf": self.occupied_sf,
            "physical_occupancy": self.physical_occupancy,
            "gross_potential_rent": self.gross_potential_rent(),
            "vacancy_credit_loss": self.vacancy_and_credit_loss(),
            "effective_gross_income": self.effective_gross_income(),
            "opex_detail": self.operating_expense_detail(),
            "total_opex": self.total_operating_expenses(),
            "net_operating_income": self.net_operating_income(),
            "expense_ratio": self.expense_ratio(),
            "noi_per_sf": self.noi_per_sf(),
            "direct_cap_value": self.direct_cap_value(cap_rate),
            "going_in_cap_rate": cap_rate,
        }

    def print_summary(self, cap_rate: float = 0.055) -> None:
        """Print a formatted underwriting summary to console."""
        s = self.underwriting_summary(cap_rate)
        print("=" * 65)
        print(f"  CRE DEAL UNDERWRITING SUMMARY")
        print(f"  {s['property_name']}")
        print(f"  {s['address']}")
        print("=" * 65)
        print(f"  Property Type    : {s['property_type']}")
        print(f"  Analysis Date    : {s['analysis_date']}")
        print(f"  Total SF         : {s['total_sf']:>12,.0f} sf")
        print(f"  Occupied SF      : {s['occupied_sf']:>12,.0f} sf")
        print(f"  Physical Occ.    : {s['physical_occupancy']:>12.1%}")
        print("-" * 65)
        print(f"  Gross Potential Rent        : ${s['gross_potential_rent']:>12,.0f}")
        print(f"  Less: Vacancy & Credit Loss : (${s['vacancy_credit_loss']:>11,.0f})")
        print(f"  Effective Gross Income      : ${s['effective_gross_income']:>12,.0f}")
        print("-" * 65)
        print("  OPERATING EXPENSES:")
        for k, v in s["opex_detail"].items():
            print(f"    {k:<30}: (${v:>11,.0f})")
        print(f"  Total Operating Expenses   : (${s['total_opex']:>11,.0f})")
        print(f"  Expense Ratio              :  {s['expense_ratio']:>11.1%}")
        print("-" * 65)
        print(f"  NET OPERATING INCOME       : ${s['net_operating_income']:>12,.0f}")
        print(f"  NOI / SF                   : ${s['noi_per_sf']:>12.2f}/sf")
        print("-" * 65)
        print(f"  Going-In Cap Rate          :  {s['going_in_cap_rate']:>11.2%}")
        print(f"  Direct Cap Value           : ${s['direct_cap_value']:>12,.0f}")
        print("=" * 65)

    def print_pro_forma(self, opex_growth_rate: float = 0.03) -> None:
        """Print a formatted 5-year pro forma to console."""
        pf = self.five_year_pro_forma(opex_growth_rate=opex_growth_rate)

        header = f"{'Line Item':<32}" + "".join(
            f"  {'Yr ' + str(r['year']) + ' (' + str(r['calendar_year']) + ')':>14}"
            for r in pf
        )
        print("\n" + "=" * (32 + 16 * 5))
        print("  5-YEAR PRO FORMA INCOME STATEMENT")
        print("=" * (32 + 16 * 5))
        print(header)
        print("-" * (32 + 16 * 5))

        rows = [
            ("Gross Potential Rent", "gpr"),
            ("Vacancy & Credit Loss", "vacancy_credit_loss"),
            ("Effective Gross Income", "egi"),
            ("Total Operating Expenses", "total_opex"),
            ("Net Operating Income", "noi"),
        ]
        for label, key in rows:
            sign = "" if key not in ("vacancy_credit_loss", "total_opex") else "-"
            line = f"  {label:<30}"
            for r in pf:
                val = r[key]
                line += f"  ${val:>13,.0f}"
            print(line)

        print("-" * (32 + 16 * 5))
        occ_line = f"  {'Occupancy':<30}"
        noi_sf_line = f"  {'NOI/SF':<30}"
        for r in pf:
            occ_line += f"   {r['occupancy']:>13.1%}"
            noi_sf_line += f"  ${r['noi_per_sf']:>12.2f}"
        print(occ_line)
        print(noi_sf_line)
        print("=" * (32 + 16 * 5))


# ---------------------------------------------------------------------------
# Sample Usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # -----------------------------------------------------------
    # Sample Property: 5-Unit Mixed-Use Building, New York City
    # Lower East Side, Manhattan — Ground Floor Retail + Upper Office
    # -----------------------------------------------------------

    RENT_ROLL = [
        {
            "unit_id": "R-001",
            "tenant": "Café Delancey LLC",
            "unit_type": "Retail",
            "sf": 1_200,
            "monthly_rent": 7_000,       # ~$70/sf/yr — ground floor retail
            "lease_start": "2022-02-01",
            "lease_end": "2027-01-31",
            "annual_escalation": 0.03,
            "status": "Occupied",
        },
        {
            "unit_id": "R-002",
            "tenant": "LES Boutique Inc.",
            "unit_type": "Retail",
            "sf": 800,
            "monthly_rent": 4_667,       # ~$70/sf/yr
            "lease_start": "2021-06-01",
            "lease_end": "2026-05-31",
            "annual_escalation": 0.025,
            "status": "Occupied",
        },
        {
            "unit_id": "O-101",
            "tenant": "Fintech Partners LP",
            "unit_type": "Office",
            "sf": 2_500,
            "monthly_rent": 10_417,      # ~$50/sf/yr — upper floor office
            "lease_start": "2023-01-01",
            "lease_end": "2028-12-31",
            "annual_escalation": 0.03,
            "status": "Occupied",
        },
        {
            "unit_id": "O-102",
            "tenant": "Creative Studio NYC",
            "unit_type": "Office",
            "sf": 1_800,
            "monthly_rent": 7_200,       # ~$48/sf/yr
            "lease_start": "2020-09-01",
            "lease_end": "2025-08-31",
            "annual_escalation": 0.02,
            "status": "Occupied",
        },
        {
            "unit_id": "S-001",
            "tenant": "",
            "unit_type": "Storage",
            "sf": 400,
            "monthly_rent": 0,           # Currently vacant
            "lease_start": "2024-01-01",
            "lease_end": "2024-01-01",
            "annual_escalation": 0.03,
            "status": "Vacant",
        },
    ]

    # Operating expenses — NYC mixed-use typical
    opex = OperatingExpenses(
        real_estate_taxes=95_000,       # NYC RE taxes are punishing
        insurance=18_000,
        repairs_maintenance=22_000,
        management_fee_pct=0.04,        # 4% of EGI
        utilities=14_000,               # Common area utilities
        general_admin=12_000,
        reserves=400 * 6,              # $6/sf/yr × 6,700 SF (proxy — total sf)
    )

    # Re-compute reserves based on full SF
    total_sf_estimate = sum(r["sf"] for r in RENT_ROLL)
    opex.reserves = total_sf_estimate * 0.50   # $0.50/sf/yr replacement reserves

    # Market assumptions for Lower East Side / SoHo adjacent
    market = MarketAssumptions(
        market_rent_per_sf={"Retail": 68.0, "Office": 50.0, "Storage": 18.0},
        market_vacancy_rate=0.05,
        credit_loss_rate=0.01,
        lease_up_months=3,
        tenant_improvement_allowance={"Retail": 45.0, "Office": 60.0, "Storage": 5.0},
        leasing_commission_pct=0.04,
    )

    model = CREDealModel(
        property_name="123 Delancey Street",
        address="123 Delancey St, New York, NY 10002",
        property_type="Mixed-Use (Retail + Office)",
        rent_roll=RENT_ROLL,
        opex=opex,
        market=market,
        analysis_date=date(2025, 1, 1),
    )

    # Print static underwriting summary
    model.print_summary(cap_rate=0.055)

    # Print 5-year pro forma
    model.print_pro_forma(opex_growth_rate=0.03)

    # DCF Valuation
    print("\nDCF VALUATION")
    print("-" * 45)
    dcf = model.dcf_valuation(
        discount_rate=0.08,
        terminal_cap_rate=0.06,
        hold_period=5,
        opex_growth_rate=0.03,
        selling_costs_pct=0.01,
    )
    print(f"  PV of NOI Cash Flows    : ${dcf['pv_cash_flows']:>12,.0f}")
    print(f"  Gross Terminal Value    : ${dcf['gross_terminal_value']:>12,.0f}")
    print(f"  Net Terminal Value      : ${dcf['net_terminal_value']:>12,.0f}")
    print(f"  PV of Terminal Value    : ${dcf['pv_terminal_value']:>12,.0f}")
    print(f"  DCF Value (Unlevered)   : ${dcf['dcf_value']:>12,.0f}")
    print(f"  Implied Going-In Cap    :  {dcf['implied_cap_rate']:>11.2%}")
