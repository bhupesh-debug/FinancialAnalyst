"""
quote_matrix_updater.py
=======================
CRE Capital Markets — Dynamic Quote Matrix Updater

Ingests lender quotes for a deal, scores them across multiple dimensions,
generates a formatted comparison matrix, exports to CSV, and produces a
ranked recommendation with written rationale.

Scoring model (100 points total):
  - Rate       : 30 pts  (lower is better)
  - Proceeds   : 30 pts  (higher is better)
  - Loan Terms : 20 pts  (IO period, non-recourse, prepay flexibility)
  - Execution  : 20 pts  (close speed, lender certainty)

Usage:
    python quote_matrix_updater.py                  # Run demo
    from quote_matrix_updater import QuoteMatrixUpdater  # Import as module

Author: [Analyst Name]
Version: 1.0
"""

import csv
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_RATE_TYPES = ["Fixed", "Floating", "Hybrid"]
VALID_PREPAY_TYPES = [
    "Defeasance",
    "Yield_Maintenance",
    "Stepdown",
    "Open",
    "Open_After_Lockout",
    "None",
]
VALID_RECOURSE_TYPES = ["Non_Recourse", "Full_Recourse", "Limited_Recourse"]

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_THIS_DIR, "..", "data")
OUTPUT_DIR = os.path.join(_THIS_DIR, "..", "output")


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Data structure for a single quote
# ---------------------------------------------------------------------------


class LenderQuote:
    """
    Represents a single lender's term sheet quote for a deal.

    Attributes
    ----------
    lender_name : str
    lender_type : str
        E.g., 'Life_Company', 'Agency_GSE', 'CMBS', 'Debt_Fund', 'Bank'
    loan_amount : float
        Quoted loan amount in USD.
    ltv : float
        Loan-to-value ratio as decimal (e.g., 0.65 = 65%).
    dscr : float
        Debt service coverage ratio (e.g., 1.25).
    rate_type : str
        'Fixed' or 'Floating'.
    all_in_rate : float
        All-in interest rate as decimal (e.g., 0.0575 = 5.75%).
    index : str
        Rate index (e.g., '10yr UST', '1M SOFR', 'Fixed').
    spread_bps : int
        Spread over index in basis points (0 for fixed all-in quotes).
    term_years : int
        Loan term in years.
    amortization_years : int
        Amortization period in years (0 = full IO).
    io_months : int
        Interest-only period in months (0 = none).
    prepayment_type : str
        One of VALID_PREPAY_TYPES.
    recourse : str
        One of VALID_RECOURSE_TYPES.
    origination_fee_pct : float
        Origination fee as decimal (e.g., 0.01 = 1.00%).
    est_close_days : int
        Estimated days from application to close.
    rate_lock_days : int
        Rate lock period in days.
    special_conditions : str
        Key conditions or covenants.
    quote_date : str
        ISO date string (YYYY-MM-DD).
    contact_name : str
        Primary lender contact for this quote.
    analyst_notes : str
        Internal analyst commentary on this quote.
    """

    def __init__(
        self,
        lender_name: str,
        lender_type: str,
        loan_amount: float,
        ltv: float,
        dscr: float,
        rate_type: str,
        all_in_rate: float,
        index: str = "Fixed",
        spread_bps: int = 0,
        term_years: int = 10,
        amortization_years: int = 30,
        io_months: int = 0,
        prepayment_type: str = "Defeasance",
        recourse: str = "Non_Recourse",
        origination_fee_pct: float = 0.01,
        est_close_days: int = 75,
        rate_lock_days: int = 30,
        special_conditions: str = "",
        quote_date: Optional[str] = None,
        contact_name: str = "",
        analyst_notes: str = "",
    ) -> None:
        self.lender_name = lender_name
        self.lender_type = lender_type
        self.loan_amount = loan_amount
        self.ltv = ltv
        self.dscr = dscr
        self.rate_type = rate_type
        self.all_in_rate = all_in_rate
        self.index = index
        self.spread_bps = spread_bps
        self.term_years = term_years
        self.amortization_years = amortization_years
        self.io_months = io_months
        self.prepayment_type = prepayment_type
        self.recourse = recourse
        self.origination_fee_pct = origination_fee_pct
        self.est_close_days = est_close_days
        self.rate_lock_days = rate_lock_days
        self.special_conditions = special_conditions
        self.quote_date = quote_date or datetime.today().strftime("%Y-%m-%d")
        self.contact_name = contact_name
        self.analyst_notes = analyst_notes

    def origination_fee_dollars(self) -> float:
        return self.loan_amount * self.origination_fee_pct

    def annual_debt_service(self) -> float:
        """
        Estimate annual debt service.
        During IO: interest only.
        After IO / no IO: standard mortgage payment.
        """
        r = self.all_in_rate / 12
        if self.amortization_years == 0 or (self.io_months >= self.term_years * 12):
            # Full IO
            return self.loan_amount * self.all_in_rate
        # Monthly payment (constant)
        n = self.amortization_years * 12
        if r == 0:
            monthly = self.loan_amount / n
        else:
            monthly = self.loan_amount * r * (1 + r) ** n / ((1 + r) ** n - 1)
        return monthly * 12

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lender_name": self.lender_name,
            "lender_type": self.lender_type,
            "loan_amount": self.loan_amount,
            "ltv_pct": round(self.ltv * 100, 2),
            "dscr": self.dscr,
            "rate_type": self.rate_type,
            "all_in_rate_pct": round(self.all_in_rate * 100, 3),
            "index": self.index,
            "spread_bps": self.spread_bps,
            "term_years": self.term_years,
            "amortization_years": self.amortization_years,
            "io_months": self.io_months,
            "prepayment_type": self.prepayment_type,
            "recourse": self.recourse,
            "origination_fee_pct": round(self.origination_fee_pct * 100, 3),
            "origination_fee_dollars": round(self.origination_fee_dollars()),
            "annual_debt_service_est": round(self.annual_debt_service()),
            "est_close_days": self.est_close_days,
            "rate_lock_days": self.rate_lock_days,
            "special_conditions": self.special_conditions,
            "quote_date": self.quote_date,
            "contact_name": self.contact_name,
            "analyst_notes": self.analyst_notes,
        }


