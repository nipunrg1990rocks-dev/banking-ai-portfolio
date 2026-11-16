"""Technology workforce review domain.

Mirrors the structure of a monthly resource roster (one row per person-contract with
monthly FTE allocation columns, e.g. ``Jan-18`` ... ``Dec-18``) and the slide set of a
"Technology Workforce Review" pack: headline KPIs, headcount dashboard, headcount
bridge, location trend, FTE heatmap, cost bridge, where-people-ended-up flow, the
contract-end cliff, next steps and a methodology appendix.

Definitions (also in the KPI dictionary):

* On contract at a date: Start Date <= date <= End Date, each Position # + Name once.
* People allocated in a month: monthly allocation > 0.
* FTE: sum of monthly allocations (part months pro-rated in the source).
* Monthly cost: FTE x 22 working days x daily rate (EUR); no FX, tax or mark-up.
* External: T&M and Fixed Price resources.
"""

from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .. import payload as P
from ..store import Dataset, content_hash
from ..validation import ValidationReport, check_columns, quarantine_rows, strip_formula_text
from .base import Context, Domain, SlideType, Template

WORKING_DAYS = 22
RATE_CARD_EUR = {"Band_11": 200.0, "Band_9": 100.0}
CATEGORIES = ["Internal", "T&M", "Fixed Price"]
CATEGORY_MAP = {"internal": "Internal", "t&m": "T&M", "t & m": "T&M", "time and material": "T&M",
                "time & material": "T&M", "tm": "T&M", "fixed price": "Fixed Price", "fp": "Fixed Price",
                "fixed-price": "Fixed Price"}
COUNTRY_MAP = {"in": "IN", "india": "IN", "sg": "SG", "singapore": "SG"}
LOCATION_MAP = {"pune": "Pune", "bangalore": "Bangalore", "bengaluru": "Bangalore", "sg": "Singapore",
                "singapore": "Singapore"}
LOCATION_COUNTRY = {"Pune": "IN", "Bangalore": "IN", "Singapore": "SG"}
PROF_CAT_MAP = {
    "service operations &technology": "Service Operations & Technology",
    "service operations & technology": "Service Operations & Technology",
    "service operations and technology professions": "Service Operations & Technology",
    "business strategy & support": "Business Strategy & Support",
    "business strategy and support professions": "Business Strategy & Support",
}
EXTERNAL = ["T&M", "Fixed Price"]
MONTH_HDR = re.compile(r"^([A-Z][a-z]{2})-(\d{2})$")

ROSTER_REQUIRED = ["Position #", "Name", "Category", "Country", "City", "Start Date", "End Date", "Vendor",
                   "Profession", "Clarity Band"]
ROSTER_OPTIONAL = ["Sr No", "Last Updated By", "Team", "Project", "Gender", "Email-ID", "Functional Manager", "PO",
                   "Profession Category", "Profession Role", "Cost Center", "Daily Rate (EUR)",
                   "Duplicate? (Position + Name)", "Duplicate? (Position # + Name)", "Count Once", "FTE 2018 (total)"]

PEOPLE_COLS = ["person_key", "team", "project", "category", "country", "location", "vendor", "profession_category",
               "profession", "profession_role", "gender", "start_date", "end_date", "clarity_band", "daily_rate_eur",
               "total_fte", "zero_allocation"]
ALLOC_COLS = ["person_key", "reporting_month", "fte", "cost_eur", "team", "category", "country", "location", "vendor",
              "profession", "gender", "daily_rate_eur"]
FILTER_DIMS = {"location": "Location", "country": "Country", "category": "Resource type", "team": "Team",
               "vendor": "Vendor", "profession": "Profession"}


def _month_from_header(h: Any) -> str | None:
    if isinstance(h, (datetime, pd.Timestamp)):
        return f"{h.year:04d}-{h.month:02d}"
    m = MONTH_HDR.match(str(h).strip())
    if not m:
        return None
    mon = list(calendar.month_abbr).index(m.group(1)) if m.group(1) in calendar.month_abbr else 0
    return f"20{m.group(2)}-{mon:02d}" if mon else None


def month_end(m: str) -> date:
    y, mo = map(int, m.split("-"))
    return date(y, mo, calendar.monthrange(y, mo)[1])


def fmt_date(d: str | date) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.day} {calendar.month_abbr[d.month]} {d.year}"


def mon(m: str) -> str:
    y, mo = m.split("-")
    return f"{calendar.month_abbr[int(mo)]}-{y[2:]}"


def eur(v: float | None) -> str:
    return P.fmt_money(v, "€")


