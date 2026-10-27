"""HTML, CSS and chart builders for the RegRadar app (kept out of app.py so the page logic stays readable)."""

from __future__ import annotations

import html
from datetime import date

import altair as alt
import pandas as pd

from .agents import CREW, Assessment
from .diff import AMENDED, NEW, REMOVED, UNCHANGED, Change, redline_html
from .gaps import CONFLICT, COVERED, MISSING, OPPORTUNITY, PARTIAL, STATUS_LABEL

STATUS_COLOR = {CONFLICT: "#d64545", MISSING: "#b02a6b", PARTIAL: "#e09b2d", COVERED: "#2f9e6e", OPPORTUNITY: "#6c7a89"}
RATING_COLOR = {"Critical": "#d64545", "High": "#e07a2d", "Medium": "#e0b72d", "Low": "#2f9e6e"}
CHANGE_COLOR = {NEW: "#2e6bd6", AMENDED: "#7a4fd6", REMOVED: "#6c7a89", UNCHANGED: "#9aa7b5"}

CSS = """
<style>
  .block-container { padding-top: 1.4rem; max-width: 1280px; }
  .rr-hero { position: relative; overflow: hidden; border-radius: 16px; padding: 1.4rem 1.6rem; margin: .6rem 0 1rem 0;
             background: radial-gradient(circle at 85% 30%, #1d4f8f 0%, #0d2546 45%, #071427 100%); color: #e8f0fb; }
  .rr-hero h1 { color: #fff; font-size: 1.9rem; margin: 0; letter-spacing: .01em; }
  .rr-hero .tag { color: #9fc0ea; font-size: .78rem; text-transform: uppercase; letter-spacing: .14em; }
  .rr-hero p { color: #c9d9ee; margin: .35rem 0 0 0; max-width: 760px; font-size: .95rem; }
  .rr-radar { position: absolute; right: 26px; top: 50%; width: 150px; height: 150px; margin-top: -75px;
              border-radius: 50%; border: 1px solid rgba(120,180,255,.35);
              background: repeating-radial-gradient(circle, rgba(120,180,255,.18) 0 1px, transparent 1px 25px); }
  .rr-radar::before { content: ""; position: absolute; inset: 0; border-radius: 50%;
              background: conic-gradient(from 0deg, rgba(61,220,151,.0) 0deg, rgba(61,220,151,.45) 40deg,
                          rgba(61,220,151,0) 41deg); animation: rr-sweep 3.2s linear infinite; }
  .rr-radar i { position: absolute; width: 8px; height: 8px; border-radius: 50%; background: #ff6b6b;
                box-shadow: 0 0 10px #ff6b6b; animation: rr-blip 3.2s ease-in-out infinite; }
  @keyframes rr-sweep { to { transform: rotate(360deg); } }
  @keyframes rr-blip { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
  @media (max-width: 760px) { .rr-radar { display: none; } }
  .rr-kpis { display: flex; flex-wrap: wrap; gap: .6rem; margin: .2rem 0 1rem 0; }
  .rr-kpi { flex: 1 1 96px; border-radius: 12px; padding: .65rem .85rem; background: #f4f7fb;
            border: 1px solid #e1e8f2; }
  .rr-kpi .v { font-size: 1.6rem; font-weight: 700; color: #0d2546; line-height: 1.1; }
  .rr-kpi .l { font-size: .74rem; color: #5a6b80; text-transform: uppercase; letter-spacing: .06em; }
  .rr-kpi.red { box-shadow: inset 4px 0 0 #d64545; } .rr-kpi.amber { box-shadow: inset 4px 0 0 #e09b2d; }
  .rr-kpi.green { box-shadow: inset 4px 0 0 #2f9e6e; } .rr-kpi.blue { box-shadow: inset 4px 0 0 #2e6bd6; }
  .rr-circ { border-radius: 12px; border: 1px solid #dfe6ef; padding: .7rem .8rem; min-height: 150px;
             background: linear-gradient(180deg, #ffffff 0%, #f6f9fc 100%); }
  .rr-circ.sel { border: 2px solid #2e6bd6; box-shadow: 0 4px 16px rgba(46,107,214,.18); }
  .rr-circ .id { font-family: ui-monospace, monospace; font-size: .75rem; color: #5a6b80; }
  .rr-circ .t { font-weight: 650; font-size: .9rem; color: #0d2546; margin: .15rem 0 .35rem 0; line-height: 1.25; }
  .rr-circ .d { font-size: .78rem; color: #3f4f63; }
  .rr-chip { display: inline-block; padding: .08rem .5rem; border-radius: 999px; font-size: .72rem; font-weight: 650;
             color: #fff; margin: .1rem .25rem .1rem 0; }
  .rr-crew { display: flex; flex-wrap: wrap; gap: .45rem; margin: .4rem 0 .8rem 0; }
  .rr-agent { flex: 1 1 96px; border-radius: 10px; padding: .5rem .6rem; border: 1px solid #dfe6ef; background: #fff;
              font-size: .78rem; color: #3f4f63; transition: all .2s; }
  .rr-agent .n { font-weight: 700; color: #0d2546; font-size: .82rem; }
  .rr-agent.work { border-color: #2e6bd6; box-shadow: 0 0 0 3px rgba(46,107,214,.18); background: #f2f7ff; }
  .rr-agent.done { border-color: #2f9e6e; background: #f2fbf6; }
  .rr-agent.warn { border-color: #e09b2d; background: #fff9ef; }
  .rr-log { font-family: ui-monospace, monospace; font-size: .78rem; background: #0b1a2e; color: #cfe0f5;
            border-radius: 10px; padding: .7rem .9rem; max-height: 340px; overflow-y: auto; }
  .rr-log .a { color: #7fb4ff; } .rr-log .w { color: #ffc857; } .rr-log .ok { color: #3ddc97; }
  .rr-red { font-size: .92rem; line-height: 1.55; }
  .rr-red del { background: #fde2e2; color: #9b1c1c; text-decoration: line-through; padding: 0 .15rem; }
  .rr-red ins { background: #dcf5e7; color: #14532d; text-decoration: none; padding: 0 .15rem; border-radius: 3px; }
  .rr-card { border: 1px solid #e1e8f2; border-radius: 12px; padding: .75rem .95rem; margin-bottom: .6rem;
             background: #fff; }
  .rr-card .h { display: flex; justify-content: space-between; gap: .5rem; flex-wrap: wrap; align-items: center; }
  .rr-card .pid { font-family: ui-monospace, monospace; font-size: .8rem; color: #3f4f63; }
  .rr-side { border-left: 3px solid #c3d0e0; padding: .3rem .7rem; font-size: .88rem; margin: .3rem 0; }
  .rr-side.reg { border-color: #2e6bd6; } .rr-side.pol { border-color: #2f9e6e; }
  .rr-summary { background: #f4f7fb; border-radius: 12px; padding: .9rem 1.1rem; border: 1px solid #e1e8f2;
                white-space: pre-wrap; font-size: .92rem; }
  .rr-foot { color: #6c757d; font-size: .78rem; margin-top: 2rem; }
</style>
"""