# ---------------------------------------------------------------------------
# QuoteMatrixUpdater class
# ---------------------------------------------------------------------------


class QuoteMatrixUpdater:
    """
    Manages a collection of lender quotes for a single deal and generates
    a scored comparison matrix with recommendations.

    Parameters
    ----------
    deal_id : str
        Deal identifier.
    property_name : str
        Human-readable property name.
    requested_loan_amount : float
        Borrower's requested loan amount.
    appraised_value : float
        Appraised value of the collateral.
    noi : float
        Underwritten NOI used in the deal.
    """

    def __init__(
        self,
        deal_id: str,
        property_name: str,
        requested_loan_amount: float,
        appraised_value: float,
        noi: float,
    ) -> None:
        _ensure_dirs()
        self.deal_id = deal_id
        self.property_name = property_name
        self.requested_loan_amount = requested_loan_amount
        self.appraised_value = appraised_value
        self.noi = noi
        self._quotes: Dict[str, LenderQuote] = {}  # keyed by lender_name

    # ------------------------------------------------------------------
    # Quote Management
    # ------------------------------------------------------------------

    def add_quote(self, quote: LenderQuote) -> None:
        """
        Add a new lender quote.

        Parameters
        ----------
        quote : LenderQuote
            The quote object to add.

        Raises
        ------
        ValueError
            If a quote for this lender already exists. Use update_quote() instead.
        """
        if quote.lender_name in self._quotes:
            raise ValueError(
                f"Quote for '{quote.lender_name}' already exists. "
                "Use update_quote() to replace it."
            )
        self._quotes[quote.lender_name] = quote
        print(
            f"  ✓ Added quote: {quote.lender_name} ({quote.lender_type}) — "
            f"${quote.loan_amount:,.0f} @ {quote.all_in_rate*100:.2f}%  "
            f"[{quote.rate_type}, {quote.term_years}yr, {quote.io_months}mo IO]"
        )

    def update_quote(self, quote: LenderQuote) -> None:
        """
        Replace an existing quote with updated terms.

        Parameters
        ----------
        quote : LenderQuote
            Updated quote object (identified by lender_name).
        """
        if quote.lender_name not in self._quotes:
            print(f"  ℹ Quote for '{quote.lender_name}' not found — adding as new.")
        self._quotes[quote.lender_name] = quote
        print(
            f"  ✓ Updated quote: {quote.lender_name} — "
            f"${quote.loan_amount:,.0f} @ {quote.all_in_rate*100:.2f}%"
        )

    def remove_quote(self, lender_name: str) -> None:
        """Remove a quote by lender name."""
        if lender_name in self._quotes:
            del self._quotes[lender_name]
            print(f"  ✓ Removed quote: {lender_name}")

    def get_quotes(self) -> List[LenderQuote]:
        """Return all quotes as a list, sorted by lender name."""
        return sorted(self._quotes.values(), key=lambda q: q.lender_name)

    # ------------------------------------------------------------------
    # Scoring Engine
    # ------------------------------------------------------------------

    def _score_quotes(self) -> List[Tuple[LenderQuote, Dict[str, float], float]]:
        """
        Score all quotes across four dimensions and return ranked results.

        Scoring weights:
          Rate (30%) — lower all-in rate scores higher
          Proceeds (30%) — higher loan amount scores higher
          Loan Terms (20%) — IO period, non-recourse, favorable prepay
          Execution (20%) — faster close, longer rate lock

        Returns
        -------
        list of (LenderQuote, component_scores_dict, total_score) tuples,
        sorted descending by total_score.
        """
        quotes = self.get_quotes()
        if not quotes:
            return []

        # Extract raw values for normalization
        rates = [q.all_in_rate for q in quotes]
        amounts = [q.loan_amount for q in quotes]
        close_days = [q.est_close_days for q in quotes]

        min_rate, max_rate = min(rates), max(rates)
        min_amt, max_amt = min(amounts), max(amounts)
        min_days, max_days = min(close_days), max(close_days)

        results = []
        for q in quotes:

            # --- Rate Score (30 pts): lower rate → higher score ---
            if max_rate == min_rate:
                rate_score = 30.0
            else:
                rate_score = 30.0 * (max_rate - q.all_in_rate) / (max_rate - min_rate)

            # --- Proceeds Score (30 pts): higher amount → higher score ---
            if max_amt == min_amt:
                proceeds_score = 30.0
            else:
                proceeds_score = 30.0 * (q.loan_amount - min_amt) / (max_amt - min_amt)

            # --- Terms Score (20 pts): IO, non-recourse, prepay ---
            terms_score = 0.0
            # IO period: up to 8 pts (5yr IO = full 8 pts; 0 IO = 0 pts)
            io_yrs = q.io_months / 12
            terms_score += min(io_yrs / 5 * 8, 8.0)
            # Non-recourse: 7 pts
            if q.recourse == "Non_Recourse":
                terms_score += 7.0
            elif q.recourse == "Limited_Recourse":
                terms_score += 3.5
            # Prepayment flexibility: up to 5 pts
            prepay_scores = {
                "Open": 5.0,
                "Open_After_Lockout": 4.0,
                "Stepdown": 3.0,
                "Yield_Maintenance": 1.5,
                "Defeasance": 1.0,
                "None": 0.0,
            }
            terms_score += prepay_scores.get(q.prepayment_type, 0.0)

            # --- Execution Score (20 pts): speed + rate lock ---
            if max_days == min_days:
                speed_score = 12.0
            else:
                # Faster close → higher score (invert)
                speed_score = 12.0 * (max_days - q.est_close_days) / (max_days - min_days)
            lock_score = min(q.rate_lock_days / 60 * 8, 8.0)
            execution_score = speed_score + lock_score

            total = rate_score + proceeds_score + terms_score + execution_score

            component = {
                "rate_score": round(rate_score, 2),
                "proceeds_score": round(proceeds_score, 2),
                "terms_score": round(terms_score, 2),
                "execution_score": round(execution_score, 2),
                "total": round(total, 2),
            }
            results.append((q, component, round(total, 2)))

        # Sort descending by total score
        results.sort(key=lambda x: -x[2])
        return results

    # ------------------------------------------------------------------
    # Category Leaders
    # ------------------------------------------------------------------

    def _find_category_leaders(self) -> Dict[str, Optional[LenderQuote]]:
        """
        Identify the best quote in each key category.

        Returns
        -------
        dict
            Keys: 'best_rate', 'highest_proceeds', 'most_io', 'fastest_close',
                  'best_terms', 'best_overall'
        """
        quotes = self.get_quotes()
        if not quotes:
            return {}

        scored = self._score_quotes()

        return {
            "best_overall": scored[0][0] if scored else None,
            "best_rate": min(quotes, key=lambda q: q.all_in_rate),
            "highest_proceeds": max(quotes, key=lambda q: q.loan_amount),
            "most_io": max(quotes, key=lambda q: q.io_months),
            "fastest_close": min(quotes, key=lambda q: q.est_close_days),
            "best_terms": max(
                quotes,
                key=lambda q: (
                    q.io_months / 12
                    + (7 if q.recourse == "Non_Recourse" else 0)
                    + {"Open": 5, "Stepdown": 3, "Yield_Maintenance": 1, "Defeasance": 1}.get(
                        q.prepayment_type, 0
                    )
                ),
            ),
        }

    # ------------------------------------------------------------------
    # Matrix Generation
    # ------------------------------------------------------------------

    def generate_matrix(self) -> str:
        """
        Build and return a formatted quote comparison matrix as a string.

        Returns
        -------
        str
            Multi-line formatted matrix.
        """
        quotes = self.get_quotes()
        if not quotes:
            return "  ⚠ No quotes to display."

        scored = self._score_quotes()
        leaders = self._find_category_leaders()
        sep = "=" * 80
        thin = "-" * 80
        lines: List[str] = []

        lines.append(sep)
        lines.append(f"  LENDER QUOTE COMPARISON MATRIX")
        lines.append(f"  Deal: {self.deal_id} — {self.property_name}")
        lines.append(
            f"  Requested: ${self.requested_loan_amount:,.0f}  |  "
            f"Appraised Value: ${self.appraised_value:,.0f}  |  "
            f"NOI: ${self.noi:,.0f}"
        )
        lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
                     f"Quotes: {len(quotes)}")
        lines.append(sep)
        lines.append("")

        # ---------- Matrix table ----------
        col_w = max(18, max(len(q.lender_name) for q in quotes) + 2)
        header_col = 28

        # Determine column labels
        lender_labels = [q.lender_name for q in quotes]
        category_labels = [
            ("best_overall", "★ OVERALL"),
            ("best_rate", "★ RATE"),
            ("highest_proceeds", "★ PROCEEDS"),
            ("most_io", "★ IO"),
            ("fastest_close", "★ SPEED"),
        ]

        # Build star row
        def stars(q: LenderQuote) -> str:
            s = []
            for cat, label in category_labels:
                ldr = leaders.get(cat)
                if ldr and ldr.lender_name == q.lender_name:
                    s.append(label)
            return " | ".join(s) if s else ""

        def fmt_row(label: str, values: List[str]) -> str:
            row = f"  {label:<{header_col}}"
            for v in values:
                row += f"  {v:>{col_w}}"
            return row

        lines.append(fmt_row("", lender_labels))
        lines.append(fmt_row("Lender Type", [q.lender_type for q in quotes]))
        lines.append("  " + thin)
        lines.append(fmt_row("AWARDS", [stars(q) or "—" for q in quotes]))
        lines.append("  " + thin)

        # Loan economics
        lines.append(fmt_row("Loan Amount",
            [f"${q.loan_amount:,.0f}" for q in quotes]))
        lines.append(fmt_row("LTV",
            [f"{q.ltv*100:.1f}%" for q in quotes]))
        lines.append(fmt_row("DSCR (Underwritten)",
            [f"{q.dscr:.2f}x" for q in quotes]))
        lines.append(fmt_row("Debt Yield (NOI/Loan)",
            [f"{(self.noi/q.loan_amount)*100:.2f}%" for q in quotes]))
        lines.append("  " + thin)

        # Rate
        lines.append(fmt_row("Rate Type",
            [q.rate_type for q in quotes]))
        lines.append(fmt_row("All-In Rate",
            [f"{q.all_in_rate*100:.3f}%" for q in quotes]))
        lines.append(fmt_row("Index",
            [q.index for q in quotes]))
        lines.append(fmt_row("Spread (bps)",
            [f"+{q.spread_bps}bps" if q.spread_bps else "N/A" for q in quotes]))
        lines.append("  " + thin)

        # Structure
        lines.append(fmt_row("Term",
            [f"{q.term_years}yr" for q in quotes]))
        lines.append(fmt_row("Amortization",
            [f"{q.amortization_years}yr" if q.amortization_years > 0 else "IO" for q in quotes]))
        lines.append(fmt_row("IO Period",
            [f"{q.io_months}mo" if q.io_months > 0 else "None" for q in quotes]))
        lines.append(fmt_row("Annual Debt Service",
            [f"${q.annual_debt_service():,.0f}" for q in quotes]))
        lines.append("  " + thin)

        # Prepay & recourse
        lines.append(fmt_row("Prepayment",
            [q.prepayment_type.replace("_", " ") for q in quotes]))
        lines.append(fmt_row("Recourse",
            [q.recourse.replace("_", " ") for q in quotes]))
        lines.append("  " + thin)

        # Fees & execution
        lines.append(fmt_row("Origination Fee",
            [f"{q.origination_fee_pct*100:.2f}% (${q.origination_fee_dollars():,.0f})" for q in quotes]))
        lines.append(fmt_row("Rate Lock",
            [f"{q.rate_lock_days}d" for q in quotes]))
        lines.append(fmt_row("Est. Close Timeline",
            [f"{q.est_close_days} days" for q in quotes]))
        lines.append("  " + thin)

        # Scores
        score_map = {q.lender_name: (comp, total) for (q, comp, total) in scored}
        lines.append(fmt_row("Rate Score (30 max)",
            [f"{score_map[q.lender_name][0]['rate_score']:.1f}" for q in quotes]))
        lines.append(fmt_row("Proceeds Score (30 max)",
            [f"{score_map[q.lender_name][0]['proceeds_score']:.1f}" for q in quotes]))
        lines.append(fmt_row("Terms Score (20 max)",
            [f"{score_map[q.lender_name][0]['terms_score']:.1f}" for q in quotes]))
        lines.append(fmt_row("Execution Score (20 max)",
            [f"{score_map[q.lender_name][0]['execution_score']:.1f}" for q in quotes]))
        lines.append("  " + thin)
        lines.append(fmt_row("TOTAL SCORE (100 max)",
            [f"{score_map[q.lender_name][1]:.1f}" for q in quotes]))
        lines.append("  " + thin)
        lines.append(fmt_row("RANK",
            [
                f"#{next(i+1 for i, (sq, _, _) in enumerate(scored) if sq.lender_name == q.lender_name)}"
                for q in quotes
            ]))
        lines.append("")

        # Special conditions
        has_conditions = any(q.special_conditions for q in quotes)
        if has_conditions:
            lines.append("  SPECIAL CONDITIONS / KEY COVENANTS")
            lines.append("  " + thin)
            for q in quotes:
                if q.special_conditions:
                    lines.append(f"  {q.lender_name}: {q.special_conditions}")
            lines.append("")

        return "\n".join(lines)

    def generate_recommendation(self) -> str:
        """
        Generate a written recommendation summary based on scoring results.

        Returns
        -------
        str
            Multi-line recommendation text.
        """
        scored = self._score_quotes()
        leaders = self._find_category_leaders()

        if not scored:
            return "  No quotes available to generate recommendation."

        top_quote, top_components, top_score = scored[0]
        lines: List[str] = []
        sep = "=" * 80
        thin = "-" * 80

        lines.append(sep)
        lines.append("  ANALYST RECOMMENDATION")
        lines.append(sep)
        lines.append("")

        lines.append("  RECOMMENDED: " + top_quote.lender_name.upper())
        lines.append(f"  Overall Score: {top_score:.1f} / 100")
        lines.append(f"  Loan Amount:   ${top_quote.loan_amount:,.0f}")
        lines.append(f"  Rate:          {top_quote.all_in_rate*100:.3f}% ({top_quote.rate_type})")
        lines.append(
            f"  Structure:     {top_quote.term_years}yr term, "
            f"{top_quote.amortization_years}yr am, "
            f"{top_quote.io_months}mo IO"
        )
        lines.append(f"  Prepayment:    {top_quote.prepayment_type.replace('_', ' ')}")
        lines.append(f"  Recourse:      {top_quote.recourse.replace('_', ' ')}")
        lines.append(f"  Close Est.:    {top_quote.est_close_days} days")
        lines.append("")

        lines.append("  RATIONALE")
        lines.append("  " + thin)

        # Build narrative
        narrative_items = []
        if leaders["best_rate"] and leaders["best_rate"].lender_name == top_quote.lender_name:
            narrative_items.append(
                f"Best available rate at {top_quote.all_in_rate*100:.3f}% — lowest of all {len(scored)} quotes received."
            )
        else:
            best_rate_lender = leaders.get("best_rate")
            if best_rate_lender:
                rate_diff_bps = round((top_quote.all_in_rate - best_rate_lender.all_in_rate) * 10000)
                narrative_items.append(
                    f"Rate of {top_quote.all_in_rate*100:.3f}% is {rate_diff_bps}bps above the lowest available "
                    f"({best_rate_lender.lender_name} at {best_rate_lender.all_in_rate*100:.3f}%), "
                    f"offset by superior proceeds and terms."
                )

        if leaders["highest_proceeds"] and leaders["highest_proceeds"].lender_name == top_quote.lender_name:
            narrative_items.append(
                f"Highest proceeds at ${top_quote.loan_amount:,.0f} — meets the full borrower request."
            )

        if top_quote.io_months > 0:
            io_yrs = top_quote.io_months // 12
            io_mos = top_quote.io_months % 12
            io_str = f"{io_yrs}yr" if io_mos == 0 else f"{io_yrs}yr {io_mos}mo"
            annual_io_ds = top_quote.loan_amount * top_quote.all_in_rate
            annual_am_ds = top_quote.annual_debt_service()
            annual_savings = annual_am_ds - annual_io_ds
            narrative_items.append(
                f"{io_str} IO period reduces annual debt service by approximately "
                f"${annual_savings:,.0f} vs. fully amortizing — improving near-term cash flow."
            )

        if top_quote.recourse == "Non_Recourse":
            narrative_items.append(
                "Non-recourse structure with standard bad-boy carve-outs protects the guarantor's "
                "personal balance sheet and preserves borrowing capacity."
            )

        if top_quote.prepayment_type in ("Open", "Stepdown", "Open_After_Lockout"):
            narrative_items.append(
                f"{top_quote.prepayment_type.replace('_', ' ')} prepayment provides flexibility "
                "for refinancing or sale ahead of maturity if market conditions warrant."
            )

        for item in narrative_items:
            lines.append(f"  • {item}")

        lines.append("")
        lines.append("  CATEGORY LEADERS SUMMARY")
        lines.append("  " + thin)

        category_display = [
            ("best_overall", "Best Overall (Weighted Score)"),
            ("best_rate", "Best Rate"),
            ("highest_proceeds", "Highest Proceeds"),
            ("most_io", "Longest IO Period"),
            ("fastest_close", "Fastest Execution"),
        ]
        for cat_key, cat_label in category_display:
            ldr = leaders.get(cat_key)
            if ldr:
                if cat_key == "best_rate":
                    detail = f"{ldr.all_in_rate*100:.3f}%"
                elif cat_key == "highest_proceeds":
                    detail = f"${ldr.loan_amount:,.0f}"
                elif cat_key == "most_io":
                    detail = f"{ldr.io_months} months"
                elif cat_key == "fastest_close":
                    detail = f"{ldr.est_close_days} days"
                else:
                    detail = f"Score: {next((t for sq, _, t in scored if sq.lender_name == ldr.lender_name), 0):.1f}"
                lines.append(f"  {cat_label:<35} {ldr.lender_name:<30}  {detail}")

        lines.append("")
        lines.append("  FULL SCORE RANKING")
        lines.append("  " + thin)
        lines.append(
            f"  {'Rank':<5} {'Lender':<30} {'Rate':>8}  {'Proceeds':>13}  {'Rate Sc':>8}  "
            f"{'Proc Sc':>8}  {'Term Sc':>8}  {'Exec Sc':>8}  {'TOTAL':>8}"
        )
        lines.append("  " + "-" * 100)
        for rank, (q, comp, total) in enumerate(scored, 1):
            lines.append(
                f"  {rank:<5} {q.lender_name[:29]:<30} "
                f"{q.all_in_rate*100:>7.3f}%  ${q.loan_amount:>11,.0f}  "
                f"{comp['rate_score']:>8.1f}  {comp['proceeds_score']:>8.1f}  "
                f"{comp['terms_score']:>8.1f}  {comp['execution_score']:>8.1f}  "
                f"{total:>8.1f}"
            )

        lines.append("")
        lines.append(sep)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_to_csv(self, filename: Optional[str] = None) -> str:
        """
        Export all quotes (with scores) to a CSV file.

        Parameters
        ----------
        filename : str, optional
            Output path. Defaults to output/quote_matrix_{deal_id}_{ts}.csv.

        Returns
        -------
        str
            Absolute path to the created CSV file.
        """
        _ensure_dirs()
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_id = self.deal_id.replace("/", "-")
            filename = os.path.join(OUTPUT_DIR, f"quote_matrix_{safe_id}_{ts}.csv")

        scored = self._score_quotes()
        if not scored:
            print("  ⚠ No quotes to export.")
            return filename

        rows = []
        for rank, (q, comp, total) in enumerate(scored, 1):
            row = {
                "rank": rank,
                "deal_id": self.deal_id,
                "property_name": self.property_name,
                **q.to_dict(),
                "score_rate": comp["rate_score"],
                "score_proceeds": comp["proceeds_score"],
                "score_terms": comp["terms_score"],
                "score_execution": comp["execution_score"],
                "score_total": total,
            }
            rows.append(row)

        fieldnames = list(rows[0].keys())
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"  ✓ Quote matrix exported: {filename}  ({len(rows)} quotes)")
        return filename

    def to_json(self) -> str:
        """Return all quotes and scores as a JSON string."""
        scored = self._score_quotes()
        output = {
            "deal_id": self.deal_id,
            "property_name": self.property_name,
            "requested_loan_amount": self.requested_loan_amount,
            "appraised_value": self.appraised_value,
            "noi": self.noi,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "quotes": [
                {**q.to_dict(), "scores": comp, "rank": rank}
                for rank, (q, comp, total) in enumerate(scored, 1)
            ],
        }
        return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# Demo — 4 Sample Quotes
