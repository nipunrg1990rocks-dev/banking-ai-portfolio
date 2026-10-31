"""Natural language -> validated DeckSpec.

Two interchangeable interpreters produce the same structured output:

* ``RulesParser`` (default, free, deterministic): regex + vocabulary matching against the
  domain's allowlists. It can only ever emit values it already knows.
* ``LLMParser`` (optional): asks Claude for JSON matching the DeckSpec schema, then the
  result goes through exactly the same Pydantic + allowlist validation. On any failure it
  falls back to the rules parser and says so.

Neither interpreter executes anything from the prompt. ``guard`` rejects prompts that
contain code, SQL or prompt-injection patterns before interpretation.
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from typing import Any

from portfolio_core.guardrails import check_input

from .domains.base import Domain
from .spec import MAX_SLIDES, MIN_SLIDES, Comparison, DeckSpec, SlideRequest, period_label
from .store import Dataset

MAX_PROMPT_CHARS = 600
WORDS = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {
    m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTH_RX = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|" \
           r"jun|jul|aug|sep|sept|oct|nov|dec)"
CODE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bselect\b.+\bfrom\b", r"\bdrop\s+table\b", r"\binsert\s+into\b", r"\bdelete\s+from\b", r"\bupdate\s+\w+\s+set\b",
    r"\bunion\s+select\b", r";\s*--", r"__import__", r"\bexec\s*\(", r"\beval\s*\(", r"os\.system", r"subprocess",
    r"<\s*script", r"^\s*import\s+\w+", r"\bpd\.read_", r"\bopen\s*\(", r"\brm\s+-rf\b",
]]


class PromptRejected(ValueError):
    pass


@dataclass
class ParseResult:
    spec: DeckSpec
    mode: str  # rules | llm | llm->rules
    notes: list[str] = field(default_factory=list)
    sanitized_prompt: str = ""
    llm_raw: str | None = None


@dataclass
class FollowUp:
    spec: DeckSpec
    action: str  # modify | fresh_copy | refresh | replace_slide | regenerate_slide
    slides_to_regenerate: list[int] | None = None  # 1-based; None = all
    use_latest_data: bool = False
    notes: list[str] = field(default_factory=list)
    changed: bool = True
    mode: str = "rules"


def guard(text: str) -> str:
    """Return the sanitised prompt or raise PromptRejected."""
    res = check_input(text, max_chars=MAX_PROMPT_CHARS)
    if res.errors:
        raise PromptRejected(res.errors[0].replace("Question", "Prompt").replace("question", "prompt"))
    if res.injection_detected:
        raise PromptRejected("This looks like an attempt to override the assistant's instructions, so it was not "
                             "processed. Describe the deck you want in plain language.")
    if any(p.search(text) for p in CODE_PATTERNS):
        raise PromptRejected("Prompts describe a deck in plain language. Code and SQL are never executed here, so "
                             "this prompt was rejected.")
    return res.sanitized


# ---------------------------------------------------------------------- helpers
def _year_for(month: int, ds: Dataset, explicit: int | None, default_year: int | None = None) -> int | None:
    if explicit:
        return explicit
    if default_year:
        return default_year
    years = sorted({int(m[:4]) for m in ds.months if int(m[5:]) == month}, reverse=True)
    return years[0] if years else None


def _norm_year(y: str | None) -> int | None:
    if not y:
        return None
    y = y.strip("'’ ").lower().replace("fy", "")
    if len(y) == 2:
        return 2000 + int(y)
    return int(y) if y.isdigit() else None


def parse_period(text: str, ds: Dataset, domain: Domain,
                 default_year: int | None = None) -> tuple[list[str] | None, list[str]]:
    """Returns (months or None if the prompt names no period, notes).

    ``default_year`` (used for follow-ups) anchors a bare month or quarter to the deck's year,
    so "add October" on a 2026 deck means Oct 2026 even if only Oct 2025 exists.
    """
    t = text.lower()
    notes: list[str] = []
    yr = r"(?:\s*(?:fy\s?)?('?\d{2,4}))?"
    m = re.search(r"\b(?:last|latest|previous|most recent) (\d{1,2}) months\b", t)
    if m:
        n = int(m.group(1))
        return ds.months[-n:], notes
    m = re.search(r"\bq([1-4])" + yr, t)
    if m:
        q, y = int(m.group(1)), _norm_year(m.group(2))
        y = y or _year_for(q * 3, ds, None, default_year) or _year_for(q * 3 - 2, ds, None)
        if not y:
            return [], [f"Q{q} is not in the dataset"]
        months = [f"{y}-{mm:02d}" for mm in range(q * 3 - 2, q * 3 + 1)]
        return months, notes
    m = re.search(r"\bh([12])" + yr, t)
    if m:
        h, y = int(m.group(1)), _norm_year(m.group(2))
        y = y or _year_for(6 * h, ds, None, default_year)
        return [f"{y}-{mm:02d}" for mm in range(6 * h - 5, 6 * h + 1)], notes
    m = re.search(r"\b(?:as of|as at|until|to end of|through) (?:end of )?" + MONTH_RX + r"(?:\s+(\d{4}))?", t)
    if m:
        mo, y = MONTHS[m.group(1)[:3] if m.group(1) != "sept" else "sep"], _norm_year(m.group(2))
        y = y or _year_for(mo, ds, None, default_year)
        return [f"{y}-{mm:02d}" for mm in range(1, mo + 1)], notes
    m = re.search(r"\b(?:fy|full year|calendar year|year)\s*('?\d{2,4})\b|\b(20\d\d)\b(?! ?-)", t)
    month_hits = re.findall(r"\b" + MONTH_RX + r"(?:\s+(\d{4}))?\b", t)
    if month_hits:
        out = []
        for name, y in month_hits:
            mo = MONTHS[name[:3] if name != "sept" else "sep"]
            yy = _norm_year(y) or (_norm_year(m.group(1) or m.group(2)) if m else None)
            yy = yy or _year_for(mo, ds, None, default_year)
            if yy:
                out.append(f"{yy}-{mo:02d}")
        if out:
            return sorted(set(out)), notes
    if re.search(r"\bytd\b|year to date", t):
        y = ds.months[-1][:4]
        return [x for x in ds.months if x.startswith(y)], notes
    if m:
        y = _norm_year(m.group(1) or m.group(2))
        ms = [x for x in ds.months if x.startswith(str(y))]
        if ms:
            return ms, notes
        notes.append(f"{y} is not in the dataset; using the default period")
    if re.search(r"\b(last|latest|this|current|most recent) quarter\b", t):
        return domain.default_months(ds), notes
    return None, notes


def split_comparison(text: str) -> tuple[str, Comparison | None, str | None]:
    """Strip the comparison clause from the text. Returns (rest, comparison, raw clause period text)."""
    t = text
    yoy = r"year[- ]over[- ]year|\byoy\b|same period last year|prior year|vs last year|versus last year"
    if re.search(yoy, t, re.I):
        t2 = re.sub(r"(compare[ds]?\s*)?(it\s*)?(year[- ]over[- ]year|\byoy\b|same period last year|prior year|vs last "
                    r"year|versus last year)", " ", t, flags=re.I)
        return t2, Comparison(mode="yoy"), None
    if re.search(r"no comparison|without comparison", t, re.I):
        return re.sub(r"(no|without) comparison", " ", t, flags=re.I), Comparison(mode="none"), None
    if re.search(r"prior period|previous period|previous quarter|prior quarter|quarter[- ]on[- ]quarter|\bqoq\b", t,
                 re.I):
        return t, Comparison(mode="prior_period"), None
    m = re.search(r"(?:compar(?:e|ed|ing)(?: it| this| that)?(?: (?:with|to|against|vs\.?|versus))?|\bvs\.?|"
                  r"\bversus|against) (q[1-4](?:\s*'?\d{2,4})?|h[12](?:\s*\d{4})?|" + MONTH_RX + r"(?:\s+\d{4})?)",
                  t, re.I)
    if m:
        return t[:m.start()] + " " + t[m.end():], Comparison(mode="custom"), m.group(1)
    return t, None, None


def slide_count(text: str) -> int | None:
    m = re.search(r"\b(\d{1,2}|" + "|".join(WORDS) + r")[- ]?(?:slides?|slide|page|pager)\b", text, re.I)
    if not m:
        return None
    v = m.group(1).lower()
    return int(v) if v.isdigit() else WORDS[v]


def match_slides(text: str, domain: Domain) -> list[str]:
    found = []
    for key, st in domain.slide_catalog.items():
        if key == "cover":
            continue
        if any(re.search(k, text, re.I) for k in st.keywords):
            found.append(key)
    return found


def choose_slides(domain: Domain, template: str, n: int | None, must: list[str], notes: list[str]) -> list[str]:
    order = list(domain.slide_catalog)
    tpl = domain.templates[template].slides
    default_n = 6 if template not in ("workforce_review",) else len(tpl)
    n = n or default_n
    if n < MIN_SLIDES or n > MAX_SLIDES:
        notes.append(f"{n} slides requested; decks are {MIN_SLIDES}-{MAX_SLIDES} slides, so using "
                     f"{min(max(n, MIN_SLIDES), MAX_SLIDES)}.")
        n = min(max(n, MIN_SLIDES), MAX_SLIDES)
    chosen = ["cover"] + [m for m in must if m != "cover"]
    if len(chosen) > n:
        notes.append(f"{len(chosen)} slide types were requested, more than {n}; keeping all requested slides.")
        n = min(len(chosen), MAX_SLIDES)
        chosen = chosen[:n]
    for s in tpl:
        if len(chosen) >= n:
            break
        if s not in chosen:
            chosen.append(s)
    for s in order:
        if len(chosen) >= n:
            break
        if s not in chosen:
            chosen.append(s)
    return sorted(chosen, key=order.index)


def make_title(domain: Domain, template: str, filters: dict[str, list[str]], months: list[str]) -> str:
    scope = " ".join(domain.display(v) for vals in filters.values() for v in vals[:2]) if filters else ""
    if len(scope) > 40:
        scope = "Filtered"
    base = domain.templates[template].label
    return f"{(scope + ' ') if scope else ''}{base} — {period_label(months)}".strip()[:120]


def detect_domain(text: str, domains: dict[str, Domain], default: str) -> str:
    scores = {k: sum(1 for kw in d.detect_keywords if re.search(kw, text, re.I)) for k, d in domains.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > scores.get(default, 0) else default


# ---------------------------------------------------------------------- rules parser
class RulesParser:
    mode = "rules"

    def parse(self, text: str, domain: Domain, ds: Dataset) -> ParseResult:
        clean = guard(text)
        notes: list[str] = []
        rest, comparison, cmp_text = split_comparison(clean)
        months, pn = parse_period(rest, ds, domain)
        notes += pn
        if months is None:
            months = domain.default_months(ds)
            notes.append(f"No period named; using {period_label(months)}.")
        missing = [m for m in months if m not in ds.months]
        if missing:
            notes.append(f"{period_label(missing)} is not in dataset {ds.version} (data to "
                         f"{period_label([ds.months[-1]])}); those months were dropped.")
            months = [m for m in months if m in ds.months] or domain.default_months(ds)
        if comparison is None:
            comparison = Comparison(mode="prior_period" if domain.key == "ops_risk" else "none")
        if comparison.mode == "custom":
            cmp_months, _ = parse_period(cmp_text or "", ds, domain)
            comparison = Comparison(mode="custom", months=cmp_months or [])
            if not cmp_months:
                comparison = Comparison(mode="prior_period")
        template = self._template(rest, domain)
        must = match_slides(rest, domain)
        filters: dict[str, list[str]] = {}
        slide_hits = set(must)
        for dim, value in domain.filter_value_for(rest):
            if dim == "severity" and "high_severity" in slide_hits and not re.search(r"\bonly\b", rest, re.I):
                continue  # "add high-severity exceptions" asks for a slide, not a filter
            for v in domain.expand(dim, value):
                filters.setdefault(dim, [])
                if v not in filters[dim]:
                    filters[dim].append(v)
            if dim == "severity" and value == "High":
                notes.append("'High severity' is read as High + Critical.")
        if "commentary" not in must and re.search(r"commentary|narrative|conclusion", rest, re.I):
            must.append("commentary")
        n = slide_count(rest)
        slides = choose_slides(domain, template, n, must, notes)
        top = re.search(r"\btop (\d{1,2})\b", rest, re.I)
        top_n = min(max(int(top.group(1)), 3), 10) if top else 5
        spec = DeckSpec(domain=domain.key, template=template, title=make_title(domain, template, filters, months),
                        filters=filters, months=months, comparison=comparison,
                        slides=[SlideRequest(slide_type=s) for s in slides], top_n=top_n,
                        speaker_notes=not re.search(r"no (speaker )?notes", rest, re.I))
        return ParseResult(spec, self.mode, notes, clean)

    @staticmethod
    def _template(text: str, domain: Domain) -> str:
        for key, t in domain.templates.items():
            if any(re.search(k, text, re.I) for k in t.keywords):
                return key
        return next(iter(domain.templates)) if domain.key == "workforce" else "executive_summary"


def _months_from_clause(clause: str, ds: Dataset, domain: Domain, year: int | None = None) -> list[str] | None:
    if not re.search(r"\b(q[1-4]|h[12]|ytd|" + MONTH_RX + r")\b", clause, re.I):
        return None
    months, _ = parse_period(clause, ds, domain, default_year=year)
    return months


def apply_followup(text: str, spec: DeckSpec, domain: Domain, ds: Dataset) -> FollowUp:
    """Interpret a follow-up instruction against the current spec. Unknown clauses change nothing."""
    clean = guard(text)
    notes: list[str] = []
    s = spec.model_copy(deep=True)
    slides = [x.slide_type for x in s.slides]
    action, regen, latest = "modify", None, False
    clauses = [c.strip(" .") for c in re.split(r"\s*(?:,|;|\band then\b|\bthen\b|\band\b|\balso\b)\s*", clean,
                                               flags=re.I) if c.strip(" .")]
    understood = 0
    year = int(spec.months[-1][:4]) if spec.months else None
    for c in clauses:
        low = c.lower()
        if re.search(r"\b(fresh|new|clean|another) (copy|version)\b|\bduplicate\b|\bsave (a )?copy\b", low):
            action = "fresh_copy" if action == "modify" else action
            notes.append("Fresh copy: the same deck saved as a new version; the earlier file is kept.")
            understood += 1
            continue
        if re.search(r"\brefresh\b|latest data|newest data|new data|reload", low) and not re.search(r"slide \d", low):
            latest, understood = True, understood + 1
            notes.append("Refresh: every slide is recomputed on the latest validated dataset.")
            action = "refresh" if action in ("modify", "fresh_copy") else action
            continue
        m = re.search(r"\b(?:replace|swap|change|turn) slide (\d{1,2})(?: (?:with|to|for|into) (.+))?", low)
        if m:
            idx = int(m.group(1))
            if not 1 <= idx <= len(slides):
                notes.append(f"There is no slide {idx} (deck has {len(slides)} slides).")
                continue
            target = match_slides(m.group(2) or "", domain) if m.group(2) else []
            if target:
                slides[idx - 1] = target[0]
                notes.append(f"Slide {idx} replaced with '{domain.slide_catalog[target[0]].label}'.")
            else:
                notes.append(f"Slide {idx} regenerated with fresh numbers and commentary.")
            regen = sorted(set((regen or []) + [idx]))
            action = "replace_slide"
            understood += 1
            continue
        m = re.search(r"\b(?:regenerate|redo|rebuild|update) slide (\d{1,2})", low)
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(slides):
                regen = sorted(set((regen or []) + [idx]))
                action = "replace_slide"
                notes.append(f"Slide {idx} regenerated.")
                understood += 1
            continue
        m = re.search(r"\b(?:remove|delete|drop) slide (\d{1,2})", low)
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(slides) and len(slides) > MIN_SLIDES:
                notes.append(f"Removed slide {idx} ({domain.slide_catalog[slides[idx - 1]].label}).")
                slides.pop(idx - 1)
                understood += 1
            continue
        if re.search(r"compar|\bvs\b|versus|year[- ]over[- ]year|\byoy\b|no comparison", low):
            _, cmp_, cmp_text = split_comparison(low)
            if cmp_ is None and re.search(r"compar", low):
                cmp_ = Comparison(mode="prior_period")
            if cmp_ is not None:
                if cmp_.mode == "custom":
                    months, _ = parse_period(cmp_text or "", ds, domain, default_year=year)
                    cmp_ = Comparison(mode="custom", months=[m for m in (months or []) if m in ds.months])
                    if not cmp_.months:
                        notes.append(f"Comparison period {cmp_text} is not in the dataset; comparison unchanged.")
                        continue
                s.comparison = cmp_
                notes.append({"yoy": "Comparison set to year over year.", "none": "Comparison removed.",
                              "prior_period": "Comparison set to the prior period.",
                              "custom": f"Comparison set to {period_label(cmp_.months)}."}[cmp_.mode])
                understood += 1
                continue
        m = re.search(r"\btop (\d{1,2})\b", low)
        if m:
            s.top_n = min(max(int(m.group(1)), 3), 10)
            notes.append(f"Top-N set to {s.top_n}.")
            understood += 1
            continue
        n = slide_count(low)
        if n and re.search(r"\b(make|use|set|change|only|just|cut|trim|expand)\b", low):
            n = min(max(n, MIN_SLIDES), MAX_SLIDES)
            if n < len(slides):
                keep = ["cover"] + [x for x in slides if x != "cover"]
                order = domain.templates[s.template].slides
                drop = sorted(keep[1:], key=lambda x: order.index(x) if x in order else 99, reverse=True)
                for d in drop:
                    if len(slides) <= n:
                        break
                    slides.remove(d)
            else:
                slides = choose_slides(domain, s.template, n, slides, notes)
            notes.append(f"Deck resized to {len(slides)} slides.")
            understood += 1
            continue
        tpl = next((k for k, t in domain.templates.items() if any(re.search(p, low) for p in t.keywords)), None)
        if tpl and re.search(r"\b(make it|turn (it )?into|switch to|use the)\b", low):
            s.template = tpl
            slides = choose_slides(domain, tpl, len(slides), [], notes)
            notes.append(f"Template switched to {domain.templates[tpl].label}.")
            understood += 1
            continue
        verb = re.match(r"^(change|switch|move|set|use|swap|show only|only|just|filter to|focus on|add|include|"
                        r"remove|exclude|drop|without)\b\s*(.*)$", low)
        if not verb:
            # A bare scope or period ("EMEA", "Q2 2026") is treated as "change to".
            verb = re.match(r"^()(.*)$", low)
        v, rest = verb.group(1), verb.group(2)
        if v in ("add", "include"):
            targets = match_slides(rest, domain)
            if targets and not re.search(r"\bonly\b", rest):
                order = list(domain.slide_catalog)
                for t in targets:
                    if t not in slides:
                        slides.append(t)
                        notes.append(f"Added slide '{domain.slide_catalog[t].label}'.")
                slides = sorted(slides, key=order.index)
                understood += 1
                continue
            months = _months_from_clause(rest, ds, domain, year)
            if months:
                new = [m for m in months if m not in s.months]
                bad = [m for m in new if m not in ds.months]
                if bad:
                    notes.append(f"{period_label(bad)} is not in dataset {ds.version} (data to "
                                 f"{period_label([ds.months[-1]])}); not added.")
                new = [m for m in new if m in ds.months]
                if new:
                    s.months = sorted(set(s.months) | set(new))
                    notes.append(f"Added {period_label(new)}; period is now {period_label(s.months)}.")
                elif not bad:
                    notes.append(f"{period_label(months)} is already in the period ({period_label(s.months)}).")
                understood += 1
                continue
            hits = domain.filter_value_for(rest)
            if hits:
                for dim, val in hits:
                    for x in domain.expand(dim, val):
                        s.filters.setdefault(dim, [])
                        if x not in s.filters[dim]:
                            s.filters[dim].append(x)
                notes.append("Scope widened: " + ", ".join(val for _, val in hits) + ".")
                understood += 1
                continue
        if v in ("remove", "exclude", "drop", "without"):
            hits = domain.filter_value_for(rest)
            targets = match_slides(rest, domain)
            if hits:
                for dim, val in hits:
                    vals = domain.expand(dim, val)
                    if dim in s.filters:
                        s.filters[dim] = [x for x in s.filters[dim] if x not in vals]
                        if not s.filters[dim]:
                            del s.filters[dim]
                    else:
                        allv = ds.dimension_values(dim)
                        s.filters[dim] = [x for x in allv if x not in vals]
                notes.append("Excluded: " + ", ".join(val for _, val in hits) + ".")
                understood += 1
                continue
            if targets:
                for t in targets:
                    if t in slides and len(slides) > MIN_SLIDES:
                        slides.remove(t)
                        notes.append(f"Removed slide '{domain.slide_catalog[t].label}'.")
                understood += 1
                continue
        # change / only / bare: scope or period replacement
        m = re.search(r"(?:from )?(.+?) to (.+)$", rest)
        src, dst = (m.group(1), m.group(2)) if (m and v in ("change", "switch", "move", "swap", "set")) else ("", rest)
        months = _months_from_clause(dst, ds, domain, year)
        hits = domain.filter_value_for(dst)
        if hits:
            dims: dict[str, list[str]] = {}
            for dim, val in hits:
                for x in domain.expand(dim, val):
                    dims.setdefault(dim, [])
                    if x not in dims[dim]:
                        dims[dim].append(x)
            src_hits = {d for d, _ in domain.filter_value_for(src)} if src else set()
            for dim, vals in dims.items():
                if src and dim in src_hits and dim in s.filters:
                    removed = {x for d, val in domain.filter_value_for(src) if d == dim for x in domain.expand(d, val)}
                    s.filters[dim] = sorted((set(s.filters[dim]) - removed) | set(vals))
                else:
                    s.filters[dim] = vals
            # A location and its country are alternatives in the workforce domain.
            if "location" in dims and "country" in s.filters and "country" not in dims:
                del s.filters["country"]
            if "country" in dims and "location" in s.filters and "location" not in dims:
                del s.filters["location"]
            if any(d == "severity" for d, _ in hits) and any(val == "High" for _, val in hits):
                notes.append("'High severity' is read as High + Critical.")
            notes.append("Scope set to " + "; ".join(f"{d}: {', '.join(x)}" for d, x in dims.items()) + ".")
            understood += 1
            continue
        if months:
            ok = [m for m in months if m in ds.months]
            if ok:
                s.months = ok
                notes.append(f"Period set to {period_label(ok)}.")
            else:
                notes.append(f"{period_label(months)} is not in dataset {ds.version}; period unchanged.")
            understood += 1
            continue
        if re.search(r"\b(all|every|whole|entire)\b.*\b(regions?|locations?|data|scope)\b|\bclear filters?\b", rest):
            s.filters = {}
            notes.append("All filters cleared.")
            understood += 1
            continue
        notes.append(f"Could not interpret '{c}'; nothing changed for that part.")
    s.slides = [SlideRequest(slide_type=x) for x in slides[:MAX_SLIDES]]
    if action in ("modify", "refresh") and understood and s != spec:
        s.title = make_title(domain, s.template, s.filters, s.months)
    changed = understood > 0
    if action == "replace_slide" and len(s.slides) != len(spec.slides):
        action, regen = "modify", None
    if action == "replace_slide" and s.model_dump(exclude={"slides"}) != spec.model_dump(exclude={"slides"}):
        action, regen = "modify", None  # scope changed too, so every slide must be recomputed
    return FollowUp(s, action, regen, latest, notes, changed)


# ---------------------------------------------------------------------- optional LLM parser
LLM_SYSTEM = """You convert a reporting request into JSON for a slide-deck generator.
Return ONLY one JSON object with keys: template, filters, months, comparison, slides, top_n, speaker_notes.
- template: one of {templates}
- filters: object mapping a dimension to a list of values. Allowed dimensions and values: {dims}
- months: list of "YYYY-MM" from this list only: {months}
- comparison: {{"mode": "none"|"prior_period"|"yoy"|"custom", "months": [...]}}
- slides: ordered list of slide types (3-12), always starting with "cover". Allowed: {slides}
- top_n: integer 3-10
Never add keys, SQL, code, or values outside these lists. If the request asks for anything else, ignore that part."""


class LLMParser:
    """Uses Claude when an authorised key exists; every output is re-validated by the same rules."""

    mode = "llm"

    def __init__(self, client) -> None:
        self.client = client
        self.fallback = RulesParser()

    def parse(self, text: str, domain: Domain, ds: Dataset) -> ParseResult:
        clean = guard(text)
        dims = {d: ds.dimension_values(d)[:40] for d in domain.filter_dimensions}
        system = LLM_SYSTEM.format(templates=list(domain.templates), dims=json.dumps(dims), months=ds.months,
                                   slides=list(domain.slide_catalog))
        raw = None
        try:
            resp = self.client.complete(system, clean, max_tokens=800)
            raw = resp.text
            data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            data = {k: v for k, v in data.items() if k in {"template", "filters", "months", "comparison", "slides",
                                                           "top_n", "speaker_notes"}}
            data["slides"] = [{"slide_type": x} if isinstance(x, str) else {"slide_type": x.get("slide_type")}
                              for x in data.get("slides", [])]
            spec = DeckSpec(domain=domain.key, title="pending", **data)
            problems = spec.validate_against(domain, ds)
            if problems:
                raise ValueError("; ".join(problems))
            spec.title = make_title(domain, spec.template, spec.filters, spec.months)
            return ParseResult(spec, "llm", ["Interpreted by Claude; validated against the allowlists."], clean, raw)
        except Exception as exc:  # noqa: BLE001 - any LLM failure falls back to rules
            res = self.fallback.parse(text, domain, ds)
            res.mode = "llm->rules"
            res.notes.insert(0, f"Claude output was not usable ({type(exc).__name__}: {str(exc)[:120]}); used the "
                                "rules parser instead.")
            res.llm_raw = raw
            return res


def spec_summary(spec: DeckSpec, domain: Domain) -> dict[str, Any]:
    return {"template": domain.templates[spec.template].label, "period": period_label(spec.months),
            "comparison": spec.comparison.mode if spec.comparison.mode != "custom" else period_label(
                spec.comparison.months), "filters": spec.filters or "All data",
            "slides": [domain.slide_catalog[s.slide_type].label for s in spec.slides]}