def hero() -> str:
    blips = "".join(f'<i style="left:{x}px;top:{y}px;animation-delay:{d}s"></i>'
                    for x, y, d in ((95, 40, 0.2), (40, 95, 1.1), (110, 105, 2.0), (60, 30, 2.6)))
    return (
        '<div class="rr-hero"><div class="tag">Ledgerline Bank · Regulatory Change Management</div>'
        "<h1>RegRadar: Regulatory Change Impact Analyzer</h1>"
        "<p>A crew of AI agents reads each new regulatory circular, compares it with the version it replaces, maps "
        "every change to Ledgerline's policies and controls, scores the gaps, drafts the fixes and challenges its own "
        "work. A named human signs off.</p>"
        f'<div class="rr-radar">{blips}</div></div>'
    )


def chip(text: str, color: str) -> str:
    return f'<span class="rr-chip" style="background:{color}">{html.escape(text)}</span>'


def kpis(items: list[tuple[str, str, str]]) -> str:
    cells = "".join(f'<div class="rr-kpi {c}"><div class="v">{html.escape(str(v))}</div>'
                    f'<div class="l">{html.escape(label)}</div></div>' for v, label, c in items)
    return f'<div class="rr-kpis">{cells}</div>'


def circular_card(a: Assessment, as_of: date, selected: bool) -> str:
    c = a.circular
    try:
        days = (date.fromisoformat(c.effective_date) - as_of).days
        when = f"in {days} days" if days >= 0 else f"{-days} days ago"
    except ValueError:
        when = "date not stated"
    rc = a.rating_counts()
    chips = "".join(chip(f"{rc[r]} {r}", RATING_COLOR[r]) for r in ("Critical", "High") if rc.get(r))
    return (f'<div class="rr-circ{" sel" if selected else ""}"><div class="id">{html.escape(c.circular_id)}</div>'
            f'<div class="t">{html.escape(c.title)}</div>'
            f'<div class="d">Effective {html.escape(c.effective_date)} · <b>{when}</b></div>'
            f'<div class="d">{len(a.gaps)} gaps · {len(a.actions)} actions</div><div>{chips}</div></div>')