class WorkforceDomain(Domain):
    key = "workforce"
    label = "Technology workforce review"
    prefix = "wf"
    currency = "€"
    tables = {"people": PEOPLE_COLS, "allocation": ALLOC_COLS}
    filter_dimensions = FILTER_DIMS
    synonyms = [
        (r"\bpune\b", "location", "Pune"),
        (r"\bbangalore\b|\bbengaluru\b", "location", "Bangalore"),
        (r"\bsingapore\b|\bSG\b", "location", "Singapore"),
        (r"\bindia\b", "country", "IN"),
        (r"\bt\s?&\s?m\b|\btime (and|&) material\b|\bcontractors?\b", "category", "T&M"),
        (r"\binternal\b|\bpermanent staff\b", "category", "Internal"),
        (r"\bfixed[- ]price\b", "category", "Fixed Price"),
        (r"\bteam (gt|vc|gw)\b", "team", "{1}"),
        (r"\bengineering\b", "profession", "Engineering"),
        (r"\bqa\b|\btesting\b", "profession", "QA & Testing"),
        (r"\bbusiness (functional )?analys", "profession", "Business Functional Analysis"),
    ]
    detect_keywords = [r"workforce", r"headcount", r"\bfte\b", r"roster", r"contractor", r"\bt&m\b", r"vendor",
                       r"resource", r"people", r"pune", r"bangalore", r"singapore", r"india", r"staff", r"daily rate"]
    templates = {
        "workforce_review": Template(
            "workforce_review", "Technology Workforce Review (full pack)",
            ["cover", "short_version", "headcount_dashboard", "headcount_bridge", "location_trend", "fte_heatmap",
             "cost_bridge", "people_flow", "contract_cliff", "next_steps", "appendix"],
            [r"workforce review", r"full (pack|review)", r"review pack"], "#0b2545"),
        "workforce_exec_summary": Template(
            "workforce_exec_summary", "Workforce Executive Summary",
            ["cover", "short_version", "headcount_dashboard", "cost_bridge", "contract_cliff", "next_steps",
             "headcount_bridge", "location_trend", "appendix"],
            [r"exec(utive)? summary", r"summary", r"board"], "#13315c"),
        "cost_and_vendor": Template(
            "cost_and_vendor", "Cost & Vendor Review",
            ["cover", "cost_bridge", "headcount_dashboard", "location_trend", "contract_cliff", "next_steps",
             "fte_heatmap", "appendix"],
            [r"\bcost\b", r"vendor", r"spend", r"rate"], "#1d4e89"),
    }
    slide_catalog = {
        "cover": SlideType("cover", "Cover with headline numbers", [r"\bcover\b", r"title slide"],
                           "Title, scope and three headline KPIs"),
        "short_version": SlideType("short_version", "The short version", [r"short version", r"key messages",
                                   r"key takeaways", r"summary of findings"], "Four data-backed key messages"),
        "headcount_dashboard": SlideType("headcount_dashboard", "Headcount dashboard",
                                         [r"headcount dashboard", r"dashboard", r"who is on contract",
                                          r"vendor concentration", r"pareto"],
                                         "KPI tiles, location x resource type, vendor concentration"),
        "headcount_bridge": SlideType("headcount_bridge", "Headcount bridge and retention",
                                      [r"bridge", r"walk", r"retention", r"waterfall", r"roll[- ]?off"],
                                      "Monthly people walk and retention by resource type"),
        "location_trend": SlideType("location_trend", "FTE by location", [r"location", r"by city", r"india",
                                    r"daily rate", r"small multiples"], "FTE trend per location"),
        "fte_heatmap": SlideType("fte_heatmap", "FTE heatmap", [r"heat ?map", r"month by month"],
                                 "Location x resource type x month FTE heatmap"),
        "cost_bridge": SlideType("cost_bridge", "Cost run-rate bridge", [r"cost bridge", r"\bcost\b", r"run[- ]rate",
                                 r"spend"], "Monthly cost run-rate bridge"),
        "people_flow": SlideType("people_flow", "Where everyone ended up", [r"ended up", r"flow", r"sankey",
                                 r"attrition", r"leavers"], "Outcome of every person in scope"),
        "contract_cliff": SlideType("contract_cliff", "Contract end dates", [r"contract end", r"cliff",
                                    r"renew", r"end dates?", r"expir"], "When contracts end + renew/convert/release"),
        "profession_mix": SlideType("profession_mix", "Profession mix", [r"profession", r"roles?\b", r"skills?"],
                                    "Headcount by profession and role"),
        "data_quality": SlideType("data_quality", "Data quality checks", [r"data quality", r"\bdq\b",
                                  r"duplicates?"], "Validation checks on the roster"),
        "next_steps": SlideType("next_steps", "Next steps", [r"next steps", r"recommendations?", r"actions?"],
                                "Proposed actions with dates"),
        "commentary": SlideType("commentary", "Management commentary", [r"commentary", r"narrative"],
                                "Editable management commentary"),
        "appendix": SlideType("appendix", "Appendix: method and provenance", [r"appendix", r"methodology",
                              r"assumptions", r"provenance"], "Definitions, assumptions and data lineage"),
    }
    examples = [
        "Create the full workforce review pack for 2018 with speaker notes",
        "Six-slide executive summary of India T&M headcount for Q1 2018, compare Q2",
        "Cost and vendor review for Singapore as of September 2018, add a data quality slide",
    ]
    followup_examples = ["Change to Singapore", "Show only T&M", "Add Bangalore", "Compare Q1",
                         "Replace slide 4 with the FTE heatmap", "Add a profession mix slide", "Make a fresh copy",
                         "Refresh using latest data"]

    def display(self, value: str) -> str:
        return {"IN": "India", "SG": "Singapore"}.get(value, value)

    # ------------------------------------------------------------------ load + validate
    def load(self, raw: pd.DataFrame, source: str, synthetic: bool, registry, snapshot: str | None = None) -> Dataset:
        rep = ValidationReport(source)
        df = raw.dropna(how="all").copy()
        df = df[[c for c in df.columns if not str(c).startswith("Unnamed")]]
        rep.rows_in = len(df)
        month_cols = {c: _month_from_header(c) for c in df.columns}
        month_cols = {c: m for c, m in month_cols.items() if m}
        allowed = ROSTER_REQUIRED + ROSTER_OPTIONAL + list(month_cols)
        ok = check_columns(df, ROSTER_REQUIRED, allowed, rep)
        if not month_cols:
            rep.add("Monthly allocation columns", "error", 1, "no columns named like Jan-18 ... Dec-18 were found")
            ok = False
        if not ok:
            return self._failed(rep, source, synthetic, registry)
        rep.add("Monthly allocation columns", "ok", 0, f"{len(month_cols)} months: "
                f"{mon(min(month_cols.values()))} to {mon(max(month_cols.values()))}")

        for c in ["Team", "Project", "Gender", "Email-ID", "Functional Manager", "PO", "Profession Category",
                  "Profession Role", "Sr No", "Daily Rate (EUR)"]:
            if c not in df.columns:
                df[c] = None
        text_cols = ["Team", "Project", "Category", "Name", "Country", "City", "Vendor", "PO", "Functional Manager",
                     "Profession Category", "Profession", "Profession Role", "Clarity Band", "Gender", "Email-ID"]
        formula_hits = 0
        for c in text_cols:
            df[c], n = strip_formula_text(df[c].astype("string"))
            formula_hits += n
        if formula_hits:
            rep.add("Formula-like text neutralised", "warning", formula_hits,
                    "cells starting with = + - @ were stored as plain text")
        for c, label in [("Project", "Project names"), ("PO", "PO numbers"),
                         ("Functional Manager", "Manager names")]:
            s = df[c].dropna()
            n = int((s != s.str.strip()).sum())
            rep.add(f"{label} with extra spaces", "warning", n, "trimmed on load; fix at source")
        n_email = int(df["Email-ID"].fillna("").str.contains(" ").sum())
        rep.add("E-mail IDs containing a space", "warning", n_email,
                "e-mail is not carried into the curated tables (data minimisation)")
        df["Position #"] = df["Position #"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
        for c in text_cols:
            df[c] = df[c].str.strip()

        q: list[pd.DataFrame] = []
        df = quarantine_rows(df, df["Position #"].isna() | df["Name"].isna(), "missing Position # or Name", q)
        df["category_n"] = df["Category"].str.lower().map(CATEGORY_MAP)
        df = quarantine_rows(df, df["category_n"].isna(), "unknown resource category", q)
        df["country_n"] = df["Country"].str.lower().map(COUNTRY_MAP)
        df["location_n"] = df["City"].str.lower().map(LOCATION_MAP)
        df = quarantine_rows(df, df["country_n"].isna() | df["location_n"].isna(), "unknown country or city", q)
        df["start"] = pd.to_datetime(df["Start Date"], errors="coerce")
        df["end"] = pd.to_datetime(df["End Date"], errors="coerce")
        df = quarantine_rows(df, df["start"].isna() | df["end"].isna(), "invalid start or end date", q)
        df = quarantine_rows(df, df["end"] < df["start"], "end date before start date", q)
        alloc = df[list(month_cols)].apply(pd.to_numeric, errors="coerce")
        bad_alloc = alloc.isna().any(axis=1) | (alloc < 0).any(axis=1) | (alloc > 1.0001).any(axis=1)
        df = quarantine_rows(df, bad_alloc, "allocation missing or outside 0-1", q)
        alloc = alloc.loc[df.index]
        rep.add("Rows quarantined", "quarantine", sum(len(x) for x in q),
                "see quarantine list; excluded from every figure")

        pcat = df["Profession Category"].fillna("").str.lower()
        n_pcat = int((pcat.str.contains(" and ") | pcat.str.endswith("professions")).sum())
        rep.add("Non-standard Profession Category", "warning", n_pcat, "mapped to the standard spelling")
        dup = df.duplicated(["Position #", "Name"], keep=False)
        rep.add("Duplicate rows (Position # + Name)", "warning", int(dup.sum()), "counted once (first row kept)")
        if df["Sr No"].notna().any():
            rep.add("Duplicate Sr No", "warning", int(df["Sr No"].dropna().duplicated(keep=False).sum()),
                    "Sr No is not used as a key")
        keep = ~df.duplicated(["Position #", "Name"], keep="first")
        df, alloc = df[keep], alloc[keep]

        rate = pd.to_numeric(df["Daily Rate (EUR)"], errors="coerce")
        rate = rate.fillna(df["Clarity Band"].map(RATE_CARD_EUR))
        rep.add("Unknown Clarity Band (no daily rate)", "warning", int(rate.isna().sum()),
                "cost for these people is excluded and disclosed")
        total = alloc.sum(axis=1)
        rep.add("Contracts with zero allocation all year", "warning", int((total == 0).sum()),
                "start/end dates set but every monthly allocation is 0")

        key = [("P" + hashlib.sha256(f"{p}|{n}".encode()).hexdigest()[:8]) for p, n in zip(df["Position #"],
                                                                                         df["Name"], strict=True)]
        people = pd.DataFrame({
            "person_key": key, "team": df["Team"].fillna("(blank)").values,
            "project": df["Project"].fillna("(blank)").values, "category": df["category_n"].values,
            "country": df["country_n"].values, "location": df["location_n"].values,
            "vendor": df["Vendor"].fillna("(none)").values,
            "profession_category": df["Profession Category"].fillna("").str.lower().map(PROF_CAT_MAP).fillna(
                df["Profession Category"].fillna("(blank)")).values,
            "profession": df["Profession"].fillna("(blank)").values,
            "profession_role": df["Profession Role"].fillna("(blank)").values,
            "gender": df["Gender"].fillna("(blank)").str.upper().values,
            "start_date": df["start"].dt.strftime("%Y-%m-%d").values,
            "end_date": df["end"].dt.strftime("%Y-%m-%d").values,
            "clarity_band": df["Clarity Band"].values, "daily_rate_eur": rate.values,
            "total_fte": total.round(6).values, "zero_allocation": (total == 0).values,
        })
        long = alloc.copy()
        long.columns = [month_cols[c] for c in long.columns]
        long.insert(0, "person_key", key)
        long = long.melt(id_vars="person_key", var_name="reporting_month", value_name="fte")
        long = long.merge(people[["person_key", "team", "category", "country", "location", "vendor", "profession",
                                  "gender", "daily_rate_eur"]], on="person_key")
        long["cost_eur"] = long["fte"] * WORKING_DAYS * long["daily_rate_eur"]
        long = long[ALLOC_COLS].sort_values(["reporting_month", "person_key"]).reset_index(drop=True)
        rep.rows_accepted = len(people)
        rep.quarantine = pd.concat(q) if q else None
        tables = {"people": people, "allocation": long}
        sha = content_hash(tables)
        months = sorted(set(month_cols.values()))
        if snapshot is None:
            snapshot = month_end(months[-1]).isoformat()
        ds = Dataset(self.key, registry.next_version(self.prefix, sha), tables, months, rep, source, synthetic, sha,
                     dim_table="people", snapshot=snapshot)
        if not len(people):
            rep.add("Usable rows", "error", 1, "no rows passed validation")
        return ds

    def _failed(self, rep, source, synthetic, registry) -> Dataset:
        sha = hashlib.sha256(source.encode()).hexdigest()
        return Dataset(self.key, registry.next_version(self.prefix, sha), {"people": pd.DataFrame(columns=PEOPLE_COLS),
                       "allocation": pd.DataFrame(columns=ALLOC_COLS)}, [], rep, source, synthetic, sha,
                       dim_table="people")

    def default_months(self, ds: Dataset) -> list[str]:
        year = ds.months[-1][:4]
        return [m for m in ds.months if m.startswith(year)]

    # ------------------------------------------------------------------ KPI dictionary
    def kpi_dictionary(self) -> pd.DataFrame:
        rows = [
            ("people_on_contract", "People on contract", "Count of unique Position # + Name with Start Date <= as-of "
             "date <= End Date", "Point in time (as-of date)", "Count distinct; never summed across months"),
            ("people_allocated", "People allocated", "Count of people with allocation > 0 in the month", "Month",
             "Not additive across months"),
            ("fte", "FTE", "Sum of monthly allocations", "Person-month",
             "Additive across people; average across months"),
            ("monthly_cost_eur", "Monthly cost run-rate (EUR)", "FTE x 22 working days x daily rate", "Month",
             "Additive across people and months (period cost = sum)"),
            ("tm_share", "T&M share", "T&M people on contract / people on contract", "Point in time",
             "Ratio of sums; divide-by-zero returns n/a"),
            ("women_share", "Women in the team", "People on contract with Gender = F / people on contract",
             "Point in time", "Ratio of sums"),
            ("avg_daily_rate", "Average daily rate", "FTE-weighted average daily rate in the month", "Month",
             "Weighted by FTE, never a simple average of rates"),
            ("vendor_concentration", "Top-2 vendor share", "External people on contract supplied by the two largest "
             "vendors / all external people on contract", "Point in time", "External = T&M + Fixed Price"),
            ("retention", "Retention", "Share of the base-month allocated team still allocated in each month",
             "Month", "Cohort fixed at the base month"),
            ("contracts_ending", "Contracts ending", "People on contract grouped by End Date", "Point in time",
             "Count distinct"),
            ("zero_allocation", "Zero-allocation contracts", "Contracts whose monthly allocations are all 0",
             "Dataset", "Flag for data clean-up"),
        ]
        return pd.DataFrame(rows, columns=["metric_id", "name", "definition", "grain", "aggregation_rule"])

    # ------------------------------------------------------------------ analytics helpers
    def _people(self, ctx: Context) -> pd.DataFrame:
        return ctx.memo("people", lambda: ctx.query("people", PEOPLE_COLS, ctx.filters))

    def _alloc(self, ctx: Context) -> pd.DataFrame:
        return ctx.memo("alloc", lambda: ctx.query("allocation", ALLOC_COLS, ctx.filters, ctx.dataset.months))

    def asof(self, ctx: Context, months: list[str] | None = None) -> date:
        months = months or ctx.months
        d = month_end(sorted(months)[-1])
        if ctx.dataset.snapshot:
            d = min(d, date.fromisoformat(ctx.dataset.snapshot))
        return d

    @staticmethod
    def on_contract(people: pd.DataFrame, when: date) -> pd.DataFrame:
        w = when.isoformat()
        return people[(people["start_date"] <= w) & (people["end_date"] >= w)]

    def monthly(self, ctx: Context) -> pd.DataFrame:
        def build() -> pd.DataFrame:
            a = self._alloc(ctx)
            g = a.groupby("reporting_month").agg(fte=("fte", "sum"), cost=("cost_eur", "sum"),
                                                 people=("fte", lambda s: int((s > 0).sum())))
            return g.reindex(ctx.dataset.months).fillna(0)
        return ctx.memo("monthly", build)

    def _planned_note(self, ctx: Context) -> str:
        snap = ctx.dataset.snapshot
        if not snap:
            return ""
        later = [m for m in ctx.months if month_end(m) > date.fromisoformat(snap)]
        if later:
            return (f" Months after the roster date ({fmt_date(snap)}): {mon(later[0])} to {mon(later[-1])} are "
                    "planned allocations, not actuals.")
        return ""

    def _src(self, ctx: Context) -> str:
        ds = ctx.dataset
        kind = "synthetic roster" if ds.synthetic else "uploaded roster"
        return f"Source: {kind} {ds.source} ({ds.version}), duplicates counted once."

    # ------------------------------------------------------------------ slides
    def build_slide(self, slide_type: str, ctx: Context, no: int, total: int) -> dict[str, Any]:
        fn = getattr(self, f"_s_{slide_type}")
        return fn(ctx)

    def _headline(self, ctx: Context) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            people = self._people(ctx)
            asof = self.asof(ctx)
            onc = self.on_contract(people, asof)
            mth = self.monthly(ctx)
            m0, m1 = ctx.months[0], ctx.months[-1]
            fte0, fte1 = float(mth.loc[m0, "fte"]), float(mth.loc[m1, "fte"])
            ends = onc["end_date"].value_counts()
            cliff_date, cliff_n = (ends.index[0], int(ends.iloc[0])) if len(ends) else (None, 0)
            ext = onc[onc["category"].isin(EXTERNAL)]
            vend = ext["vendor"].value_counts()
            top2 = int(vend.iloc[:2].sum()) if len(vend) else 0
            return {"asof": asof, "unique": len(people), "on_contract": len(onc), "onc": onc,
                    "fte0": fte0, "fte1": fte1, "fte_chg": P.pct_change(fte1, fte0), "cliff_date": cliff_date,
                    "cliff_n": cliff_n, "ext": len(ext), "vend": vend, "top2": top2,
                    "top2_share": P.safe_div(top2, len(ext)), "m0": m0, "m1": m1}
        return ctx.memo("headline", build)

    def _s_cover(self, ctx: Context) -> dict[str, Any]:
        h = self._headline(ctx)
        fl = ctx.filters_label(FILTER_DIMS)
        items = [
            P.kpi("people on contract today", P.fmt_int(h["on_contract"]), f"as of {fmt_date(h['asof'])}", "blue"),
            P.kpi(f"FTE since {mon(h['m0'])}", P.fmt_pp(h["fte_chg"], 0).replace(" pp", "%")
                  if h["fte_chg"] is not None else "n/a", f"{P.fmt_num(h['fte0'], 0)} to {P.fmt_num(h['fte1'], 0)} FTE"
                  f" by {mon(h['m1'])}", "red" if (h["fte_chg"] or 0) < 0 else "green"),
            P.kpi(f"contracts end on {fmt_date(h['cliff_date'])}" if h["cliff_date"] else "contracts ending",
                  P.fmt_int(h["cliff_n"]), "people on contract with that end date", "amber"),
        ]
        com = [
            P.fact(f"{h['on_contract']} people were on contract on {fmt_date(h['asof'])}, from {h['unique']} people "
                   f"on the roster.", "people_on_contract", h["on_contract"], fmt_date(h["asof"]), fl),
            P.fact(f"FTE moved from {h['fte0']:.0f} in {mon(h['m0'])} to {h['fte1']:.0f} in {mon(h['m1'])} "
                   f"({P.fmt_pct(h['fte_chg'], 0)}).", "fte", h["fte1"], f"{mon(h['m0'])} vs {mon(h['m1'])}", fl),
        ]
        if h["cliff_date"]:
            com.append(P.fact(f"{h['cliff_n']} people on contract share the end date {fmt_date(h['cliff_date'])}.",
                              "contracts_ending", h["cliff_n"], fmt_date(h["asof"]), fl))
        sub = f"{ctx.period} · {fl} · Headcount data as of {fmt_date(h['asof'])}"
        return P.slide("cover", ctx.spec.title, sub, "cover", [P.kpis(items)], com,
                       self._src(ctx) + self._planned_note(ctx))

    def _location_change(self, ctx: Context) -> pd.DataFrame:
        a = self._alloc(ctx)
        m0, m1 = ctx.months[0], ctx.months[-1]
        g = a[a["reporting_month"].isin([m0, m1])].pivot_table(index="location", columns="reporting_month",
                                                               values="fte", aggfunc="sum").fillna(0)
        for m in (m0, m1):
            if m not in g.columns:
                g[m] = 0.0
        g["chg"] = g[m1] - g[m0]
        return g.sort_values("chg")

    def _s_short_version(self, ctx: Context) -> dict[str, Any]:
        h = self._headline(ctx)
        fl = ctx.filters_label(FILTER_DIMS)
        mth = self.monthly(ctx).loc[ctx.months]
        diffs = mth["fte"].diff().dropna()
        cards, com = [], []
        if len(diffs):
            worst = diffs.idxmin()
            chg = h["fte_chg"] or 0
            head = (f"We've {'shrunk' if chg < 0 else 'grown'} by {abs(chg * 100):.0f}% since {mon(h['m0'])}"
                    if abs(chg) >= 0.02 else f"FTE broadly flat since {mon(h['m0'])}")
            body = f"FTE went from about {h['fte0']:.0f} to {h['fte1']:.0f}."
            if abs(diffs.min()) >= 0.5:
                body += f" The largest monthly fall was {diffs.min():+.0f} FTE into {mon(worst)}."
            cards.append({"head": head, "body": body})
            com.append(P.fact(f"{head}. {body}", "fte", round(h["fte1"], 1), f"{mon(h['m0'])}–{mon(h['m1'])}", fl))
        loc = self._location_change(ctx)
        if len(loc):
            big = loc.index[0]
            parts = ", ".join(f"{i} {r[ctx.months[0]]:.0f}→{r[ctx.months[-1]]:.0f}" for i, r in loc.iterrows())
            total_chg = loc["chg"].sum()
            share = P.safe_div(loc.loc[big, "chg"], total_chg) if abs(total_chg) >= 1 else None
            head = (f"{big} carried {P.fmt_pct(share, 0)} of the change" if share is not None
                    else "No material change by location")
            cards.append({"head": head, "body": f"FTE by location: {parts}."})
            com.append(P.fact(f"{head} (FTE by location: {parts}).", "fte_by_location", round(loc.loc[big, 'chg'], 1),
                              f"{mon(h['m0'])} vs {mon(h['m1'])}", fl))
        if h["ext"]:
            top = list(h["vend"].index[:2])
            head = f"Two vendors supply {P.fmt_pct(h['top2_share'], 0)} of external people"
            body = (f"{' and '.join(top)} provide {h['top2']} of our {h['ext']} external people on contract. "
                    "That is concentration risk at renewal.")
            cards.append({"head": head, "body": body})
            com.append(P.fact(body, "vendor_concentration", h["top2"], fmt_date(h["asof"]), fl))
        else:
            cards.append({"head": "No external resources in scope", "body": "The current filters contain no T&M or "
                          "Fixed Price people on contract, so vendor concentration does not apply."})
        if h["cliff_date"]:
            head = f"{h['cliff_n']} contracts end on {fmt_date(h['cliff_date'])}"
            body = ("They roll off on the same day unless we act. A renew, convert or release plan is needed "
                    "before then.")
            cards.append({"head": head, "body": body})
            com.append(P.fact(f"{head}. {body}", "contracts_ending", h["cliff_n"], fmt_date(h["asof"]), fl))
        return P.slide("short_version", "The short version", "Four things worth knowing before we go into the "
                       "detail", "cards", [P.cards(cards[:4])], com, self._src(ctx) + " 'On contract' = start date on "
                       "or before the as-of date and end date on or after it.")

    def _s_headcount_dashboard(self, ctx: Context) -> dict[str, Any]:
        h = self._headline(ctx)
        fl = ctx.filters_label(FILTER_DIMS)
        onc = h["onc"]
        n = len(onc)
        cat = onc["category"].value_counts()
        loc = onc["location"].value_counts()
        mth = self.monthly(ctx)
        asof_m = h["asof"].strftime("%Y-%m")
        run = float(mth.loc[asof_m, "cost"]) if asof_m in mth.index else None
        peak_m = mth.loc[ctx.months, "cost"].idxmax()
        women = int((onc["gender"] == "F").sum())
        top_loc = loc.index[0] if len(loc) else "n/a"
        items = [
            P.kpi("People on contract", P.fmt_int(n), f"from {h['unique']} people across the roster", "blue"),
            P.kpi("Are T&M contractors", P.fmt_pct(P.safe_div(cat.get("T&M", 0), n), 0),
                  ", ".join(f"{int(cat.get(c, 0))} {c.lower() if c != 'T&M' else c}" for c in CATEGORIES)),
            P.kpi(f"Based in {top_loc}", P.fmt_pct(P.safe_div(loc.iloc[0], n), 0) if len(loc) else "n/a",
                  ", ".join(f"{int(v)} {k}" for k, v in loc.items())),
            P.kpi("Monthly cost run-rate", eur(run), f"{mon(asof_m)}; peak {eur(mth.loc[peak_m, 'cost'])} in "
                  f"{mon(peak_m)}"),
            P.kpi("Women in the team", P.fmt_pct(P.safe_div(women, n), 0), f"{women} of {n} people"),
        ]
        ct = pd.crosstab(onc["location"], onc["category"]).reindex(columns=CATEGORIES, fill_value=0)
        ch1 = P.chart("bar_stacked", "People on contract: location x resource type", list(ct.index),
                      [{"name": c, "values": ct[c].tolist()} for c in CATEGORIES])
        vend = h["vend"].head(8)
        cum = (vend.cumsum() / max(h["ext"], 1)).round(3)
        ch2 = P.chart("bar", "External people by vendor (Pareto)", list(vend.index)[::-1],
                      [{"name": "External people", "values": vend.tolist()[::-1]}],
                      data_labels=True, note="Cum. share: " + ", ".join(f"{k} {v:.0%}" for k, v in cum.items()))
        com = [
            P.fact(f"{n} people on contract on {fmt_date(h['asof'])}; {int(cat.get('T&M', 0))} T&M, "
                   f"{int(cat.get('Internal', 0))} internal, {int(cat.get('Fixed Price', 0))} fixed price.",
                   "people_on_contract", n, fmt_date(h["asof"]), fl),
            P.fact(f"Monthly run-rate {eur(run)} in {mon(asof_m)} against a peak of {eur(mth.loc[peak_m, 'cost'])} "
                   f"in {mon(peak_m)}.", "monthly_cost_eur", round(run or 0), mon(asof_m), fl),
        ]
        if h["ext"]:
            com.append(P.fact(f"Top two vendors supply {h['top2']} of {h['ext']} external people "
                              f"({P.fmt_pct(h['top2_share'], 0)}).", "vendor_concentration", h["top2"],
                              fmt_date(h["asof"]), fl))
        return P.slide("headcount_dashboard", "Headcount dashboard",
                       f"Who is on contract as of {fmt_date(h['asof'])}, and how the team is put together",
                       "kpis+2charts", [P.kpis(items), ch1, ch2], com,
                       self._src(ctx) + " Pareto: cum. = cumulative share of external people on contract.")

    def _s_headcount_bridge(self, ctx: Context) -> dict[str, Any]:
        fl = ctx.filters_label(FILTER_DIMS)
        a = self._alloc(ctx)
        a = a[a["reporting_month"].isin(ctx.months)]
        pm = a[a["fte"] > 0].groupby("reporting_month")["person_key"].apply(set).reindex(ctx.months)
        pm = pm.apply(lambda s: s if isinstance(s, set) else set())
        cats = [mon(ctx.months[0])]
        deltas = [len(pm.iloc[0])]
        for i in range(1, len(ctx.months)):
            cats.append(mon(ctx.months[i]))
            deltas.append(len(pm.iloc[i]) - len(pm.iloc[i - 1]))
        cats.append(f"{mon(ctx.months[-1])} end")
        wf = P.chart("waterfall", "People allocated: month-by-month walk", cats,
                     [{"name": "People", "values": deltas + [len(pm.iloc[-1])]}])
        base_m = ctx.months[0]
        base = a[(a["reporting_month"] == base_m) & (a["fte"] > 0)][["person_key", "category"]]
        series = []
        ret_end = {}
        for c in CATEGORIES:
            keys = set(base[base["category"] == c]["person_key"])
            if not keys:
                continue
            vals = [P.safe_div(len(keys & pm.loc[m]), len(keys)) for m in ctx.months]
            series.append({"name": c, "values": vals})
            ret_end[c] = vals[-1]
        rt = P.chart("line", f"Retention by resource type ({mon(base_m)} cohort)", [mon(m) for m in ctx.months],
                     series, number_format="0%")
        diffs = [(cats[i], deltas[i]) for i in range(1, len(ctx.months))]
        worst = min(diffs, key=lambda x: x[1]) if diffs else (cats[0], 0)
        com = [P.fact(f"People allocated went from {deltas[0]} in {cats[0]} to {len(pm.iloc[-1])} in "
                      f"{mon(ctx.months[-1])}; the biggest single move was {worst[1]:+d} in {worst[0]}.",
                      "people_allocated", len(pm.iloc[-1]), ctx.period, fl)]
        if ret_end:
            txt = ", ".join(f"{P.fmt_pct(v, 0)} of {k}" for k, v in ret_end.items())
            com.append(P.fact(f"Of the {mon(base_m)} team, {txt} are still allocated in {mon(ctx.months[-1])}.",
                              "retention", round(min(ret_end.values()) or 0, 3), ctx.period, fl))
        title = "How we got here: a headcount bridge"
        return P.slide("headcount_bridge", title, "Monthly joiners and leavers, and who stayed",
                       "2charts", [wf, rt], com, self._src(ctx) + " A person counts in a month if allocation > 0. "
                       "Retention = share of the base-month team still allocated." + self._planned_note(ctx))

    def _s_location_trend(self, ctx: Context) -> dict[str, Any]:
        fl = ctx.filters_label(FILTER_DIMS)
        a = self._alloc(ctx)
        a = a[a["reporting_month"].isin(ctx.months)]
        pv = a.pivot_table(index="reporting_month", columns="location", values="fte", aggfunc="sum").reindex(
            ctx.months).fillna(0)
        ch = P.chart("line", "FTE by location", [mon(m) for m in ctx.months],
                     [{"name": c, "values": pv[c].round(1).tolist()} for c in pv.columns], number_format="0")
        items, com = [], []
        for c in pv.columns[:3]:
            ch_pct = P.pct_change(pv[c].iloc[-1], pv[c].iloc[0])
            items.append(P.kpi(f"{c} FTE, {mon(ctx.months[0])} to {mon(ctx.months[-1])}",
                               P.fmt_pct(ch_pct, 0) if ch_pct is not None else "n/a",
                               f"{pv[c].iloc[0]:.0f} to {pv[c].iloc[-1]:.0f} FTE",
                               "red" if (ch_pct or 0) < -0.1 else "green"))
            com.append(P.fact(f"{c} FTE went from {pv[c].iloc[0]:.0f} to {pv[c].iloc[-1]:.0f} "
                              f"({P.fmt_pct(ch_pct, 0)}).", "fte_by_location", round(pv[c].iloc[-1], 1),
                              f"{mon(ctx.months[0])} vs {mon(ctx.months[-1])}", fl))

        def rate(m: str) -> float | None:
            x = a[a["reporting_month"] == m]
            return P.safe_div((x["fte"] * x["daily_rate_eur"]).sum(), x["fte"].sum())
        r0, r1 = rate(ctx.months[0]), rate(ctx.months[-1])
        items.append(P.kpi("Average daily rate (FTE-weighted)", f"€{r0:.0f} → €{r1:.0f}" if r0 and r1 else "n/a",
                           "lower-rate capacity leaving raises the average" if (r0 and r1 and r1 > r0) else ""))
        if r0 and r1:
            com.append(P.fact(f"FTE-weighted average daily rate moved from €{r0:.0f} to €{r1:.0f}.",
                              "avg_daily_rate", round(r1, 1), f"{mon(ctx.months[0])} vs {mon(ctx.months[-1])}", fl))
        com.append(P.fact("Where a location loses most of its team, knowledge of critical systems may now sit "
                          "elsewhere; this is a hypothesis to test with line managers.", "fte_by_location", None,
                          ctx.period, fl, kind="hypothesis"))
        return P.slide("location_trend", "Where the change landed: FTE by location",
                       "Same scale for every location", "kpis+chart", [P.kpis(items), ch], com,
                       self._src(ctx) + " FTE = sum of monthly allocations." + self._planned_note(ctx))

    def _s_fte_heatmap(self, ctx: Context) -> dict[str, Any]:
        fl = ctx.filters_label(FILTER_DIMS)
        a = self._alloc(ctx)
        a = a[a["reporting_month"].isin(ctx.months)]
        pv = a.pivot_table(index=["location", "category"], columns="reporting_month", values="fte",
                           aggfunc="sum").reindex(columns=ctx.months).fillna(0)
        pv = pv[pv.sum(axis=1) > 0]
        rows = [[f"{loc} · {cat}"] + [round(float(v), 1) if v else None for v in r] for (loc, cat), r in pv.iterrows()]
        tbl = P.table("FTE by location, resource type and month", ["Location · type"] + [mon(m) for m in ctx.months],
                      rows, heat={"from_col": 1, "max": float(pv.values.max()) if pv.size else 1.0})
        com = []
        if len(pv):
            peak = pv.max(axis=1).idxmax()
            last = pv.iloc[:, -1]
            com.append(P.fact(f"{peak[0]} {peak[1]} was the largest block at {pv.loc[peak].max():.0f} FTE and is "
                              f"{last.loc[peak]:.0f} FTE in {mon(ctx.months[-1])}.", "fte",
                              round(pv.loc[peak].max(), 1),
                              ctx.period, fl))
            rng = (pv.max(axis=1) - pv.min(axis=1)).sort_values()
            st = rng.index[0]
            com.append(P.fact(f"{st[0]} {st[1]} was the most stable row (range {rng.iloc[0]:.1f} FTE).", "fte",
                              round(rng.iloc[0], 1), ctx.period, fl))
        return P.slide("fte_heatmap", "Month by month: where the FTE sat",
                       "Darker cells = more FTE; blank = no allocation", "table+comm", [tbl], com,
                       self._src(ctx) + self._planned_note(ctx))

    def _s_cost_bridge(self, ctx: Context) -> dict[str, Any]:
        fl = ctx.filters_label(FILTER_DIMS)
        a = self._alloc(ctx)
        m0, m1 = ctx.months[0], ctx.months[-1]
        g = a[a["reporting_month"].isin([m0, m1])].pivot_table(index=["location", "category"],
                                                               columns="reporting_month", values="cost_eur",
                                                               aggfunc="sum").fillna(0)
        for m in (m0, m1):
            if m not in g.columns:
                g[m] = 0.0
        g["chg"] = g[m1] - g[m0]
        g = g[g["chg"].abs() > 0.5].sort_values("chg")
        c0, c1 = float(a[a["reporting_month"] == m0]["cost_eur"].sum()), float(
            a[a["reporting_month"] == m1]["cost_eur"].sum())
        cats = [f"{mon(m0)} run-rate"] + [f"{loc} {cat}" for loc, cat in g.index] + [f"{mon(m1)} run-rate"]
        vals = [c0] + g["chg"].round(0).tolist() + [c1]
        wf = P.chart("waterfall", f"Monthly cost run-rate bridge, {mon(m0)} to {mon(m1)} (EUR)", cats,
                     [{"name": "EUR", "values": vals}], number_format="#,##0")
        tm = a[(a["reporting_month"].isin(ctx.months)) & (a["category"] == "T&M")]
        tm_cost = float(tm["cost_eur"].sum())
        loc_chg = g.groupby(level=0)["chg"].sum().sort_values()
        items = [P.kpi("monthly run-rate", P.fmt_pct(P.pct_change(c1, c0), 0), f"{eur(c0)} in {mon(m0)}, {eur(c1)} in "
                       f"{mon(m1)}", "green" if c1 < c0 else "red")]
        if len(loc_chg):
            items.append(P.kpi(f"change each month in {loc_chg.index[0]}", eur(loc_chg.iloc[0]),
                               ", ".join(f"{cat} {eur(v)}" for (loc, cat), v in g["chg"].items()
                                         if loc == loc_chg.index[0])))
        items.append(P.kpi(f"T&M cost, {ctx.period}", eur(tm_cost), f"{tm['person_key'].nunique()} contractors, "
                           f"{WORKING_DAYS} days a month"))
        com = [P.fact(f"Monthly run-rate moved from {eur(c0)} to {eur(c1)} ({P.fmt_pct(P.pct_change(c1, c0), 0)}).",
                      "monthly_cost_eur", round(c1), f"{mon(m0)} vs {mon(m1)}", fl),
               P.fact(f"T&M cost over {ctx.period} was {eur(tm_cost)} (EUR {tm_cost:,.2f}).", "monthly_cost_eur",
                      round(tm_cost, 2), ctx.period, fl)]
        if len(loc_chg):
            share = P.safe_div(loc_chg.iloc[0], c1 - c0)
            com.append(P.fact(f"{loc_chg.index[0]} accounts for {P.fmt_pct(share, 0)} of the run-rate change.",
                              "monthly_cost_eur", round(loc_chg.iloc[0]), f"{mon(m0)} vs {mon(m1)}", fl))
        return P.slide("cost_bridge", "What the change did to cost",
                       f"Monthly run-rate {mon(m0)} to {mon(m1)}, by location and resource type", "kpis+chart",
                       [P.kpis(items), wf], com,
                       self._src(ctx) + f" Run-rate = monthly FTE x {WORKING_DAYS} working days"
                       " x daily rate (EUR); no FX, tax or vendor mark-up." + self._planned_note(ctx))

    def _outcomes(self, ctx: Context) -> pd.DataFrame:
        people = self._people(ctx)
        a = self._alloc(ctx)
        a = a[a["reporting_month"].isin(ctx.months)]
        last = a[a["fte"] > 0].groupby("person_key")["reporting_month"].max()
        out = people[["person_key", "location", "category"]].copy()
        out["last"] = out["person_key"].map(last)
        m1 = ctx.months[-1]

        def label(x: Any) -> str:
            if not isinstance(x, str):
                return "Never allocated"
            if x == m1:
                return f"Still allocated in {mon(m1)}"
            q = (int(x[5:]) - 1) // 3 + 1
            return f"Left in Q{q} {x[:4]}"
        out["outcome"] = out["last"].map(label)
        return out

    def _s_people_flow(self, ctx: Context) -> dict[str, Any]:
        fl = ctx.filters_label(FILTER_DIMS)
        out = self._outcomes(ctx)
        order = sorted(out["outcome"].unique(), key=lambda s: (s.startswith("Never"), s.startswith("Still"), s))
        ct = pd.crosstab(out["location"], out["outcome"]).reindex(columns=order, fill_value=0)
        ch = P.chart("bar_stacked", "Outcome of every person by location", list(ct.index),
                     [{"name": o, "values": ct[o].tolist()} for o in order])
        ct2 = pd.crosstab(out["outcome"], out["category"]).reindex(index=order, columns=CATEGORIES, fill_value=0)
        rows = [[o] + [int(v) for v in r] + [int(r.sum())] for o, r in ct2.iterrows()]
        tbl = P.table("Outcome x resource type (people)", ["Outcome"] + CATEGORIES + ["Total"], rows)
        n = len(out)
        com = []
        for o in order:
            k = int((out["outcome"] == o).sum())
            com.append(P.fact(f"{o}: {k} of {n} people ({P.fmt_pct(P.safe_div(k, n), 0)}).", "people", k,
                              ctx.period, fl))
        return P.slide("people_flow", "Where everyone ended up",
                       f"Following all {n} people in scope from location and resource type to outcome",
                       "chart+table", [ch, tbl], com[:5], self._src(ctx) + " 'Never allocated' = contract dates "
                       "set but every monthly allocation is zero. Native stacked bar used instead of a Sankey so the "
                       "chart stays editable.")

    def _s_contract_cliff(self, ctx: Context) -> dict[str, Any]:
        fl = ctx.filters_label(FILTER_DIMS)
        h = self._headline(ctx)
        onc = h["onc"]
        ends = onc.groupby(onc["end_date"].str[:7]).size().sort_index()
        ch = P.chart("column", "When contracts end: people on contract today", [mon(m) for m in ends.index],
                     [{"name": "People", "values": ends.tolist()}], data_labels=True)
        zero = int(onc["zero_allocation"].sum())
        asof = h["asof"]
        long_tm = int(((onc["category"] == "T&M") & (onc["start_date"] <= (asof - timedelta(days=180)).isoformat())
                       ).sum())
        cards_ = [
            {"head": "Renew", "body": "Keep the people who run critical systems; use the renewal to renegotiate rates "
             f"with the top vendors ({', '.join(h['vend'].index[:2]) or 'n/a'})."},
            {"head": "Convert", "body": f"{long_tm} T&M contractors have been with us 6+ months; convert where the "
             "role is permanent (cost case to be confirmed)."},
            {"head": "Release", "body": f"Let roles go where the project has ended, starting with the {zero} "
             "people on contract with zero allocation."},
        ]
        com = [P.fact(f"{h['cliff_n']} of {len(onc)} people on contract end on {fmt_date(h['cliff_date'])}."
                      if h["cliff_date"] else "No contracts in scope.", "contracts_ending", h["cliff_n"],
                      fmt_date(asof), fl),
               P.fact(f"{zero} people on contract show zero allocation in every month: either not working on these "
                      "projects or the tracker is out of date.", "zero_allocation", zero, ctx.period, fl),
               P.fact("Renew / convert / release options are proposals for management decision, not conclusions "
                      "from the data.", "contracts_ending", None, fmt_date(asof), fl, kind="hypothesis")]
        return P.slide("contract_cliff", "A big decision is due",
                       "Contract end dates for everyone on contract today", "chart+cards", [ch, P.cards(cards_)], com,
                       self._src(ctx) + " Zero-allocation check uses the monthly allocation columns.")

    def _s_profession_mix(self, ctx: Context) -> dict[str, Any]:
        fl = ctx.filters_label(FILTER_DIMS)
        people = self._people(ctx)
        prof = people["profession"].value_counts()
        ch = P.chart("doughnut", "Headcount by profession (unique people)", list(prof.index),
                     [{"name": "People", "values": prof.tolist()}], number_format="0")
        roles = people["profession_role"].value_counts()
        big = roles[roles >= 5]
        rows = [[k, int(v), P.fmt_pct(v / len(people), 1)] for k, v in big.items()]
        other = int(roles[roles < 5].sum())
        if other:
            rows.append(["Other roles (<5 each)", other, P.fmt_pct(other / len(people), 1)])
        tbl = P.table("Profession role", ["Role", "Headcount", "Share"], rows)
        com = [P.fact(f"{prof.index[0]} is {P.fmt_pct(prof.iloc[0] / len(people), 0)} of the {len(people)} people "
                      f"in scope.", "people", int(prof.iloc[0]), ctx.period, fl)]
        return P.slide("profession_mix", "Headcount by profession and role", "Full roster in scope, each person once",
                       "chart+table", [ch, tbl], com, self._src(ctx))

    def _s_data_quality(self, ctx: Context) -> dict[str, Any]:
        rep = ctx.dataset.report
        rows = [[c.name, c.level.upper(), c.count, c.detail] for c in rep.checks]
        tbl = P.table("Validation checks on the source roster", ["Check", "Result", "Count", "Action / note"], rows,
                      col_widths=[3.2, 1.1, 0.8, 4.1])
        passed = sum(1 for c in rep.checks if c.level == "ok")
        com = [P.fact(f"{passed} of {len(rep.checks)} checks passed; {rep.quarantined} rows quarantined; "
                      f"{len(rep.warnings)} warnings disclosed.", "data_quality", passed, ctx.dataset.version,
                      "whole dataset")]
        return P.slide("data_quality", "Data quality checks on the roster",
                       "Every figure in this deck is computed after these checks", "table+comm", [tbl], com,
                       f"Dataset {ctx.dataset.version}, SHA-256 {ctx.dataset.sha256[:12]}…")

    def _s_next_steps(self, ctx: Context) -> dict[str, Any]:
        h = self._headline(ctx)
        cliff = date.fromisoformat(h["cliff_date"]) if h["cliff_date"] else self.asof(ctx) + timedelta(days=90)
        zero = int(h["onc"]["zero_allocation"].sum())
        dups = next((c.count for c in ctx.dataset.report.checks if c.name.startswith("Duplicate rows")), 0)
        d = lambda days: fmt_date(cliff - timedelta(days=days))  # noqa: E731
        top = ", ".join(h["vend"].index[:2]) or "key vendors"
        steps = [
            {"head": "Clean the data", "body": f"Confirm the {zero} zero-allocation contracts and fix the "
             f"{dups // 2 if dups else 0} duplicate record(s), so we decide on facts.", "when": f"By {d(46)}"},
            {"head": f"Decide on the {h['cliff_n']}", "body": f"Sort every {fmt_date(cliff)} contract into renew, "
             "convert or release with the line managers.", "when": f"By {d(31)}"},
            {"head": "Renegotiate with vendors", "body": f"Use the renewals to agree better rates with {top} and "
             "test one new supplier.", "when": f"By {d(16)}"},
            {"head": "Protect critical capability", "body": "Agree the minimum team each location needs before "
             "any further reduction.",
             "when": f"Q{(cliff.month % 12) // 3 + 1} {cliff.year + (cliff.month == 12)} plan"},
        ]
        com = [P.fact("Next steps are proposals for management decision; dates are counted back from the contract "
                      f"end date {fmt_date(cliff)}.", "contracts_ending", h["cliff_n"], fmt_date(self.asof(ctx)),
                      ctx.filters_label(FILTER_DIMS), kind="hypothesis")]
        return P.slide("next_steps", "What I'd suggest we do next", "Four steps, in order", "steps",
                       [P.cards(steps, numbered=True)], com, self._src(ctx))

    def _s_commentary(self, ctx: Context) -> dict[str, Any]:
        h = self._headline(ctx)
        fl = ctx.filters_label(FILTER_DIMS)
        com = list(self._s_short_version(ctx)["commentary"])
        material = [d_ for d_ in ctx.disclosures if "extra spaces" not in d_ and "E-mail" not in d_]
        for d_ in material[:3]:
            com.append(P.fact(d_, "data_quality", None, ctx.dataset.version, fl, kind="caveat"))
        return P.slide("commentary", "Management commentary", f"{ctx.period} · {fl} · as of {fmt_date(h['asof'])}",
                       "commentary", [], com, self._src(ctx) + " Draft commentary generated from computed metrics; "
                       "requires human review before distribution.")

    def _s_appendix(self, ctx: Context) -> dict[str, Any]:
        ds = ctx.dataset
        cards_ = [
            {"head": "The data", "body": f"{ds.source} ({ds.version}); {len(self._people(ctx))} unique people in "
             "scope; "
             f"duplicates counted once; {ds.report.quarantined} rows quarantined. 'On contract' = start date on or "
             f"before {fmt_date(self.asof(ctx))} and end date on or after it."},
            {"head": "Cost and FTE", "body": f"Monthly columns are FTE allocations, so part months are pro-rated. "
             f"Cost = FTE x {WORKING_DAYS} working days x daily rate in EUR (rate card: Band_11 €200, Band_9 €100 "
             "where no rate is given). No FX, tax or vendor mark-up."},
            {"head": "Provenance", "body": f"Filters: {ctx.filters_label(FILTER_DIMS)}. Period: {ctx.period}. "
             f"Queries: {', '.join(q.fingerprint for q in ctx.queries[:4])}. Commentary is generated from computed "
             "numbers and is labelled draft until a reviewer approves it."},
        ]
        return P.slide("appendix", "Appendix: how the numbers were worked out",
                       "Assumptions, definitions and data lineage", "cards", [P.cards(cards_)], [],
                       self._src(ctx))


# ---------------------------------------------------------------------- synthetic roster
FIRST = ["Aarav", "Mei", "Thomas", "Priya", "Ananya", "Vikram", "Daniel", "Wei", "Hannah", "Sofia", "Lukas", "Neha",
         "Rahul", "Karan", "Siti", "Arjun", "Li", "Kavya", "Rohan", "Isha", "Jun", "Meera", "Farhan", "Chloe", "Dev",
         "Nadia", "Omar", "Rina", "Sanjay", "Tara", "Uma", "Vivek", "Yusuf", "Zara", "Aditi", "Bryan", "Celine",
         "Dinesh", "Elena", "Gautam"]
LAST = ["Mehta", "Tan", "Becker", "Nair", "Rao", "Joshi", "Koh", "Lim", "Ong", "Martins", "Weber", "Kulkarni",
        "Deshpande", "Shah", "Rahman", "Iyer", "Chen", "Pillai", "Sharma", "Goh", "Lee", "Menon", "Ali", "Ng", "Patel",
        "Bose", "Khan", "Wong", "Reddy", "Seah"]
VENDORS = [("Wyvern Consulting", 0.30), ("Shellcrest Partners", 0.28), ("Northwind Staffing", 0.16),
           ("Starling IT", 0.08), ("Dunmore Tech", 0.08), ("Mackinley Infotech", 0.04), ("Morrow Systems", 0.03),
           ("Crownfield", 0.02), ("Cedar Consulting", 0.01)]
PROJECTS = ["Internet Security", "Risk, Business Continuity and Disaster Recovery", "Mobile Device Management",
            "JIRA Upgrade", "BIM Strategic Review", "Staff card printing facility", "Payments Platform Refresh",
            "Data Centre Exit"]
PROFESSIONS = [("Engineering", ["Engineer"] * 10 + ["Lead Engineer", "Associate Engineer", "Senior Engineer",
                                                    "Technology Lead"], 0.72),
               ("Business Functional Analysis", ["Business Analyst", "Business Functional Analyst"], 0.12),
               ("QA & Testing", ["QA & Testing Engineer", "QA & Testing Analyst"], 0.08),
               ("Service Operations(Technology)", ["Service Design Specialist", "Technology Manager"], 0.05),
               ("Programme & Project Management", ["PMO Specialist", "Project Manager"], 0.03)]


def synthetic_roster(seed: int = 7, year: int = 2018, n: int = 220) -> pd.DataFrame:
    """A fictional roster with the same columns and a similar shape to a monthly resource tracker.

    Seeded features (for tests and demos): two roll-off waves (end of March and end of
    September), India-heavy reductions, 20 zero-allocation contracts, one duplicate
    record, e-mails with spaces, untrimmed text and non-standard profession categories.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    yy = str(year)[2:]
    months = [f"{calendar.month_abbr[i]}-{yy}" for i in range(1, 13)]
    rows = []
    loc_plan = [("Pune", "IN", 0.50), ("Singapore", "SG", 0.42), ("Bangalore", "IN", 0.08)]
    for i in range(n):
        loc, ctry, _ = loc_plan[rng.choice(3, p=[p for *_, p in loc_plan])]
        cat = rng.choice(CATEGORIES, p=[0.37, 0.51, 0.12])
        start = date(year, 1, 1) if rng.random() < 0.45 else (
            date(year, 1, 2) if rng.random() < 0.4 else
            date(year - 1, int(rng.integers(9, 13)), int(rng.integers(1, 28))))
        india = ctry == "IN"
        r = rng.random()
        if india and loc == "Bangalore":
            end = date(year, 3, 31) if r < 0.6 else date(year, 6, 30)
        elif india:
            end = date(year, 3, 31) if r < 0.38 + (0.22 if cat == "T&M" else 0) else (
                date(year, 9, 30) if r < 0.85 else date(year, 12, 31))
        else:
            end = date(year, 12, 31) if r < 0.82 else (date(year, 3, 31) if r < 0.9 else date(year, 9, 30))
        alloc = []
        for mi in range(1, 13):
            ms, me = date(year, mi, 1), month_end(f"{year}-{mi:02d}")
            if end < ms or start > me:
                alloc.append(0.0)
            else:
                s, e = max(start, ms), min(end, me)
                alloc.append(round(((e - s).days + 1) / ((me - ms).days + 1), 6))
        prof, roles, _ = PROFESSIONS[rng.choice(len(PROFESSIONS), p=[p for *_, p in PROFESSIONS])]
        band = "Band_9" if (india and rng.random() < 0.6) else "Band_11"
        vendor = VENDORS[rng.choice(len(VENDORS), p=[p for _, p in VENDORS])][0]
        fn, ln = FIRST[rng.integers(len(FIRST))], LAST[rng.integers(len(LAST))]
        rows.append({
            "Sr No": i + 1, "Last Updated By": "Roster Admin",
            "Team": rng.choice(["GT", "VC", "GW"], p=[.48, .44, .08]),
            "Project": (" " + PROJECTS[rng.integers(len(PROJECTS))] + "  ") if rng.random() < 0.9 else
            PROJECTS[rng.integers(len(PROJECTS))],
            "Category": cat, "Position #": str(1_000_000_000 + int(rng.integers(0, 8_999_999_999))),
            "Name": f"{fn} {ln}", "Gender": "F" if rng.random() < 0.36 else "M", "Country": ctry,
            "City": "SG" if loc == "Singapore" else loc, "Email-ID": f"{fn} {ln}@ledgerline.example",
            "Start Date": pd.Timestamp(start), "End Date": pd.Timestamp(end),
            "Functional Manager": rng.choice(["Manager A ", "Manager B ", "Manager C "]), "Vendor": vendor,
            "PO": f"PO9{int(rng.integers(1_000_000, 9_999_999))}" + (" " if rng.random() < 0.3 else ""),
            "Profession Category": "Service Operations &Technology" if rng.random() < 0.97 else
            "Service Operations and Technology Professions",
            "Profession": prof, "Profession Role": roles[rng.integers(len(roles))], "Cost Center": 123456789,
            **dict(zip(months, alloc, strict=True)), "Clarity Band": band,
        })
    df = pd.DataFrame(rows)
    # 20 zero-allocation contracts running to year end (tracker not updated)
    idx = rng.choice(df.index[df["End Date"] == pd.Timestamp(date(year, 12, 31))], size=20, replace=False)
    df.loc[idx, months] = 0.0
    df.loc[idx, "Vendor"] = "Dunmore Tech"
    # one duplicate record (same Position # + Name)
    dup = df.iloc[[30]].copy()
    df = pd.concat([df, dup], ignore_index=True)
    return df


def load_original_roster(path: str) -> pd.DataFrame:
    """Read a roster workbook's 'Data' sheet (or the first sheet) for an in-session upload."""
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "Data" if "Data" in xl.sheet_names else xl.sheet_names[0]
    return xl.parse(sheet, dtype=object)
