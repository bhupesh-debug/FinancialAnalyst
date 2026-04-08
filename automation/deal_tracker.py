"""
deal_tracker.py
===============
CRE Capital Markets — Deal Pipeline Tracker

A SQLite-backed deal pipeline management system for commercial mortgage brokerages.
Tracks deals from prospect through closing, with analytics and CSV export capability.

Usage:
    python deal_tracker.py                  # Run demo
    from deal_tracker import DealTracker    # Import as module

Author: [Analyst Name]
Version: 1.0
"""

import sqlite3
import csv
import os
import json
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = [
    "Prospecting",
    "Marketing",
    "Quoting",
    "Under_Application",
    "Closing",
    "Closed",
    "Dead",
]

VALID_PROPERTY_TYPES = [
    "Multifamily",
    "Office",
    "Retail",
    "Industrial",
    "Hotel",
    "Self-Storage",
    "Mixed-Use",
    "Land",
    "Other",
]

STATUS_ORDER = {s: i for i, s in enumerate(VALID_STATUSES)}

# Directories used by the module
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_THIS_DIR, "..", "data")
OUTPUT_DIR = os.path.join(_THIS_DIR, "..", "output")
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "deals.db")


def _ensure_dirs() -> None:
    """Create data/ and output/ directories if they do not exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# DealTracker class
# ---------------------------------------------------------------------------


class DealTracker:
    """
    SQLite-backed CRE deal pipeline tracker.

    Parameters
    ----------
    db_path : str, optional
        Path to the SQLite database file. Defaults to data/deals.db relative
        to this script's parent directory.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        _ensure_dirs()
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn: sqlite3.Connection = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row  # column access by name
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_tables()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        """Create the deals table and supporting views if they don't exist."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS deals (
                deal_id          TEXT PRIMARY KEY,
                property_name    TEXT NOT NULL,
                address          TEXT,
                property_type    TEXT,
                loan_amount      REAL,
                borrower         TEXT,
                status           TEXT NOT NULL DEFAULT 'Prospecting',
                phase            TEXT,
                assigned_broker  TEXT,
                date_added       TEXT NOT NULL,
                last_updated     TEXT NOT NULL,
                notes            TEXT,
                lender_count     INTEGER DEFAULT 0,
                quotes_received  INTEGER DEFAULT 0,
                target_close_date TEXT
            );

            CREATE TABLE IF NOT EXISTS deal_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id         TEXT NOT NULL,
                changed_at      TEXT NOT NULL,
                field_changed   TEXT NOT NULL,
                old_value       TEXT,
                new_value       TEXT
            );
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD Operations
    # ------------------------------------------------------------------

    def add_deal(
        self,
        deal_id: str,
        property_name: str,
        address: str = "",
        property_type: str = "Multifamily",
        loan_amount: float = 0.0,
        borrower: str = "",
        status: str = "Prospecting",
        phase: str = "",
        assigned_broker: str = "",
        notes: str = "",
        lender_count: int = 0,
        quotes_received: int = 0,
        target_close_date: Optional[str] = None,
    ) -> None:
        """
        Add a new deal to the pipeline.

        Parameters
        ----------
        deal_id : str
            Unique identifier (e.g., 'NYC-2024-001').
        property_name : str
            Descriptive name of the property or deal.
        address : str
            Street address of the collateral property.
        property_type : str
            One of VALID_PROPERTY_TYPES.
        loan_amount : float
            Requested loan amount in USD.
        borrower : str
            Borrower / sponsor name.
        status : str
            Pipeline status from VALID_STATUSES.
        phase : str
            Sub-status detail (e.g., 'Lender Outreach', 'Quote Review').
        assigned_broker : str
            Name of the senior broker running the deal.
        notes : str
            Free-form notes visible in reports.
        lender_count : int
            Number of lenders contacted.
        quotes_received : int
            Number of formal quotes received.
        target_close_date : str, optional
            ISO date string (YYYY-MM-DD) for expected closing.
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}"
            )
        now = datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            """
            INSERT INTO deals (
                deal_id, property_name, address, property_type,
                loan_amount, borrower, status, phase, assigned_broker,
                date_added, last_updated, notes,
                lender_count, quotes_received, target_close_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deal_id,
                property_name,
                address,
                property_type,
                loan_amount,
                borrower,
                status,
                phase,
                assigned_broker,
                now,
                now,
                notes,
                lender_count,
                quotes_received,
                target_close_date,
            ),
        )
        self._conn.commit()
        print(f"  ✓ Added deal: [{deal_id}] {property_name} — ${loan_amount:,.0f} — {status}")

    def update_status(
        self,
        deal_id: str,
        new_status: str,
        phase: Optional[str] = None,
        notes: Optional[str] = None,
        lender_count: Optional[int] = None,
        quotes_received: Optional[int] = None,
    ) -> None:
        """
        Update the status (and optionally other fields) for an existing deal.

        Parameters
        ----------
        deal_id : str
            Deal to update.
        new_status : str
            New pipeline status from VALID_STATUSES.
        phase : str, optional
            Updated phase description.
        notes : str, optional
            Notes to append to existing notes (not overwrite).
        lender_count : int, optional
            Updated total lenders contacted.
        quotes_received : int, optional
            Updated quotes received count.
        """
        if new_status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{new_status}'. Must be one of: {VALID_STATUSES}"
            )
        row = self._conn.execute(
            "SELECT * FROM deals WHERE deal_id = ?", (deal_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Deal ID '{deal_id}' not found.")

        now = datetime.now().isoformat(timespec="seconds")
        old_status = row["status"]

        # Build history entry
        self._conn.execute(
            """
            INSERT INTO deal_history (deal_id, changed_at, field_changed, old_value, new_value)
            VALUES (?, ?, 'status', ?, ?)
            """,
            (deal_id, now, old_status, new_status),
        )

        # Update fields
        updates: Dict[str, Any] = {"status": new_status, "last_updated": now}
        if phase is not None:
            updates["phase"] = phase
        if notes is not None:
            existing = row["notes"] or ""
            updates["notes"] = (existing + f"\n[{now}] {notes}").strip()
        if lender_count is not None:
            updates["lender_count"] = lender_count
        if quotes_received is not None:
            updates["quotes_received"] = quotes_received

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE deals SET {set_clause} WHERE deal_id = ?",
            list(updates.values()) + [deal_id],
        )
        self._conn.commit()
        print(
            f"  ✓ Updated [{deal_id}]: {old_status} → {new_status}"
            + (f" | Phase: {phase}" if phase else "")
        )

    def get_deal(self, deal_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single deal by ID as a dictionary."""
        row = self._conn.execute(
            "SELECT * FROM deals WHERE deal_id = ?", (deal_id,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Pipeline Queries
    # ------------------------------------------------------------------

    def get_pipeline(
        self,
        status_filter: Optional[List[str]] = None,
        broker_filter: Optional[str] = None,
        property_type_filter: Optional[str] = None,
        exclude_statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return all deals matching the given filters.

        Parameters
        ----------
        status_filter : list of str, optional
            Only include deals in these statuses.
        broker_filter : str, optional
            Filter by assigned_broker name (case-insensitive partial match).
        property_type_filter : str, optional
            Filter by property_type.
        exclude_statuses : list of str, optional
            Exclude deals in these statuses (e.g., ['Closed', 'Dead']).

        Returns
        -------
        list of dict
            Sorted by status pipeline order, then by date_added descending.
        """
        query = "SELECT * FROM deals WHERE 1=1"
        params: List[Any] = []

        if status_filter:
            placeholders = ",".join("?" * len(status_filter))
            query += f" AND status IN ({placeholders})"
            params.extend(status_filter)
        if exclude_statuses:
            placeholders = ",".join("?" * len(exclude_statuses))
            query += f" AND status NOT IN ({placeholders})"
            params.extend(exclude_statuses)
        if broker_filter:
            query += " AND LOWER(assigned_broker) LIKE ?"
            params.append(f"%{broker_filter.lower()}%")
        if property_type_filter:
            query += " AND property_type = ?"
            params.append(property_type_filter)

        rows = self._conn.execute(query, params).fetchall()
        deals = [dict(r) for r in rows]
        # Sort by status pipeline order, then by date_added (newest first)
        deals.sort(
            key=lambda d: (STATUS_ORDER.get(d["status"], 99), d["date_added"])
        )
        return deals

    def get_deal_summary(self) -> Dict[str, Any]:
        """
        Return pipeline analytics: deal counts by status, volume, average days,
        and conversion metrics.

        Returns
        -------
        dict
            Summary metrics dictionary.
        """
        now_iso = datetime.now().isoformat(timespec="seconds")
        rows = self._conn.execute("SELECT * FROM deals").fetchall()
        deals = [dict(r) for r in rows]

        total = len(deals)
        by_status: Dict[str, Dict[str, Any]] = {}
        for status in VALID_STATUSES:
            group = [d for d in deals if d["status"] == status]
            total_volume = sum(d["loan_amount"] or 0 for d in group)
            avg_days = 0.0
            if group:
                days_list = []
                for d in group:
                    try:
                        added = datetime.fromisoformat(d["date_added"])
                        days_list.append((datetime.now() - added).days)
                    except Exception:
                        pass
                avg_days = sum(days_list) / len(days_list) if days_list else 0
            by_status[status] = {
                "count": len(group),
                "total_volume": total_volume,
                "avg_days_in_stage": round(avg_days, 1),
            }

        active_deals = [
            d for d in deals if d["status"] not in ("Closed", "Dead")
        ]
        closed_deals = [d for d in deals if d["status"] == "Closed"]
        dead_deals = [d for d in deals if d["status"] == "Dead"]

        total_active_volume = sum(d["loan_amount"] or 0 for d in active_deals)
        total_closed_volume = sum(d["loan_amount"] or 0 for d in closed_deals)

        # Conversion rate: Closed / (Closed + Dead) — deals that reached a terminal status
        terminal = len(closed_deals) + len(dead_deals)
        conversion_rate = (
            round(len(closed_deals) / terminal * 100, 1) if terminal > 0 else None
        )

        # Upcoming closes in next 30 days
        upcoming = []
        for d in active_deals:
            if d.get("target_close_date"):
                try:
                    tcd = date.fromisoformat(d["target_close_date"])
                    days_out = (tcd - date.today()).days
                    if 0 <= days_out <= 30:
                        upcoming.append(
                            {
                                "deal_id": d["deal_id"],
                                "property_name": d["property_name"],
                                "target_close_date": d["target_close_date"],
                                "days_out": days_out,
                                "loan_amount": d["loan_amount"],
                            }
                        )
                except ValueError:
                    pass
        upcoming.sort(key=lambda x: x["days_out"])

        return {
            "total_deals": total,
            "active_deals": len(active_deals),
            "total_active_volume": total_active_volume,
            "total_closed_volume": total_closed_volume,
            "closed_deals": len(closed_deals),
            "dead_deals": len(dead_deals),
            "conversion_rate_pct": conversion_rate,
            "by_status": by_status,
            "upcoming_closes": upcoming,
            "summary_as_of": now_iso,
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_to_csv(self, filename: Optional[str] = None) -> str:
        """
        Export the full pipeline to a CSV file.

        Parameters
        ----------
        filename : str, optional
            Output filename. Defaults to pipeline_YYYYMMDD_HHMMSS.csv
            in the output/ directory.

        Returns
        -------
        str
            Absolute path to the created CSV file.
        """
        _ensure_dirs()
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(OUTPUT_DIR, f"pipeline_{ts}.csv")

        deals = self.get_pipeline()
        if not deals:
            print("  ⚠ No deals to export.")
            return filename

        fieldnames = list(deals[0].keys())
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(deals)

        print(f"  ✓ Pipeline exported to: {filename}  ({len(deals)} deals)")
        return filename

    def generate_pipeline_report(self) -> str:
        """
        Build and return a formatted text pipeline report.

        Returns
        -------
        str
            Multi-line report string suitable for printing or file export.
        """
        summary = self.get_deal_summary()
        active = self.get_pipeline(exclude_statuses=["Closed", "Dead"])
        lines: List[str] = []

        sep = "=" * 72
        thin = "-" * 72

        lines.append(sep)
        lines.append("  CRE CAPITAL MARKETS — DEAL PIPELINE REPORT")
        lines.append(f"  As of: {summary['summary_as_of']}")
        lines.append(sep)
        lines.append("")
        lines.append("  PIPELINE OVERVIEW")
        lines.append(thin)
        lines.append(
            f"  Total Deals: {summary['total_deals']}  |  "
            f"Active: {summary['active_deals']}  |  "
            f"Closed: {summary['closed_deals']}  |  "
            f"Dead: {summary['dead_deals']}"
        )
        lines.append(
            f"  Active Pipeline Volume: ${summary['total_active_volume']:>14,.0f}"
        )
        lines.append(
            f"  Total Closed Volume:    ${summary['total_closed_volume']:>14,.0f}"
        )
        if summary["conversion_rate_pct"] is not None:
            lines.append(
                f"  Conversion Rate:        {summary['conversion_rate_pct']}%  "
                "(Closed / (Closed + Dead))"
            )
        lines.append("")
        lines.append("  DEALS BY STATUS")
        lines.append(thin)
        lines.append(
            f"  {'Status':<20} {'Count':>5}  {'Volume':>16}  {'Avg Days':>9}"
        )
        lines.append("  " + "-" * 55)
        for status in VALID_STATUSES:
            s = summary["by_status"][status]
            lines.append(
                f"  {status:<20} {s['count']:>5}  ${s['total_volume']:>14,.0f}  "
                f"{s['avg_days_in_stage']:>8.1f}d"
            )
        lines.append("")

        if summary["upcoming_closes"]:
            lines.append("  UPCOMING CLOSES (Next 30 Days)")
            lines.append(thin)
            lines.append(
                f"  {'Deal ID':<15} {'Property':<28} {'Close Date':<12} {'Days Out':>8}  {'Loan Amt':>12}"
            )
            lines.append("  " + "-" * 80)
            for uc in summary["upcoming_closes"]:
                lines.append(
                    f"  {uc['deal_id']:<15} {uc['property_name']:<28} "
                    f"{uc['target_close_date']:<12} {uc['days_out']:>8}  "
                    f"${uc['loan_amount']:>10,.0f}"
                )
            lines.append("")

        if active:
            lines.append("  ACTIVE DEAL DETAIL")
            lines.append(thin)
            header = (
                f"  {'Deal ID':<15} {'Property':<26} {'Type':<13} "
                f"{'Loan Amt':>12}  {'Status':<18} {'Broker':<14} {'Lenders':>7} {'Quotes':>7}"
            )
            lines.append(header)
            lines.append("  " + "-" * 116)
            for d in active:
                lines.append(
                    f"  {d['deal_id']:<15} {(d['property_name'] or '')[:25]:<26} "
                    f"{(d['property_type'] or ''):<13} "
                    f"${d['loan_amount']:>10,.0f}  "
                    f"{d['status']:<18} {(d['assigned_broker'] or '')[:13]:<14} "
                    f"{d['lender_count']:>7} {d['quotes_received']:>7}"
                )
        lines.append("")
        lines.append(sep)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "DealTracker":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Demo — 5 Sample NYC Deals
# ---------------------------------------------------------------------------

def _run_demo() -> None:
    """Populate the tracker with 5 sample NYC deals and print a report."""
    db_path = os.path.join(DATA_DIR, "demo_deals.db")
    # Remove existing demo DB so we start clean each run
    if os.path.exists(db_path):
        os.remove(db_path)

    print("\n" + "=" * 72)
    print("  DEAL TRACKER — DEMO MODE")
    print("  Loading 5 sample NYC commercial mortgage deals...")
    print("=" * 72 + "\n")

    with DealTracker(db_path=db_path) as tracker:

        # ----------------------------------------------------------------
        # Add 5 sample deals
        # ----------------------------------------------------------------
        tracker.add_deal(
            deal_id="NYC-2024-001",
            property_name="The Waverly Apartments",
            address="247 W 87th Street, New York, NY 10024",
            property_type="Multifamily",
            loan_amount=18_500_000,
            borrower="Hudson Ridge Capital LLC",
            status="Closing",
            phase="Loan Docs Under Review",
            assigned_broker="Sarah Chen",
            notes="Agency (Fannie DUS) refinance. Rate locked at 5.72%. Close target Nov 15.",
            lender_count=8,
            quotes_received=4,
            target_close_date=(date.today() + timedelta(days=14)).isoformat(),
        )

        tracker.add_deal(
            deal_id="NYC-2024-002",
            property_name="222 West 44th Street Office",
            address="222 W 44th Street, New York, NY 10036",
            property_type="Office",
            loan_amount=42_000_000,
            borrower="Midtown Properties Group Inc.",
            status="Quoting",
            phase="Quote Matrix Review",
            assigned_broker="James Rollins",
            notes="Ground-floor retail + 12 office floors. WALT 4.8 yrs. 3 quotes received; best rate 7.25% from Lender B (debt fund).",
            lender_count=11,
            quotes_received=3,
            target_close_date=(date.today() + timedelta(days=75)).isoformat(),
        )

        tracker.add_deal(
            deal_id="NYC-2024-003",
            property_name="Greenpoint Industrial Portfolio",
            address="55-75 Commercial Street, Brooklyn, NY 11222",
            property_type="Industrial",
            loan_amount=29_750_000,
            borrower="Brooklyn Industrial Holdings LLC",
            status="Marketing",
            phase="Initial Lender Outreach",
            assigned_broker="Sarah Chen",
            notes="3-building flex industrial portfolio. 98% occupied. Sent OM to 9 lenders. No quotes yet.",
            lender_count=9,
            quotes_received=0,
            target_close_date=(date.today() + timedelta(days=90)).isoformat(),
        )

        tracker.add_deal(
            deal_id="NYC-2024-004",
            property_name="Kew Gardens Retail Center",
            address="118-15 Queens Blvd, Queens, NY 11415",
            property_type="Retail",
            loan_amount=9_200_000,
            borrower="QBD Real Estate Partners",
            status="Under_Application",
            phase="Appraisal Ordered",
            assigned_broker="James Rollins",
            notes="Anchored by regional grocery. 7-yr WALT. Application submitted to First Republic successor bank. Appraisal due Dec 1.",
            lender_count=6,
            quotes_received=2,
            target_close_date=(date.today() + timedelta(days=55)).isoformat(),
        )

        tracker.add_deal(
            deal_id="NYC-2024-005",
            property_name="NoMad Mixed-Use Development",
            address="31 W 28th Street, New York, NY 10001",
            property_type="Mixed-Use",
            loan_amount=67_000_000,
            borrower="Belgrove Street Ventures LLC",
            status="Prospecting",
            phase="OM Preparation",
            assigned_broker="Sarah Chen",
            notes="12-story mixed-use: ground retail + 96 luxury condos. Construction lender takeout. Targeting life company + mez. OM in progress.",
            lender_count=0,
            quotes_received=0,
            target_close_date=(date.today() + timedelta(days=120)).isoformat(),
        )

        # ----------------------------------------------------------------
        # Simulate some status updates
        # ----------------------------------------------------------------
        print("\n--- Simulating deal progression updates ---\n")
        tracker.update_status(
            "NYC-2024-003",
            "Marketing",
            phase="First Lender Responses",
            notes="Received 'interested' response from PGIM and Cornerstone. Follow-up calls scheduled.",
            lender_count=9,
            quotes_received=0,
        )
        tracker.update_status(
            "NYC-2024-002",
            "Quoting",
            phase="Best & Final Round",
            notes="Sent B&F request to top 2 lenders: Blackstone RE Debt, Fortress. Decision expected by EOW.",
            quotes_received=3,
        )

        # ----------------------------------------------------------------
        # Print the report
        # ----------------------------------------------------------------
        print("\n" + tracker.generate_pipeline_report())

        # ----------------------------------------------------------------
        # Print deal summary JSON for programmatic use
        # ----------------------------------------------------------------
        summary = tracker.get_deal_summary()
        print("\n--- DEAL SUMMARY (JSON) ---\n")
        # Format currency for readability
        display = {
            **summary,
            "total_active_volume": f"${summary['total_active_volume']:,.0f}",
            "total_closed_volume": f"${summary['total_closed_volume']:,.0f}",
        }
        print(json.dumps(display, indent=2, default=str))

        # ----------------------------------------------------------------
        # Export CSV
        # ----------------------------------------------------------------
        print("\n--- EXPORTING PIPELINE TO CSV ---\n")
        csv_path = tracker.export_to_csv()

        print(f"\n  Database: {db_path}")
        print(f"  CSV:      {csv_path}")
        print("\n" + "=" * 72)
        print("  Demo complete.")
        print("=" * 72 + "\n")


if __name__ == "__main__":
    _run_demo()