def crew_html(states: dict[str, str]) -> str:
    cells = []
    for a in CREW:
        state = states.get(a.key, "")
        label = {"work": "working…", "done": "done", "warn": "done, with warnings"}.get(state, "waiting")
        cells.append(f'<div class="rr-agent {state}"><div class="n">{a.icon} {html.escape(a.name)}</div>'
                     f"{label}</div>")
    cells.append('<div class="rr-agent"><div class="n">👤 Human approver</div>signs off</div>')
    return f'<div class="rr-crew">{"".join(cells)}</div>'


def log_html(lines: list[tuple[str, str, str, float]]) -> str:
    rows = []
    names = {a.key: f"{a.icon} {a.name}" for a in CREW}
    for agent, kind, text, t in lines:
        cls = {"warn": "w", "done": "ok"}.get(kind, "")
        rows.append(f'<div><span class="a">{t:7.0f} ms {html.escape(names.get(agent, agent))}</span> '
                    f'<span class="{cls}">{html.escape(text)}</span></div>')
    return f'<div class="rr-log">{"".join(rows)}</div>'


def change_card(c: Change) -> str:
    head = chip(c.change_type, CHANGE_COLOR[c.change_type])
    if c.materiality not in ("None",):
        head += chip(f"materiality {c.materiality}", {"High": "#d64545", "Medium": "#e09b2d"}.get(c.materiality,
                                                                                                   "#6c7a89"))
    deltas = "".join(chip(p.summary, "#d64545" if p.direction == "tightened" else "#2f9e6e")
                     for p in c.parameter_changes if p.direction != "unchanged")
    sec = html.escape(f"§{c.provision.section_no} {c.provision.section_title}")
    return (f'<div class="rr-card"><div class="h"><span class="pid">{html.escape(c.pid)} · {sec}</span>'
            f"<span>{head}</span></div><div class=\"rr-red\">{redline_html(c)}</div><div>{deltas}</div></div>")


def horizon_chart(assessments: dict[str, Assessment]) -> alt.Chart:
    rows = []
    for a in assessments.values():
        rc = a.rating_counts()
        rows.append({"circular": a.circular.circular_id, "title": a.circular.title,
                     "effective": a.circular.effective_date, "gaps": len(a.gaps), "critical": rc.get("Critical", 0),
                     "domain": a.circular.domain})
    df = pd.DataFrame(rows)
    df["effective"] = pd.to_datetime(df["effective"])
    return (alt.Chart(df).mark_circle(opacity=.85, stroke="#0d2546", strokeWidth=1).encode(
        x=alt.X("effective:T", title="Takes effect", axis=alt.Axis(format="%b %Y")),
        y=alt.Y("domain:N", title=None),
        size=alt.Size("gaps:Q", title="Gaps", scale=alt.Scale(range=[200, 1600])),
        color=alt.Color("critical:Q", title="Critical gaps", scale=alt.Scale(scheme="orangered")),
        tooltip=["circular", "title", alt.Tooltip("effective:T", format="%d %b %Y"), "gaps", "critical"],
    ).properties(height=230))