# ---------------------------------------------------------------------------

def _run_demo() -> None:
    """Demo: 4 quotes for a NYC multifamily refinance."""

    print("\n" + "=" * 80)
    print("  QUOTE MATRIX UPDATER — DEMO MODE")
    print("  Deal: NYC-2024-003 — The Waverly Apartments, New York NY")
    print("=" * 80 + "\n")

    matrix = QuoteMatrixUpdater(
        deal_id="NYC-2024-003",
        property_name="The Waverly Apartments",
        requested_loan_amount=18_500_000,
        appraised_value=28_400_000,
        noi=1_250_000,
    )

    print("  Adding quotes...\n")

    # Quote 1: Agency (Fannie Mae DUS) — Best rate, strong IO
    matrix.add_quote(LenderQuote(
        lender_name="Berkadia (Fannie DUS)",
        lender_type="Agency_GSE",
        loan_amount=18_500_000,
        ltv=0.651,
        dscr=1.38,
        rate_type="Fixed",
        all_in_rate=0.0572,
        index="10yr UST",
        spread_bps=152,
        term_years=10,
        amortization_years=30,
        io_months=24,
        prepayment_type="Yield_Maintenance",
        recourse="Non_Recourse",
        origination_fee_pct=0.0075,
        est_close_days=110,
        rate_lock_days=60,
        special_conditions="Green MBS pricing available (+10bps NOI credit if property passes Fannie Green assessment). PCNA required within 6 months.",
        contact_name="Maria Gonzalez, Senior Director",
        analyst_notes="Best rate of the group. Close timeline is longest (Agency process) — must coordinate with rate lock.",
        quote_date=datetime.today().strftime("%Y-%m-%d"),
    ))

    # Quote 2: Life Company — Good rate, less IO, higher bar
    matrix.add_quote(LenderQuote(
        lender_name="PGIM Real Estate Finance",
        lender_type="Life_Company",
        loan_amount=17_900_000,
        ltv=0.630,
        dscr=1.42,
        rate_type="Fixed",
        all_in_rate=0.0591,
        index="10yr UST",
        spread_bps=171,
        term_years=10,
        amortization_years=30,
        io_months=12,
        prepayment_type="Defeasance",
        recourse="Non_Recourse",
        origination_fee_pct=0.005,
        est_close_days=80,
        rate_lock_days=30,
        special_conditions="Requires Phase I dated within 12 months. Minimum guarantor net worth $5M. No secondary financing permitted.",
        contact_name="Alexandra Torres, Director",
        analyst_notes="Reliable life company execution. $600K less proceeds than Agency. Faster close than Agency.",
        quote_date=datetime.today().strftime("%Y-%m-%d"),
    ))

    # Quote 3: Regional Bank — Recourse, moderate rate, fast close
    matrix.add_quote(LenderQuote(
        lender_name="Signature RE Lending Group",
        lender_type="Regional_Bank",
        loan_amount=18_500_000,
        ltv=0.651,
        dscr=1.35,
        rate_type="Floating",
        all_in_rate=0.0620,
        index="1M SOFR",
        spread_bps=220,
        term_years=5,
        amortization_years=25,
        io_months=0,
        prepayment_type="Stepdown",
        recourse="Full_Recourse",
        origination_fee_pct=0.01,
        est_close_days=50,
        rate_lock_days=30,
        special_conditions="Personal guarantee required from all principals >20% ownership. Springing cash management at DSCR <1.20x. Rate cap not required.",
        contact_name="Tom Bradley, VP",
        analyst_notes="Fastest close option. Full recourse is the major drawback — borrower expressed preference for NR. Floating rate adds index risk.",
        quote_date=datetime.today().strftime("%Y-%m-%d"),
    ))

    # Quote 4: Debt Fund — Full IO, flexible, expensive
    matrix.add_quote(LenderQuote(
        lender_name="Mesa West Capital",
        lender_type="Debt_Fund",
        loan_amount=19_200_000,
        ltv=0.676,
        dscr=1.28,
        rate_type="Floating",
        all_in_rate=0.0735,
        index="1M SOFR",
        spread_bps=335,
        term_years=3,
        amortization_years=0,
        io_months=36,
        prepayment_type="Open_After_Lockout",
        recourse="Non_Recourse",
        origination_fee_pct=0.015,
        est_close_days=35,
        rate_lock_days=0,
        special_conditions="Rate cap required — interest rate cap at SOFR + 5.50%, estimated cost $85,000. Open prepayment after 12-month lockout. 1-year extension option at SOFR+345bps if DSCR ≥1.15x.",
        contact_name="Linda Kim, Director",
        analyst_notes="Highest proceeds ($700K above next best). Full IO significantly improves Year 1 cash flow. Most expensive by rate. Bridge lender — best fit if borrower plans to sell or refi within 3 years.",
        quote_date=datetime.today().strftime("%Y-%m-%d"),
    ))

    print()

    # Print matrix
    print(matrix.generate_matrix())

    # Print recommendation
    print(matrix.generate_recommendation())

    # Export CSV
    print("  --- EXPORTING TO CSV ---\n")
    csv_path = matrix.export_to_csv()

    # Print JSON summary
    print("\n  --- JSON SUMMARY (first 800 chars) ---\n")
    json_str = matrix.to_json()
    print("  " + json_str[:800].replace("\n", "\n  ") + "...\n")

    print(f"  CSV exported to: {csv_path}")
    print("\n" + "=" * 80)
    print("  Demo complete.")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    _run_demo()
