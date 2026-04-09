"""PDF Report Generator — produces professional PDF reports using fpdf2.

Converts report dicts (from daily_report.generate_daily_report()) into
styled PDF documents that can be sent via Telegram as attachments.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fpdf import FPDF

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_CLR_PRIMARY = (26, 54, 93)       # Dark navy — headers
_CLR_ACCENT = (52, 152, 219)      # Blue — section headers
_CLR_SUCCESS = (39, 174, 96)      # Green
_CLR_WARNING = (243, 156, 18)     # Amber
_CLR_DANGER = (231, 76, 60)       # Red
_CLR_MUTED = (127, 140, 141)      # Grey text
_CLR_WHITE = (255, 255, 255)
_CLR_LIGHT_BG = (245, 247, 250)   # Light row background
_CLR_TABLE_HDR = (41, 65, 106)    # Table header bg
_CLR_BLACK = (30, 30, 30)


class _ReportPDF(FPDF):
    """Custom FPDF subclass with header/footer branding."""

    report_title: str = "AI Holding Report"
    report_date: str = ""

    @staticmethod
    def _latin1_safe(text: str) -> str:
        """Replace Unicode chars unsupported by Helvetica/latin-1."""
        replacements = {
            "\u2014": "-",   # em dash
            "\u2013": "-",   # en dash
            "\u2018": "'",   # left single quote
            "\u2019": "'",   # right single quote
            "\u201c": '"',   # left double quote
            "\u201d": '"',   # right double quote
            "\u2026": "...", # ellipsis
            "\u2022": "*",   # bullet
            "\u2192": "->",  # right arrow
            "\u2190": "<-",  # left arrow
            "\u2764": "+",   # heart
            "\u2696": "=",   # balance scale
            "\u270D": ">",   # writing hand
            "\u23F0": "@",   # alarm clock
            "\u2692": "#",   # hammer & pick
        }
        for char, repl in replacements.items():
            text = text.replace(char, repl)
        # Fallback: encode to latin-1, replacing anything left
        return text.encode("latin-1", errors="replace").decode("latin-1")

    def header(self):
        # Navy bar at top
        self.set_fill_color(*_CLR_PRIMARY)
        self.rect(0, 0, 210, 18, "F")

        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*_CLR_WHITE)
        self.set_xy(10, 3)
        self.cell(0, 12, self.report_title, align="L")

        if self.report_date:
            self.set_font("Helvetica", "", 9)
            self.set_xy(10, 3)
            self.cell(0, 12, self.report_date, align="R")

        self.set_xy(10, 22)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*_CLR_MUTED)
        self.cell(0, 10, f"AI Holding  |  Page {self.page_no()}/{{nb}}", align="C")

    # Override cell / multi_cell to auto-sanitise all text
    def cell(self, w=None, h=None, text="", *args, **kwargs):
        return super().cell(w, h, self._latin1_safe(str(text)), *args, **kwargs)

    def multi_cell(self, w=0, h=None, text="", *args, **kwargs):
        return super().multi_cell(w, h, self._latin1_safe(str(text)), *args, **kwargs)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _safe(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value)


def _pct_bar(pdf: _ReportPDF, x: float, y: float, w: float, pct: float):
    """Draw a tiny percentage bar."""
    h = 4
    pdf.set_fill_color(220, 220, 220)
    pdf.rect(x, y, w, h, "F")

    fill_w = max(0.5, w * min(pct / 100.0, 1.0))
    if pct < 50:
        pdf.set_fill_color(*_CLR_SUCCESS)
    elif pct < 80:
        pdf.set_fill_color(*_CLR_WARNING)
    else:
        pdf.set_fill_color(*_CLR_DANGER)
    pdf.rect(x, y, fill_w, h, "F")


def _section_heading(pdf: _ReportPDF, icon: str, title: str):
    """Render a colored section heading."""
    pdf.ln(4)
    pdf.set_fill_color(*_CLR_ACCENT)
    pdf.rect(10, pdf.get_y(), 3, 7, "F")
    pdf.set_x(16)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_CLR_PRIMARY)
    pdf.cell(0, 7, f"{icon}  {title}")
    pdf.ln(9)


def _kv_line(pdf: _ReportPDF, label: str, value: str, indent: float = 14):
    """Print a key: value line."""
    pdf.set_x(indent)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_CLR_BLACK)
    pdf.cell(50, 5, label)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 5, value)
    pdf.ln(5.5)


def _table(pdf: _ReportPDF, headers: list[str], rows: list[list[str]],
           col_widths: list[float] | None = None):
    """Render a simple table."""
    if not rows:
        return
    n = len(headers)
    if col_widths is None:
        avail = 190 - 8  # page width minus margins
        col_widths = [avail / n] * n

    # header row
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(*_CLR_TABLE_HDR)
    pdf.set_text_color(*_CLR_WHITE)
    pdf.set_x(14)
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 6, h, border=0, fill=True, align="C")
    pdf.ln(6)

    # data rows
    pdf.set_font("Helvetica", "", 8)
    for row_idx, row in enumerate(rows):
        if row_idx % 2 == 0:
            pdf.set_fill_color(*_CLR_LIGHT_BG)
        else:
            pdf.set_fill_color(*_CLR_WHITE)
        pdf.set_text_color(*_CLR_BLACK)
        pdf.set_x(14)
        for i, cell in enumerate(row):
            pdf.cell(col_widths[i], 5.5, cell[:40], border=0, fill=True, align="C")
        pdf.ln(5.5)


def _error_note(pdf: _ReportPDF, msg: str):
    pdf.set_x(16)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*_CLR_DANGER)
    pdf.cell(0, 5, f"Error: {msg[:120]}")
    pdf.ln(6)
    pdf.set_text_color(*_CLR_BLACK)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_costs(pdf: _ReportPDF, costs: dict):
    _section_heading(pdf, "\u0024", "COST SUMMARY")
    if "error" in costs:
        _error_note(pdf, costs["error"])
        return

    budget_pct = costs.get("budget_used_pct", 0)
    _kv_line(pdf, "Spend Today", f"${costs.get('cost_today_usd', 0):.4f}  /  ${costs.get('daily_budget_usd', 0):.2f}  ({budget_pct:.1f}%)")
    _pct_bar(pdf, 64, pdf.get_y() - 4, 55, budget_pct)
    _kv_line(pdf, "API Calls Today", _safe(costs.get("calls_today", 0)))
    _kv_line(pdf, "Lifetime Cost", f"${costs.get('lifetime_cost_usd', 0):.4f}  ({costs.get('lifetime_calls', 0)} calls)")

    top = costs.get("top_spenders", [])
    if top:
        pdf.ln(2)
        _table(
            pdf,
            ["Agent", "Cost Today ($)", "Calls"],
            [
                [
                    _safe(s.get("agent_id")),
                    f"{s.get('cost_today_usd', 0):.4f}",
                    _safe(s.get("calls_today", 0)),
                ]
                for s in top[:5]
            ],
            [70, 50, 50],
        )


def _render_performance(pdf: _ReportPDF, perf: dict):
    _section_heading(pdf, "\u2191", "AGENT PERFORMANCE")
    if "error" in perf:
        _error_note(pdf, perf["error"])
        return

    _kv_line(pdf, "Agents Tracked", _safe(perf.get("total_tracked", 0)))

    agents = perf.get("agents", [])
    if agents:
        _table(
            pdf,
            ["Agent", "Grade", "Success %", "Tasks"],
            [
                [
                    _safe(a.get("agent_id")),
                    _safe(a.get("grade", "-")),
                    f"{a.get('success_rate', 0):.0f}%",
                    _safe(a.get("total_tasks", 0)),
                ]
                for a in agents[:10]
            ],
            [60, 30, 40, 40],
        )

    underperformers = perf.get("underperformers", [])
    if underperformers:
        pdf.ln(2)
        pdf.set_x(16)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_CLR_WARNING)
        pdf.cell(0, 5, "Underperformers:")
        pdf.ln(5)
        pdf.set_text_color(*_CLR_BLACK)
        for u in underperformers:
            _kv_line(pdf, f"  {_safe(u.get('agent_id'))}", f"Grade {_safe(u.get('grade'))}  —  Success {u.get('success_rate', 0):.0f}%", indent=18)


def _render_operations(pdf: _ReportPDF, ops: dict):
    _section_heading(pdf, "\u2692", "OPERATIONS")
    if "error" in ops:
        _error_note(pdf, ops["error"])
        return

    _kv_line(pdf, "Companies", _safe(ops.get("total_companies", 0)))
    _kv_line(pdf, "Active Agents", f"{ops.get('active_agents', 0)} / {ops.get('total_agents', 0)} total")

    companies = ops.get("companies", [])
    if companies:
        _table(
            pdf,
            ["Company", "Type", "Agents"],
            [
                [_safe(c.get("name")), _safe(c.get("type")), _safe(c.get("agent_count", 0))]
                for c in companies
            ],
            [80, 50, 40],
        )

    errors = ops.get("error_or_paused_agents", [])
    if errors:
        pdf.ln(2)
        pdf.set_x(16)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_CLR_DANGER)
        pdf.cell(0, 5, "Agents with Issues:")
        pdf.ln(5)
        pdf.set_text_color(*_CLR_BLACK)
        _table(
            pdf,
            ["Agent", "Name", "Status"],
            [[_safe(e.get("id")), _safe(e.get("name")), _safe(e.get("status"))] for e in errors],
            [60, 60, 50],
        )


def _render_trading(pdf: _ReportPDF, trading: dict):
    _section_heading(pdf, "\u2193", "TRADING PORTFOLIO")
    if "error" in trading:
        _error_note(pdf, trading["error"])
        return
    if not trading:
        pdf.set_x(16)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*_CLR_MUTED)
        pdf.cell(0, 5, "No trading data available.")
        pdf.ln(6)
        return

    for k, v in trading.items():
        if isinstance(v, (int, float)):
            _kv_line(pdf, k.replace("_", " ").title(), f"{v}")
        elif isinstance(v, str):
            _kv_line(pdf, k.replace("_", " ").title(), v)


def _render_health(pdf: _ReportPDF, health: dict):
    _section_heading(pdf, "\u2764", "SYSTEM HEALTH")
    if "error" in health:
        _error_note(pdf, health["error"])
        return

    overall = health.get("overall", "unknown")
    label_color = _CLR_SUCCESS if overall == "healthy" else _CLR_DANGER
    pdf.set_x(14)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*_CLR_BLACK)
    pdf.cell(50, 5, "Overall Status")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*label_color)
    pdf.cell(0, 5, overall.upper())
    pdf.ln(5.5)
    pdf.set_text_color(*_CLR_BLACK)

    _kv_line(pdf, "Database", _safe(health.get("db")))
    _kv_line(pdf, "Redis", _safe(health.get("redis")))
    uptime_h = health.get("uptime_seconds", 0) / 3600
    _kv_line(pdf, "Uptime", f"{uptime_h:.1f} hours")


def _render_budgets(pdf: _ReportPDF, budgets: dict):
    _section_heading(pdf, "\u2696", "BUDGET ENFORCEMENT")
    if "error" in budgets:
        _error_note(pdf, budgets["error"])
        return

    _kv_line(pdf, "Global Daily Budget", f"${budgets.get('global_daily_budget_usd', 0):.2f}")

    agent_budgets = budgets.get("agent_budgets", {})
    if agent_budgets:
        _kv_line(pdf, "Agent Budgets", f"{len(agent_budgets)} configured")

    company_budgets = budgets.get("company_budgets", {})
    if company_budgets:
        _kv_line(pdf, "Company Budgets", f"{len(company_budgets)} configured")

    paused = budgets.get("paused_agents_today", [])
    if paused:
        pdf.set_x(16)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_CLR_WARNING)
        pdf.cell(0, 5, f"Paused today: {', '.join(paused)}")
        pdf.ln(6)
        pdf.set_text_color(*_CLR_BLACK)


def _render_audit(pdf: _ReportPDF, audit: dict):
    _section_heading(pdf, "\u270D", "AUDIT LOG (24h)")
    if "error" in audit:
        _error_note(pdf, audit["error"])
        return

    _kv_line(pdf, "Total Actions", _safe(audit.get("total_actions_24h", 0)))
    failed = audit.get("failed_actions", 0)
    if failed:
        pdf.set_x(14)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_CLR_BLACK)
        pdf.cell(50, 5, "Failed Actions")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_CLR_DANGER)
        pdf.cell(0, 5, str(failed))
        pdf.ln(5.5)
        pdf.set_text_color(*_CLR_BLACK)
    else:
        _kv_line(pdf, "Failed Actions", "0")

    top_actions = audit.get("top_actions", [])
    if top_actions:
        _table(
            pdf,
            ["Action", "Count"],
            [[_safe(a[0]) if isinstance(a, (list, tuple)) else _safe(a), _safe(a[1]) if isinstance(a, (list, tuple)) else ""] for a in top_actions[:8]],
            [100, 70],
        )


def _render_scheduled(pdf: _ReportPDF, sched: dict):
    _section_heading(pdf, "\u23F0", "SCHEDULED TASKS")
    if "error" in sched:
        _error_note(pdf, sched["error"])
        return

    _kv_line(pdf, "Active Tasks", _safe(sched.get("total", 0)))

    tasks = sched.get("tasks", [])
    if tasks:
        _table(
            pdf,
            ["Task ID", "Type", "Next Run"],
            [
                [
                    _safe(t.get("task_id", t.get("id", "?"))),
                    _safe(t.get("type", t.get("trigger", "?"))),
                    _safe(t.get("next_run", "?")),
                ]
                for t in tasks[:10]
            ],
            [70, 40, 60],
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_report_pdf(
    report: dict,
    title: str = "Daily Intelligence Report",
) -> bytes:
    """Convert a report dict into a styled PDF.

    Args:
        report: Dict returned by ``daily_report.generate_daily_report()``.
        title: Title shown in the header bar.

    Returns:
        Raw PDF bytes ready to be sent as a file.
    """
    generated_at = report.get("generated_at", datetime.now(timezone.utc).isoformat())
    try:
        dt = datetime.fromisoformat(generated_at)
        date_str = dt.strftime("%B %d, %Y  %H:%M UTC")
    except (ValueError, TypeError):
        date_str = str(generated_at)

    pdf = _ReportPDF(orientation="P", unit="mm", format="A4")
    pdf.report_title = f"AI Holding  —  {title}"
    pdf.report_date = date_str
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    # Subtitle
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_CLR_MUTED)
    pdf.cell(0, 5, f"Report generated on {date_str}", align="C")
    pdf.ln(8)

    sections = report.get("sections", {})

    # Render each section in order
    renderers = [
        ("costs", _render_costs),
        ("performance", _render_performance),
        ("operations", _render_operations),
        ("trading", _render_trading),
        ("health", _render_health),
        ("budgets", _render_budgets),
        ("audit", _render_audit),
        ("scheduled_tasks", _render_scheduled),
    ]

    for key, renderer in renderers:
        data = sections.get(key)
        if data is not None:
            renderer(pdf, data)

    return pdf.output()


def generate_text_report_pdf(
    title: str,
    content: str,
) -> bytes:
    """Convert arbitrary text/markdown content into a branded PDF.

    Used by the CEO's ``send_report`` tool for ad-hoc reports.
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%B %d, %Y  %H:%M UTC")

    pdf = _ReportPDF(orientation="P", unit="mm", format="A4")
    pdf.report_title = f"AI Holding  —  {title}"
    pdf.report_date = date_str
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    # Subtitle
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_CLR_MUTED)
    pdf.cell(0, 5, f"Report generated on {date_str}", align="C")
    pdf.ln(10)

    # Render content — split into paragraphs
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_CLR_BLACK)

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            pdf.ln(4)
            continue

        # Detect markdown-ish headings
        if stripped.startswith("### "):
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*_CLR_ACCENT)
            pdf.set_x(14)
            pdf.multi_cell(0, 5, stripped[4:])
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*_CLR_BLACK)
        elif stripped.startswith("## "):
            pdf.ln(4)
            _section_heading(pdf, "", stripped[3:])
        elif stripped.startswith("# "):
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(*_CLR_PRIMARY)
            pdf.set_x(14)
            pdf.multi_cell(0, 6, stripped[2:])
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*_CLR_BLACK)
        elif stripped.startswith("**") and stripped.endswith("**"):
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_x(14)
            pdf.multi_cell(0, 5, stripped.strip("*"))
            pdf.set_font("Helvetica", "", 9)
        elif stripped.startswith("- ") or stripped.startswith("• "):
            pdf.set_x(18)
            pdf.cell(4, 5, "\u2022")
            pdf.multi_cell(0, 5, stripped[2:])
        else:
            pdf.set_x(14)
            pdf.multi_cell(0, 5, stripped)

    return pdf.output()
