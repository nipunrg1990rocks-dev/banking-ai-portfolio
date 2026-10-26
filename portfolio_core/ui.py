"""Shared Streamlit styling so all five portfolio apps look like one product."""

from __future__ import annotations

PORTFOLIO_CSS = """
<style>
  .block-container { padding-top: 2rem; max-width: 1200px; }
  .pf-banner {
    background: linear-gradient(90deg, #0b2545 0%, #13315c 100%);
    color: #ffffff; padding: 1.1rem 1.4rem; border-radius: 10px; margin-bottom: 1rem;
  }
  .pf-banner h1 { color: #ffffff; font-size: 1.55rem; margin: 0 0 .25rem 0; }
  .pf-banner p { color: #d6e2f0; margin: 0; font-size: .95rem; }
  .pf-pill {
    display: inline-block; padding: .15rem .6rem; border-radius: 999px;
    font-size: .78rem; font-weight: 600; margin-right: .35rem;
  }
  .pf-pill.green { background: #d8f3dc; color: #1b4332; }
  .pf-pill.amber { background: #fff3cd; color: #7a5200; }
  .pf-pill.red { background: #f8d7da; color: #842029; }
  .pf-pill.blue { background: #dbe9ff; color: #0b3d91; }
  .pf-pill.grey { background: #e9ecef; color: #343a40; }
  .pf-citation {
    border-left: 4px solid #13315c; background: rgba(19,49,92,.05);
    padding: .6rem .9rem; border-radius: 6px; margin-bottom: .6rem; font-size: .9rem;
  }
  .pf-footer { color: #6c757d; font-size: .8rem; margin-top: 2rem; }
</style>
"""


def pill(text: str, color: str = "grey") -> str:
    return f'<span class="pf-pill {color}">{text}</span>'


def banner(title: str, subtitle: str) -> str:
    return f'<div class="pf-banner"><h1>{title}</h1><p>{subtitle}</p></div>'


FOOTER = (
    '<div class="pf-footer">Independent AI-assisted portfolio project. All policies, customers and '
    "transactions are synthetic. Not affiliated with any real financial institution.</div>"
)
