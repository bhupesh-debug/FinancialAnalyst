"""
lender_outreach.py
==================
CRE Capital Markets — Lender Outreach Management System

Tracks lender database, outreach activity, responses, and deal-lender matching
for a commercial mortgage brokerage deal team.

Usage:
    python lender_outreach.py                    # Run demo
    from lender_outreach import LenderOutreach   # Import as module

Author: [Analyst Name]
Version: 1.0
"""

import sqlite3
import csv
import os
import json
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_RESPONSE_STATUSES = [
    "Pending",
    "Interested",
    "Passed",
    "Quote_Received",
    "Declined",
]

VALID_LENDER_TYPES = [
    "Life_Company",
    "Agency_GSE",
    "CMBS",
    "National_Bank",
    "Regional_Bank",
    "Community_Bank",
    "Debt_Fund",
    "Bridge_Lender",
    "Mezzanine",
    "Construction",
    "CreditUnion",
    "Other",
]

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_THIS_DIR, "..", "data")
OUTPUT_DIR = os.path.join(_THIS_DIR, "..", "output")
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "lenders.db")


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# LenderOutreach class
# ---------------------------------------------------------------------------


class LenderOutreach:
    """
    SQLite-backed lender relationship and outreach tracker.

    Maintains a database of lenders and their deal-level outreach activity.
    Provides matching logic to identify the best lenders for a given deal.

    Parameters
    ----------
    db_path : str, optional
        Path to SQLite database. Defaults to data/lenders.db.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        _ensure_dirs()
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_tables()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        """Create lenders and outreach tables."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lenders (
                lender_id            INTEGER PRIMARY KEY AUTOINCREMENT,
                lender_name          TEXT NOT NULL UNIQUE,
                lender_type          TEXT NOT NULL,
                contact_name         TEXT,
                email                TEXT,
                phone                TEXT,
                property_type_focus  TEXT,
                geography_focus      TEXT,
                min_loan             REAL DEFAULT 0,
                max_loan             REAL DEFAULT 999999999,
                preferred_ltv_max    REAL DEFAULT 0.75,
                preferred_dscr_min   REAL DEFAULT 1.20,
                active               INTEGER DEFAULT 1,
                notes                TEXT,
                last_contacted       TEXT
            );

            CREATE TABLE IF NOT EXISTS outreach (
                outreach_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id          TEXT NOT NULL,
                lender_id        INTEGER NOT NULL,
                date_sent        TEXT NOT NULL,
                response_status  TEXT NOT NULL DEFAULT 'Pending',
                quote_amount     REAL,
                quoted_rate      REAL,
                quoted_terms     TEXT,
                follow_up_date   TEXT,
                analyst_notes    TEXT,
                last_updated     TEXT NOT NULL,
                UNIQUE(deal_id, lender_id)
            );
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Lender CRUD
    # ------------------------------------------------------------------

    def add_lender(
        self,
        lender_name: str,
        lender_type: str,
        contact_name: str = "",
        email: str = "",
        phone: str = "",
        property_type_focus: str = "",
        geography_focus: str = "",
        min_loan: float = 1_000_000,
        max_loan: float = 200_000_000,
        preferred_ltv_max: float = 0.75,
        preferred_dscr_min: float = 1.25,
        notes: str = "",
    ) -> int:
        """
        Add a lender to the database.

        Parameters
        ----------
        lender_name : str
            Full lender name (must be unique).
        lender_type : str
            One of VALID_LENDER_TYPES.
        contact_name : str
            Primary contact at the lender (name + title).
        email : str
            Primary contact email.
        phone : str
            Primary contact phone.
        property_type_focus : str
            Comma-delimited list of preferred property types.
        geography_focus : str
            Geographic markets the lender actively lends in.
        min_loan : float
            Minimum loan amount in USD.
        max_loan : float
            Maximum loan amount in USD.
        preferred_ltv_max : float
            Maximum LTV ratio (e.g., 0.75 = 75%).
        preferred_dscr_min : float
            Minimum DSCR required (e.g., 1.25).
        notes : str
            Relationship notes, preferences, hot buttons.

        Returns
        -------
        int
            The lender_id of the inserted record.
        """
        if lender_type not in VALID_LENDER_TYPES:
            raise ValueError(
                f"Invalid lender_type '{lender_type}'. Must be one of: {VALID_LENDER_TYPES}"
            )
        cursor = self._conn.execute(
            """
            INSERT INTO lenders (
                lender_name, lender_type, contact_name, email, phone,
                property_type_focus, geography_focus, min_loan, max_loan,
                preferred_ltv_max, preferred_dscr_min, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lender_name, lender_type, contact_name, email, phone,
                property_type_focus, geography_focus, min_loan, max_loan,
                preferred_ltv_max, preferred_dscr_min, notes,
            ),
        )
        self._conn.commit()
        lender_id = cursor.lastrowid
        print(
            f"  ✓ Added lender: [{lender_id}] {lender_name} ({lender_type}) — "
            f"${min_loan/1e6:.1f}M–${max_loan/1e6:.0f}M | Max LTV: {preferred_ltv_max*100:.0f}%"
        )
        return lender_id  # type: ignore[return-value]

    def get_lender(self, lender_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a lender by ID."""
        row = self._conn.execute(
            "SELECT * FROM lenders WHERE lender_id = ?", (lender_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_lenders(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Return all lenders, optionally filtered to active only."""
        query = "SELECT * FROM lenders"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY lender_type, lender_name"
        return [dict(r) for r in self._conn.execute(query).fetchall()]

    # ------------------------------------------------------------------
    # Outreach Tracking
    # ------------------------------------------------------------------

    def log_outreach(
        self,
        deal_id: str,
        lender_id: int,
        response_status: str = "Pending",
        quote_amount: Optional[float] = None,
        quoted_rate: Optional[float] = None,
        quoted_terms: Optional[str] = None,
        follow_up_date: Optional[str] = None,
        analyst_notes: str = "",
        date_sent: Optional[str] = None,
    ) -> None:
        """
        Log or update an outreach record for a deal-lender pair.

        If an outreach record already exists for this (deal_id, lender_id)
        pair, the record is updated rather than duplicated.

        Parameters
        ----------
        deal_id : str
            Deal identifier matching the deals tracker.
        lender_id : int
            Lender ID from the lenders table.
        response_status : str
            One of VALID_RESPONSE_STATUSES.
        quote_amount : float, optional
            Dollar amount of the lender's quote.
        quoted_rate : float, optional
            Quoted interest rate as a decimal (e.g., 0.0625 = 6.25%).
        quoted_terms : str, optional
            Brief description of quoted terms (e.g., '10yr fixed, 30yr am, 2yr IO').
        follow_up_date : str, optional
            ISO date for next follow-up (YYYY-MM-DD).
        analyst_notes : str
            Notes from the outreach conversation.
        date_sent : str, optional
            ISO date OM was sent. Defaults to today.
        """
        if response_status not in VALID_RESPONSE_STATUSES:
            raise ValueError(
                f"Invalid status '{response_status}'. Must be one of: {VALID_RESPONSE_STATUSES}"
            )
        now = datetime.now().isoformat(timespec="seconds")
        sent = date_sent or date.today().isoformat()

        # Upsert: update if exists, insert if new
        existing = self._conn.execute(
            "SELECT outreach_id FROM outreach WHERE deal_id=? AND lender_id=?",
            (deal_id, lender_id),
        ).fetchone()

        lender = self.get_lender(lender_id)
        lender_name = lender["lender_name"] if lender else f"Lender #{lender_id}"

        if existing:
            self._conn.execute(
                """
                UPDATE outreach SET
                    response_status=?, quote_amount=?, quoted_rate=?,
                    quoted_terms=?, follow_up_date=?, analyst_notes=?, last_updated=?
                WHERE deal_id=? AND lender_id=?
                """,
                (
                    response_status, quote_amount, quoted_rate,
                    quoted_terms, follow_up_date, analyst_notes, now,
                    deal_id, lender_id,
                ),
            )
            action = "Updated"
        else:
            self._conn.execute(
                """
                INSERT INTO outreach (
                    deal_id, lender_id, date_sent, response_status,
                    quote_amount, quoted_rate, quoted_terms, follow_up_date,
                    analyst_notes, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deal_id, lender_id, sent, response_status,
                    quote_amount, quoted_rate, quoted_terms, follow_up_date,
                    analyst_notes, now,
                ),
            )
            action = "Logged"

        # Update lender's last_contacted date
        self._conn.execute(
            "UPDATE lenders SET last_contacted=? WHERE lender_id=?",
            (now, lender_id),
        )
        self._conn.commit()

        rate_str = f" @ {quoted_rate*100:.2f}%" if quoted_rate else ""
        amt_str = f" ${quote_amount:,.0f}" if quote_amount else ""
        print(
            f"  ✓ {action} outreach: [{deal_id}] → {lender_name} | "
            f"{response_status}{amt_str}{rate_str}"
        )

    # ------------------------------------------------------------------
    # Lender Matching
    # ------------------------------------------------------------------

    def get_best_lenders_for_deal(
        self,
        loan_amount: float,
        property_type: str,
        geography: str = "",
        ltv: float = 0.65,
        dscr: float = 1.30,
        top_n: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Return ranked list of lenders that match the deal criteria.

        Matching criteria (all must pass):
        - Loan amount between lender min and max
        - LTV does not exceed lender preferred_ltv_max
        - DSCR meets or exceeds lender preferred_dscr_min
        - Property type appears in lender's property_type_focus (or lender has no restriction)
        - Geography match (optional — used as a bonus score)

        Ranking is by lender type preference (Life Co and Agency first for
        stabilized deals) and then alphabetically.

        Parameters
        ----------
        loan_amount : float
            Requested loan amount in USD.
        property_type : str
            Property type of the subject (e.g., 'Multifamily').
        geography : str
            Market / city for geographic preference matching.
        ltv : float
            Deal LTV ratio as a decimal.
        dscr : float
            Deal DSCR.
        top_n : int
            Maximum number of lenders to return.

        Returns
        -------
        list of dict
            Ranked lenders with a 'match_score' field (0–100).
        """
        all_lenders = self.list_lenders(active_only=True)
        candidates = []

        for lender in all_lenders:
            # Hard filters
            if loan_amount < lender["min_loan"]:
                continue
            if loan_amount > lender["max_loan"]:
                continue
            if ltv > lender["preferred_ltv_max"]:
                continue
            if dscr < lender["preferred_dscr_min"]:
                continue

            # Property type soft filter
            pt_focus = (lender["property_type_focus"] or "").lower()
            pt_match = (
                not pt_focus
                or property_type.lower() in pt_focus
                or "all" in pt_focus
            )
            if not pt_match:
                continue

            # Scoring
            score = 60  # base pass score

            # Geography bonus
            geo_focus = (lender["geography_focus"] or "").lower()
            if geography and geo_focus and geography.lower() in geo_focus:
                score += 15
            elif not geo_focus:
                score += 5  # national lender — slight bonus

            # Lender type preference for stabilized assets
            preferred_types_for_perm = {
                "Life_Company": 20,
                "Agency_GSE": 18,
                "CMBS": 12,
                "National_Bank": 10,
                "Regional_Bank": 8,
            }
            score += preferred_types_for_perm.get(lender["lender_type"], 0)

            # LTV headroom bonus (more conservative = more willing)
            ltv_headroom = lender["preferred_ltv_max"] - ltv
            if ltv_headroom > 0.10:
                score += 5

            candidates.append({**lender, "match_score": min(score, 100)})

        # Sort by match_score descending, then lender_name
        candidates.sort(key=lambda x: (-x["match_score"], x["lender_name"]))
        return candidates[:top_n]

    # ------------------------------------------------------------------
    # Response Summary & Reporting
    # ------------------------------------------------------------------

    def get_response_summary(self, deal_id: str) -> Dict[str, Any]:
        """
        Return a summary of outreach responses for a specific deal.

        Parameters
        ----------
        deal_id : str
            Deal to summarize.

        Returns
        -------
        dict
            Response counts, best quote, and per-status detail.
        """
        rows = self._conn.execute(
            """
            SELECT o.*, l.lender_name, l.lender_type
            FROM outreach o
            JOIN lenders l ON o.lender_id = l.lender_id
            WHERE o.deal_id = ?
            ORDER BY o.response_status, l.lender_name
            """,
            (deal_id,),
        ).fetchall()

        records = [dict(r) for r in rows]
        by_status: Dict[str, List[Dict]] = {}
        for status in VALID_RESPONSE_STATUSES:
            by_status[status] = [r for r in records if r["response_status"] == status]

        # Best quote by rate
        quotes = [r for r in records if r.get("quoted_rate")]
        best_quote = None
        if quotes:
            best_quote = min(quotes, key=lambda x: x["quoted_rate"])  # type: ignore

        # Best quote by amount
        quote_amounts = [r for r in records if r.get("quote_amount")]
        highest_proceeds = None
        if quote_amounts:
            highest_proceeds = max(quote_amounts, key=lambda x: x["quote_amount"])  # type: ignore

        # Pending follow-ups due today or earlier
        today_iso = date.today().isoformat()
        overdue_followups = [
            r for r in records
            if r.get("follow_up_date") and r["follow_up_date"] <= today_iso
            and r["response_status"] == "Pending"
        ]

        return {
            "deal_id": deal_id,
            "total_lenders_contacted": len(records),
            "by_status": {k: len(v) for k, v in by_status.items()},
            "response_rate_pct": (
                round(
                    (len(records) - len(by_status["Pending"])) / len(records) * 100, 1
                )
                if records
                else 0
            ),
            "best_rate_quote": {
                "lender": best_quote["lender_name"],
                "rate_pct": round(best_quote["quoted_rate"] * 100, 3),  # type: ignore
                "terms": best_quote.get("quoted_terms"),
            }
            if best_quote
            else None,
            "highest_proceeds_quote": {
                "lender": highest_proceeds["lender_name"],
                "amount": highest_proceeds["quote_amount"],
                "rate_pct": round(highest_proceeds["quoted_rate"] * 100, 3)
                if highest_proceeds.get("quoted_rate")
                else None,
            }
            if highest_proceeds
            else None,
            "overdue_followups": [
                {"lender": r["lender_name"], "due": r["follow_up_date"]}
                for r in overdue_followups
            ],
            "detail": records,
        }

    def generate_outreach_report(self, deal_id: Optional[str] = None) -> str:
        """
        Generate a formatted outreach status report.

        Parameters
        ----------
        deal_id : str, optional
            If provided, report is scoped to that deal only.
            If None, reports all deals with any outreach.

        Returns
        -------
        str
            Multi-line formatted report string.
        """
        sep = "=" * 72
        thin = "-" * 72
        lines: List[str] = []

        # Get distinct deal IDs
        if deal_id:
            deal_ids = [deal_id]
        else:
            rows = self._conn.execute(
                "SELECT DISTINCT deal_id FROM outreach ORDER BY deal_id"
            ).fetchall()
            deal_ids = [r["deal_id"] for r in rows]

        lines.append(sep)
        lines.append("  LENDER OUTREACH REPORT")
        lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(sep)

        for did in deal_ids:
            summary = self.get_response_summary(did)
            lines.append(f"\n  DEAL: {did}")
            lines.append(thin)
            lines.append(
                f"  Lenders Contacted: {summary['total_lenders_contacted']}  |  "
                f"Response Rate: {summary['response_rate_pct']}%"
            )
            lines.append(
                f"  By Status — "
                + "  ".join(
                    f"{k}: {v}"
                    for k, v in summary["by_status"].items()
                    if v > 0
                )
            )
            if summary["best_rate_quote"]:
                bq = summary["best_rate_quote"]
                lines.append(
                    f"  Best Rate: {bq['lender']} @ {bq['rate_pct']}%"
                    + (f"  ({bq['terms']})" if bq.get("terms") else "")
                )
            if summary["highest_proceeds_quote"]:
                hp = summary["highest_proceeds_quote"]
                rate_str = f" @ {hp['rate_pct']}%" if hp.get("rate_pct") else ""
                lines.append(
                    f"  Highest Proceeds: {hp['lender']} — ${hp['amount']:,.0f}{rate_str}"
                )
            if summary["overdue_followups"]:
                lines.append(
                    "  ⚠ Overdue Follow-Ups: "
                    + ", ".join(f"{r['lender']} (due {r['due']})" for r in summary["overdue_followups"])
                )

            # Detail table
            detail = summary["detail"]
            if detail:
                lines.append(
                    f"\n  {'Lender':<28} {'Type':<16} {'Status':<15} {'Amount':>12}  {'Rate':>7}  {'Terms':<30}"
                )
                lines.append("  " + "-" * 115)
                for r in detail:
                    amt = f"${r['quote_amount']:,.0f}" if r.get("quote_amount") else "—"
                    rt = f"{r['quoted_rate']*100:.2f}%" if r.get("quoted_rate") else "—"
                    terms = (r.get("quoted_terms") or "—")[:30]
                    lines.append(
                        f"  {r['lender_name'][:27]:<28} {r['lender_type'][:15]:<16} "
                        f"{r['response_status']:<15} {amt:>12}  {rt:>7}  {terms:<30}"
                    )

        lines.append("\n" + sep)
        return "\n".join(lines)

    def export_lender_db_to_csv(self, filename: Optional[str] = None) -> str:
        """Export the full lender database to CSV."""
        _ensure_dirs()
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(OUTPUT_DIR, f"lender_database_{ts}.csv")
        lenders = self.list_lenders(active_only=False)
        if not lenders:
            print("  ⚠ No lenders to export.")
            return filename
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(lenders[0].keys()))
            writer.writeheader()
            writer.writerows(lenders)
        print(f"  ✓ Lender database exported: {filename}  ({len(lenders)} lenders)")
        return filename

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LenderOutreach":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Demo — 8 Sample Lenders
# ---------------------------------------------------------------------------

def _run_demo() -> None:
    """Load 8 representative lenders and simulate outreach on two deals."""
    db_path = os.path.join(DATA_DIR, "demo_lenders.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    print("\n" + "=" * 72)
    print("  LENDER OUTREACH SYSTEM — DEMO MODE")
    print("  Loading 8 sample institutional lenders...")
    print("=" * 72 + "\n")

    with LenderOutreach(db_path=db_path) as lo:

        # ----------------------------------------------------------------
        # Add 8 lenders representing major capital source types
        # ----------------------------------------------------------------

        pgim_id = lo.add_lender(
            lender_name="PGIM Real Estate Finance",
            lender_type="Life_Company",
            contact_name="Alexandra Torres, Director – CRE Debt",
            email="atorres@pgim.com",
            phone="212-555-0101",
            property_type_focus="Multifamily, Office, Industrial, Retail",
            geography_focus="National — primary and secondary markets",
            min_loan=10_000_000,
            max_loan=500_000_000,
            preferred_ltv_max=0.65,
            preferred_dscr_min=1.25,
            notes="Prefers stabilized assets, WALT >5 yrs, Class A/B. Strong preference for LEED-certified. "
                  "Typically 60–90 day close. Competitive on long-term fixed rates.",
        )

        ml_id = lo.add_lender(
            lender_name="MetLife Investment Management",
            lender_type="Life_Company",
            contact_name="David Kim, VP – Mortgage Production",
            email="dkim@metlife.com",
            phone="212-555-0202",
            property_type_focus="Multifamily, Industrial, Office",
            geography_focus="National — top 30 MSAs",
            min_loan=15_000_000,
            max_loan=300_000_000,
            preferred_ltv_max=0.65,
            preferred_dscr_min=1.25,
            notes="Known for very competitive spreads on industrial and multifamily. "
                  "Conservative underwriter — clean deals only. No retail.",
        )

        fannie_id = lo.add_lender(
            lender_name="Berkadia (Fannie Mae DUS)",
            lender_type="Agency_GSE",
            contact_name="Maria Gonzalez, Senior Director",
            email="mgonzalez@berkadia.com",
            phone="212-555-0303",
            property_type_focus="Multifamily",
            geography_focus="National",
            min_loan=5_000_000,
            max_loan=1_000_000_000,
            preferred_ltv_max=0.80,
            preferred_dscr_min=1.25,
            notes="Best execution for multifamily — Fannie Mae DUS. Green program adds 10–15bps benefit. "
                  "Typically 90–120 days to close including Agency approval. PCNA required.",
        )

        gs_cmbs_id = lo.add_lender(
            lender_name="Goldman Sachs Mortgage Company (CMBS)",
            lender_type="CMBS",
            contact_name="Ryan Walsh, Managing Director",
            email="rwalsh@gs.com",
            phone="212-555-0404",
            property_type_focus="Multifamily, Office, Retail, Industrial, Hotel, Mixed-Use",
            geography_focus="National",
            min_loan=5_000_000,
            max_loan=500_000_000,
            preferred_ltv_max=0.75,
            preferred_dscr_min=1.20,
            notes="Active CMBS conduit. Good execution on higher-leverage deals where Life Co won't go. "
                  "Defeasance-only prepayment — not for borrowers expecting early exit. "
                  "Typically 75–90 day close.",
        )

        chase_id = lo.add_lender(
            lender_name="JPMorgan Chase Bank N.A.",
            lender_type="National_Bank",
            contact_name="Susan Park, SVP – CRE",
            email="spark@jpmorgan.com",
            phone="212-555-0505",
            property_type_focus="Multifamily, Office, Industrial, Hotel",
            geography_focus="National — relationship-driven",
            min_loan=10_000_000,
            max_loan=2_000_000_000,
            preferred_ltv_max=0.70,
            preferred_dscr_min=1.25,
            notes="Recourse on most deals under $50M. Good for relationship borrowers with deposits. "
                  "Floating-rate execution strong. Will consider complex structures.",
        )

        sig_id = lo.add_lender(
            lender_name="Signature RE Lending Group",
            lender_type="Regional_Bank",
            contact_name="Tom Bradley, VP – CRE Lending",
            email="tbradley@signaturere.com",
            phone="212-555-0606",
            property_type_focus="Multifamily, Mixed-Use, Retail",
            geography_focus="New York, New Jersey, Connecticut",
            min_loan=2_000_000,
            max_loan=75_000_000,
            preferred_ltv_max=0.75,
            preferred_dscr_min=1.20,
            notes="NYC-focused community/regional lender. Fast close (45–60 days). Recourse required. "
                  "Strong appetite for NYC multifamily and mixed-use under $50M.",
        )

        bf_id = lo.add_lender(
            lender_name="Blackstone Real Estate Debt Strategies",
            lender_type="Debt_Fund",
            contact_name="Chris Huang, VP – Originations",
            email="chuang@blackstone.com",
            phone="212-555-0707",
            property_type_focus="Multifamily, Office, Retail, Industrial, Hotel, Mixed-Use",
            geography_focus="National — major metros",
            min_loan=15_000_000,
            max_loan=2_000_000_000,
            preferred_ltv_max=0.80,
            preferred_dscr_min=1.05,
            notes="Best-in-class debt fund for complex and transitional deals. Full IO standard. "
                  "SOFR + 275–450bps range. Will move quickly — 30–45 day close. "
                  "Good bridge/value-add execution.",
        )

        mesa_id = lo.add_lender(
            lender_name="Mesa West Capital",
            lender_type="Debt_Fund",
            contact_name="Linda Kim, Director – Originations",
            email="lkim@mesawest.com",
            phone="310-555-0808",
            property_type_focus="Multifamily, Office, Industrial",
            geography_focus="National — gateway markets (NYC, LA, SF, Chicago, Boston, DC)",
            min_loan=20_000_000,
            max_loan=500_000_000,
            preferred_ltv_max=0.78,
            preferred_dscr_min=1.00,
            notes="Bridge and value-add specialist. Strong in gateway markets. "
                  "Interest-only, floating rate. Morgan Stanley subsidiary. "
                  "Very active in NYC/LA multifamily bridge market.",
        )

        # ----------------------------------------------------------------
        # Simulate outreach on two deals
        # ----------------------------------------------------------------
        print("\n--- Simulating outreach for NYC-2024-001 (Waverly Apartments — $18.5M MF Refi) ---\n")

        lo.log_outreach(
            deal_id="NYC-2024-001",
            lender_id=fannie_id,
            response_status="Quote_Received",
            quote_amount=18_500_000,
            quoted_rate=0.0572,
            quoted_terms="10yr fixed, 30yr am, 24mo IO, non-recourse",
            follow_up_date=(date.today() + timedelta(days=3)).isoformat(),
            analyst_notes="Best rate received. Rate locked. Moving to application.",
            date_sent=(date.today() - timedelta(days=14)).isoformat(),
        )
        lo.log_outreach(
            deal_id="NYC-2024-001",
            lender_id=pgim_id,
            response_status="Quote_Received",
            quote_amount=17_900_000,
            quoted_rate=0.0591,
            quoted_terms="10yr fixed, 30yr am, 12mo IO, non-recourse",
            follow_up_date=(date.today() + timedelta(days=5)).isoformat(),
            analyst_notes="Second best. $600K less proceeds at higher rate. Competitive but not best.",
            date_sent=(date.today() - timedelta(days=14)).isoformat(),
        )
        lo.log_outreach(
            deal_id="NYC-2024-001",
            lender_id=chase_id,
            response_status="Passed",
            analyst_notes="Chase passed — borrower does not have existing deposit relationship. Not a fit.",
            date_sent=(date.today() - timedelta(days=14)).isoformat(),
        )
        lo.log_outreach(
            deal_id="NYC-2024-001",
            lender_id=sig_id,
            response_status="Interested",
            follow_up_date=(date.today() + timedelta(days=2)).isoformat(),
            analyst_notes="Interested. Recourse deal — not borrower's preference but keeping in pipeline as backup.",
            date_sent=(date.today() - timedelta(days=10)).isoformat(),
        )
        lo.log_outreach(
            deal_id="NYC-2024-001",
            lender_id=ml_id,
            response_status="Pending",
            follow_up_date=date.today().isoformat(),  # overdue
            analyst_notes="Sent OM. No response yet. Call today to follow up.",
            date_sent=(date.today() - timedelta(days=7)).isoformat(),
        )

        print("\n--- Simulating outreach for NYC-2024-002 (222 W 44th St Office — $42M) ---\n")

        lo.log_outreach(
            deal_id="NYC-2024-002",
            lender_id=bf_id,
            response_status="Quote_Received",
            quote_amount=42_000_000,
            quoted_rate=0.0725,
            quoted_terms="5yr floating SOFR+290bps, full IO, non-recourse",
            follow_up_date=(date.today() + timedelta(days=2)).isoformat(),
            analyst_notes="Best proceeds at full loan amount. Rate expensive but only option at full LTV.",
            date_sent=(date.today() - timedelta(days=10)).isoformat(),
        )
        lo.log_outreach(
            deal_id="NYC-2024-002",
            lender_id=gs_cmbs_id,
            response_status="Quote_Received",
            quote_amount=39_500_000,
            quoted_rate=0.0695,
            quoted_terms="10yr fixed, 30yr am, 2yr IO, non-recourse, defeasance",
            analyst_notes="Lower proceeds but better rate. Borrower not keen on CMBS defeasance.",
            date_sent=(date.today() - timedelta(days=10)).isoformat(),
        )
        lo.log_outreach(
            deal_id="NYC-2024-002",
            lender_id=pgim_id,
            response_status="Passed",
            analyst_notes="PGIM passed — office not in their current focus. No appetite below 6yr WALT.",
            date_sent=(date.today() - timedelta(days=10)).isoformat(),
        )
        lo.log_outreach(
            deal_id="NYC-2024-002",
            lender_id=ml_id,
            response_status="Passed",
            analyst_notes="MetLife passed on all NYC office — portfolio concentration concern.",
            date_sent=(date.today() - timedelta(days=10)).isoformat(),
        )

        # ----------------------------------------------------------------
        # Outreach report
        # ----------------------------------------------------------------
        print("\n" + lo.generate_outreach_report())

        # ----------------------------------------------------------------
        # Lender matching demo
        # ----------------------------------------------------------------
        print("\n--- LENDER MATCHING: NYC-2024-003 (Industrial, $29.75M, LTV 60%, DSCR 1.45x) ---\n")
        matches = lo.get_best_lenders_for_deal(
            loan_amount=29_750_000,
            property_type="Industrial",
            geography="New York",
            ltv=0.60,
            dscr=1.45,
            top_n=6,
        )
        print(
            f"  {'Rank':<5} {'Lender':<35} {'Type':<16} {'Max LTV':>8}  "
            f"{'Min DSCR':>9}  {'Score':>6}"
        )
        print("  " + "-" * 85)
        for i, m in enumerate(matches, 1):
            print(
                f"  {i:<5} {m['lender_name'][:34]:<35} {m['lender_type']:<16} "
                f"{m['preferred_ltv_max']*100:>7.0f}%  "
                f"{m['preferred_dscr_min']:>9.2f}  {m['match_score']:>6}"
            )

        # ----------------------------------------------------------------
        # Export
        # ----------------------------------------------------------------
        print("\n--- EXPORTING LENDER DATABASE TO CSV ---\n")
        lo.export_lender_db_to_csv()

        print(f"\n  Database: {db_path}")
        print("\n" + "=" * 72)
        print("  Demo complete.")
        print("=" * 72 + "\n")


if __name__ == "__main__":
    _run_demo()