def heatmap(assessments: dict[str, Assessment]) -> alt.Chart:
    rows = []
    for a in assessments.values():
        for f in a.gaps:
            for ref in f.policy_refs or ["(no policy)"]:
                rows.append({"circular": a.circular.circular_id, "policy": ref.split(" §")[0], "status": f.status,
                             "score": f.risk.score if f.risk else 0})
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame([{"circular": "-", "policy": "-", "gaps": 0, "max_score": 0}])
    else:
        df = df.groupby(["circular", "policy"]).agg(gaps=("status", "size"), max_score=("score", "max")).reset_index()
    base = alt.Chart(df).encode(x=alt.X("policy:N", title="Ledgerline policy"), y=alt.Y("circular:N", title=None))
    return (base.mark_rect(cornerRadius=4).encode(
        color=alt.Color("max_score:Q", title="Worst risk score", scale=alt.Scale(scheme="reds", domain=[0, 25])),
        tooltip=["circular", "policy", "gaps", "max_score"])
        + base.mark_text(fontWeight="bold").encode(text="gaps:Q")).properties(height=240)


def trace_graph(a: Assessment) -> str:
    """Graphviz DOT: provision → policy section → control, coloured by gap status."""
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.9;',
             'node [fontname="Helvetica" fontsize=10 style="filled,rounded" shape=box color="#c3d0e0"];',
             'edge [color="#9aa9bd" arrowsize=0.6];']
    seen = set()

    def node(nid: str, label: str, fill: str, font: str = "#0d2546", shape: str = "box") -> None:
        if nid in seen:
            return
        seen.add(nid)
        safe = label.replace('"', "'")
        lines.append(f'"{nid}" [label="{safe}" fillcolor="{fill}" fontcolor="{font}" shape={shape}];')

    # A control can be hit by several findings: show its strongest required action once.
    strength = {"Reconfigure": 2, "Review": 1}
    control_action: dict[str, str] = {}
    for f in a.findings:
        for c in f.controls:
            if c.control_id != "NEW" and strength.get(c.action, 0) > strength.get(control_action.get(c.control_id), 0):
                control_action[c.control_id] = c.action
    edges: set[tuple[str, str]] = set()

    def edge(src: str, dst: str, style: str = "") -> None:
        if (src, dst) not in edges:
            edges.add((src, dst))
            lines.append(f'"{src}" -> "{dst}"{style};')

    for f in a.findings:
        if f.status == OPPORTUNITY:
            continue
        pid = f.provision.pid
        color = STATUS_COLOR[f.status]
        node(pid, f"{pid.split(' ', 1)[1]}\\n{STATUS_LABEL[f.status]}", color, "#ffffff")
        if not f.policy_refs:
            node(f"gap-{pid}", "No policy\\ncovers this", "#fbe4ef", "#b02a6b", "note")
            edge(pid, f"gap-{pid}", ' [style=dashed color="#b02a6b"]')
        for ref in f.policy_refs:
            node(ref, ref, "#e7f0fb")
            edge(pid, ref)
            for c in f.controls:
                if c.control_id == "NEW":
                    continue
                action = control_action[c.control_id]
                fill = "#fde2e2" if action == "Reconfigure" else "#f4f7fb"
                node(c.control_id, f"{c.control_id}\\n{action}", fill, "#0d2546", "component")
                edge(ref, c.control_id)
    lines.append("}")
    return "\n".join(lines)


def findings_frame(a: Assessment) -> pd.DataFrame:
    return pd.DataFrame([{
        "Finding": f.fid, "Provision": f.provision.pid, "Change": f.change.change_type,
        "What changed": f.change.headline, "Status": STATUS_LABEL[f.status], "Rating": f.rating,
        "Score": f.risk.score if f.risk else 0, "Policy": ", ".join(f.policy_refs) or "-",
        "Controls": ", ".join(c.control_id for c in f.controls) or "-", "Confidence": f.confidence,
        "Human review": "required" if f.needs_review else "-"} for f in a.findings])


FOOTER = ('<div class="rr-foot">Independent AI-assisted portfolio project. Ledgerline Bank, the Northbridge Prudential '
          "and Conduct Authority (NPCA), all circulars, policies and controls are synthetic. Not legal or regulatory "
          "advice.</div>")
