"""Orchestration: prompt -> spec -> governed queries -> payload -> .pptx -> version + audit.

This is the single entry point the Streamlit app and the tests use, so the acceptance
demo (generate, follow up, save a second version, refresh data, edit commentary, export,
show provenance) is exercised identically in both.
"""

from __future__ import annotations

import copy
import hashlib
import io
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from portfolio_core.audit import AuditLog
from portfolio_core.llm import ClaudeClient, LLMUnavailable, live_llm_available

from . import payload as P
from .domains.base import Domain
from .domains.ops_risk import OpsRiskDomain, synthetic_ops_risk
from .domains.workforce import WorkforceDomain, synthetic_roster
from .parser import LLMParser, PromptRejected, RulesParser, apply_followup, detect_domain
from .render import render, to_pdf
from .spec import DeckSpec, period_label
from .store import Dataset, DatasetRegistry, QueryBuilder
from .validation import read_table
from .versioning import VersionRecord, VersionStore, now

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data"


class ReviewError(ValueError):
    pass


def file_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReportingService:
    def __init__(self, workdir: str | Path | None = None, use_llm: bool | None = None, llm_client=None) -> None:
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="p2p_"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.domains: dict[str, Domain] = {"ops_risk": OpsRiskDomain(), "workforce": WorkforceDomain()}
        self.registry = DatasetRegistry()
        self.qbs = {k: QueryBuilder(d.tables) for k, d in self.domains.items()}
        db = self.workdir / "p2p.db"
        self.store = VersionStore(db)
        self.audit = AuditLog(db)
        self.rules = RulesParser()
        self.llm = None
        if llm_client is not None:
            self.llm = LLMParser(llm_client)
        elif use_llm is not False and live_llm_available():
            try:
                self.llm = LLMParser(ClaudeClient())
            except LLMUnavailable:
                self.llm = None

    @property
    def parser_mode(self) -> str:
        return "Claude (validated)" if self.llm else "Rules-based (offline, no API cost)"

    # ---------------------------------------------------------------- datasets
    def bootstrap(self) -> None:
        """Load and activate the shipped synthetic datasets (CSV in data/, regenerated if missing)."""
        ops_csv, wf_csv = DATA / "ops_risk_synthetic.csv", DATA / "workforce_roster_synthetic.csv"
        ops = pd.read_csv(ops_csv, dtype=str, keep_default_na=False, na_values=[""]) if ops_csv.exists() else \
            synthetic_ops_risk().astype(object)
        wf = pd.read_csv(wf_csv, dtype=object) if wf_csv.exists() else synthetic_roster()
        for key, raw, src, snap in [("ops_risk", ops, ops_csv.name, None),
                                    ("workforce", wf, wf_csv.name, "2018-09-30")]:
            ds = self.domains[key].load(raw, src, True, self.registry, snapshot=snap) if key == "workforce" else \
                self.domains[key].load(raw, src, True, self.registry)
            self.registry.stage(ds)
            self.activate(ds.version, actor="system")

    def stage_upload(self, domain_key: str, data: bytes, filename: str, actor: str = "user",
                     snapshot: str | None = None, synthetic: bool | None = None) -> Dataset:
        """Validate a file into a staged dataset. ``synthetic`` is True only for the shipped sample files."""
        domain = self.domains[domain_key]
        try:
            raw = read_table(data, filename, sheet_hint="Data")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as a validation error
            from .validation import ValidationReport
            rep = ValidationReport(filename)
            rep.add("File readable", "error", 1, str(exc)[:200])
            ds = Dataset(domain_key, self.registry.next_version(domain.prefix, hashlib.sha256(data).hexdigest()), {},
                         [], rep, filename, False, hashlib.sha256(data).hexdigest())
            self.audit.record("dataset_rejected", {"file": filename, "reason": str(exc)[:200]}, actor=actor)
            return ds
        synthetic = bool(synthetic)
        if domain_key == "workforce":
            ds = domain.load(raw, filename, synthetic, self.registry, snapshot=snapshot)
        else:
            ds = domain.load(raw, filename, synthetic, self.registry)
        self.registry.stage(ds)
        self.audit.record("dataset_staged", {"version": ds.version, **ds.report.summary()}, actor=actor,
                          correlation_id=ds.version)
        return ds

    def activate(self, version: str, actor: str = "user") -> Dataset:
        ds = self.registry.activate(version)
        self.audit.record("dataset_activated", ds.meta(), actor=actor, correlation_id=ds.version)
        return ds

    def dataset(self, domain_key: str) -> Dataset:
        return self.registry.current(domain_key)

    # ---------------------------------------------------------------- deck building
    def build_payload(self, spec: DeckSpec, ds: Dataset, previous: dict[str, Any] | None = None,
                      regenerate: list[int] | None = None) -> tuple[dict[str, Any], list[str]]:
        domain = self.domains[spec.domain]
        problems = spec.validate_against(domain, ds)
        if problems:
            raise ValueError("Spec failed validation: " + "; ".join(problems))
        ctx = domain.context(spec, ds, self.qbs[spec.domain])
        slides = []
        total = len(spec.slides)
        for i, req in enumerate(spec.slides, start=1):
            if previous is not None and regenerate is not None and i not in regenerate and \
                    i <= len(previous["slides"]) and previous["slides"][i - 1]["slide_type"] == req.slide_type:
                slides.append(copy.deepcopy(previous["slides"][i - 1]))
                continue
            s = domain.build_slide(req.slide_type, ctx, i, total)
            if req.title:
                s["title"] = req.title
            if not spec.commentary and req.slide_type != "commentary":
                s["notes_extra"] = (s.get("notes_extra", "") + " Commentary suppressed on slide by request.").strip()
                s["commentary_hidden"] = s.pop("commentary")
                s["commentary"] = []
            slides.append(s)
        tpl = domain.templates[spec.template]
        deck = {
            "domain": spec.domain, "template": spec.template, "accent": tpl.accent, "kicker": tpl.label,
            "slides": slides,
            "meta": {
                "org": domain.org, "synthetic": ds.synthetic, "dataset_version": ds.version, "dataset_sha": ds.sha256,
                "dataset_source": ds.source, "data_to": ds.months[-1], "snapshot": ds.snapshot,
                "period": period_label(spec.months), "comparison": ctx.cmp_period,
                "filters_label": ctx.filters_label(domain.filter_dimensions), "disclosures": ctx.disclosures,
                "fingerprints": [q.fingerprint for q in ctx.queries], "queries": [q.as_dict() for q in ctx.queries],
                "speaker_notes": spec.speaker_notes, "kpi_definitions": domain.kpi_dictionary().to_dict("records"),
            },
        }
        return deck, ctx.disclosures

    def _save(self, deck_id: str, parent: VersionRecord | None, spec: DeckSpec, ds: Dataset, deck: dict[str, Any],
              action: str, prompt: str, parser_mode: str, actor: str, notes: list[str]) -> VersionRecord:
        version_no = self.store.next_version_no(deck_id)
        vid = uuid.uuid4().hex[:12]
        gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        deck["meta"].update({"deck_id": deck_id, "version_label": f"{deck_id} v{version_no}", "generated_at": gen,
                             "status": "draft", "status_text": "DRAFT – for internal review only",
                             "parser_mode": parser_mode, "prompt": prompt})
        deck["comments"] = {}
        slug = "".join(ch if ch.isalnum() else "_" for ch in deck["slides"][0]["title"])[:40].strip("_")
        path = self.workdir / "decks" / deck_id / f"{deck_id}_v{version_no:02d}_{slug}.pptx"
        render(deck, path)
        rec = VersionRecord(vid, deck_id, version_no, parent.version_id if parent else None, now(), actor, action,
                            prompt, parser_mode, spec.model_dump(), ds.version, ds.sha256, spec.filters,
                            period_label(spec.months), deck, str(path), file_sha(path), "draft", notes,
                            deck["meta"]["fingerprints"], None)
        self.store.add_version(rec)
        self.audit.record("deck_version_created", {
            "version": rec.label, "action": action, "prompt": prompt, "parser": parser_mode, "spec": spec.model_dump(),
            "dataset_version": ds.version, "dataset_sha": ds.sha256, "filters": spec.filters,
            "period": rec.period, "query_fingerprints": rec.fingerprints, "file_sha256": rec.file_sha256,
            "review_status": "draft", "notes": notes}, actor=actor, correlation_id=deck_id)
        return rec

    def generate(self, prompt: str, domain_key: str | None = None, actor: str = "user",
                 force_domain: bool = False) -> tuple[VersionRecord, list[str]]:
        dkey = domain_key or "ops_risk"
        if not force_domain:
            dkey = detect_domain(prompt, self.domains, dkey)
        domain = self.domains[dkey]
        ds = self.dataset(dkey)
        try:
            res = (self.llm or self.rules).parse(prompt, domain, ds)
        except PromptRejected as exc:
            self.audit.record("prompt_rejected", {"prompt": prompt[:300], "reason": str(exc)}, actor=actor)
            raise
        deck, _ = self.build_payload(res.spec, ds)
        deck_id = self.store.new_deck(dkey, res.spec.title, actor)
        rec = self._save(deck_id, None, res.spec, ds, deck, "generate", res.sanitized_prompt, res.mode, actor,
                         res.notes)
        if res.llm_raw:
            self.audit.record("llm_output", {"raw": res.llm_raw[:4000]}, actor="llm", correlation_id=deck_id)
        return rec, res.notes

    def generate_from_spec(self, spec: DeckSpec, label: str, actor: str = "user") -> VersionRecord:
        ds = self.dataset(spec.domain)
        deck, _ = self.build_payload(spec, ds)
        deck_id = self.store.new_deck(spec.domain, spec.title, actor)
        return self._save(deck_id, None, spec, ds, deck, "generate_from_dashboard", label, "dashboard state", actor,
                          [])

    def follow_up(self, version_id: str, prompt: str, actor: str = "user") -> tuple[VersionRecord, list[str]]:
        parent = self.store.get(version_id)
        spec = DeckSpec.model_validate(parent.spec)
        domain = self.domains[spec.domain]
        pinned = self.registry.get(parent.dataset_version)
        try:
            fu = apply_followup(prompt, spec, domain, pinned)
        except PromptRejected as exc:
            self.audit.record("prompt_rejected", {"prompt": prompt[:300], "reason": str(exc)}, actor=actor,
                              correlation_id=parent.deck_id)
            raise
        if not fu.changed:
            raise ValueError(" ".join(fu.notes) + " Try e.g. " + "; ".join(domain.followup_examples[:4]) + ".")
        ds = self.dataset(spec.domain) if fu.use_latest_data else pinned
        notes = list(fu.notes)
        if not fu.use_latest_data and pinned.version != self.dataset(spec.domain).version:
            notes.append(f"Kept dataset {pinned.version} for consistency with v{parent.version_no}; a newer dataset "
                         f"{self.dataset(spec.domain).version} is active (say 'refresh using latest data').")
        if fu.use_latest_data and ds.version == parent.dataset_version:
            notes.append(f"{ds.version} is already the latest validated dataset; numbers unchanged.")
        if fu.action == "fresh_copy" and not fu.use_latest_data and fu.spec == spec:
            deck = copy.deepcopy(parent.payload)
            for s in deck["slides"]:
                s.pop("unverified", None)
        else:
            problems = fu.spec.validate_against(domain, ds)
            if problems:
                raise ValueError("That change would leave the deck invalid: " + "; ".join(problems))
            regen = fu.slides_to_regenerate if fu.action == "replace_slide" and not fu.use_latest_data else None
            deck, _ = self.build_payload(fu.spec, ds, previous=parent.payload if regen else None, regenerate=regen)
            if regen:
                deck["meta"]["disclosures"] = parent.payload["meta"]["disclosures"]
        action = "refresh" if fu.use_latest_data else fu.action
        rec = self._save(parent.deck_id, parent, fu.spec, ds, deck, action, prompt, fu.mode, actor, notes)
        return rec, notes

    def edit_commentary(self, version_id: str, slide_no: int, lines: list[str], actor: str = "user",
                        title: str | None = None) -> tuple[VersionRecord, list[str]]:
        parent = self.store.get(version_id)
        deck = copy.deepcopy(parent.payload)
        s = deck["slides"][slide_no - 1]
        before = {"title": s["title"], "commentary": [c["text"] for c in s["commentary"]]}
        old_ev = s["commentary"]
        new = []
        for i, line in enumerate(x.strip() for x in lines):
            if not line:
                continue
            ev = old_ev[i]["evidence"] if i < len(old_ev) and old_ev[i]["text"] == line else None
            new.append({"text": line[:600], "kind": "fact" if ev else "edited", "evidence": ev or {}})
        unverified = sorted({n for c in new if c["kind"] == "edited" for n in P.unverified_numbers(c["text"],
                                                                                                   parent.payload["slides"]
                                                                                                   [slide_no - 1])})
        s["commentary"] = new
        if title:
            s["title"] = title[:120]
        s["human_edited"] = True
        s["edited_by"] = actor
        s["unverified"] = unverified
        spec = DeckSpec.model_validate(parent.spec)
        ds = self.registry.get(parent.dataset_version)
        notes = [f"Commentary on slide {slide_no} edited by {actor}."]
        if unverified:
            notes.append(f"Numbers not found in the slide's computed data: {', '.join(unverified)}. Check before "
                         "approval.")
        rec = self._save(parent.deck_id, parent, spec, ds, deck, "edit_commentary", f"[edit slide {slide_no}]",
                         "human edit", actor, notes)
        self.audit.record("commentary_edited", {"version": rec.label, "slide": slide_no, "before": before,
                                                "after": {"title": s["title"], "commentary": [c["text"] for c in new]},
                                                "unverified_numbers": unverified}, actor=actor,
                          correlation_id=parent.deck_id)
        return rec, notes

    def add_comment(self, version_id: str, slide_no: int, text: str, author: str) -> int:
        text = (text or "").strip()[:800]
        if not text:
            raise ValueError("Comment is empty")
        rec = self.store.get(version_id)
        cid = self.store.add_comment(version_id, slide_no, author, text)
        self.audit.record("review_comment", {"version": rec.label, "slide": slide_no, "text": text}, actor=author,
                          correlation_id=rec.deck_id)
        return cid

    def resolve_comment(self, comment_id: int, actor: str) -> None:
        self.store.resolve_comment(comment_id)
        self.audit.record("review_comment_resolved", {"comment_id": comment_id}, actor=actor)

    def review(self, version_id: str, decision: str, reviewer: str, note: str = "") -> VersionRecord:
        rec = self.store.get(version_id)
        reviewer = (reviewer or "").strip()
        if not reviewer:
            raise ReviewError("Reviewer name is required")
        if decision == "approved":
            if reviewer.lower() == rec.actor.lower():
                raise ReviewError("Maker-checker: the person who generated or edited this version cannot approve it.")
            open_comments = [c for c in self.store.comments(version_id) if c["status"] == "open"]
            if open_comments:
                raise ReviewError(f"{len(open_comments)} review comment(s) are still open; resolve them first.")
            if any(s.get("unverified") for s in rec.payload["slides"]) and "verified" not in note.lower():
                raise ReviewError("Edited commentary contains unverified numbers; add a note confirming they were "
                                  "verified (include the word 'verified').")
        approved_file = None
        if decision == "approved":
            deck = copy.deepcopy(rec.payload)
            deck["meta"].update({"status": "approved", "approved": True,
                                 "status_text": f"APPROVED by {reviewer} {now()[:10]} – internal use"})
            deck["comments"] = self._comment_map(version_id)
            p = Path(rec.file_path)
            approved = p.with_name(p.stem + "_APPROVED.pptx")
            if not approved.exists():
                render(deck, approved)
            approved_file = str(approved)
        self.store.add_review(version_id, reviewer, decision, note)
        self.store.set_status(version_id, decision, approved_file)
        self.audit.record("review_decision", {"version": rec.label, "decision": decision, "note": note,
                                              "approved_file_sha256": file_sha(approved_file) if approved_file else
                                              None}, actor=reviewer, correlation_id=rec.deck_id)
        return self.store.get(version_id)

    def _comment_map(self, version_id: str) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for c in self.store.comments(version_id):
            out.setdefault(str(c["slide_no"]), []).append(c)
        return out

    def download(self, version_id: str, with_comments: bool = True) -> tuple[bytes, str]:
        """Bytes of the version's .pptx (approved rendition if approved). Comments are exported into notes."""
        rec = self.store.get(version_id)
        if rec.review_status == "approved" and rec.approved_file:
            return Path(rec.approved_file).read_bytes(), Path(rec.approved_file).name
        comments = self._comment_map(version_id)
        if with_comments and comments:
            deck = copy.deepcopy(rec.payload)
            deck["comments"] = comments
            p = Path(rec.file_path)
            out = p.with_name(p.stem + f"_with_comments_{len(sum(comments.values(), []))}.pptx")
            if not out.exists():
                render(deck, out)
            return out.read_bytes(), out.name
        return Path(rec.file_path).read_bytes(), Path(rec.file_path).name

    def export_pdf(self, version_id: str) -> Path | None:
        rec = self.store.get(version_id)
        src = rec.approved_file if rec.review_status == "approved" and rec.approved_file else rec.file_path
        pdf = Path(src).with_suffix(".pdf")
        if pdf.exists():
            return pdf
        out = to_pdf(src, Path(src).parent)
        if out:
            self.audit.record("pdf_exported", {"version": rec.label, "sha256": file_sha(out)}, actor="system",
                              correlation_id=rec.deck_id)
        return out

    def provenance(self, version_id: str) -> dict[str, Any]:
        rec = self.store.get(version_id)
        m = rec.payload["meta"]
        return {
            "version": rec.label, "created_at": rec.created_at, "actor": rec.actor, "action": rec.action,
            "prompt": rec.prompt, "parser_mode": rec.parser_mode, "review_status": rec.review_status,
            "dataset_version": rec.dataset_version, "dataset_sha256": rec.dataset_sha,
            "dataset_source": m.get("dataset_source"), "synthetic": m.get("synthetic"), "data_to": m.get("data_to"),
            "period": rec.period, "comparison": m.get("comparison"), "filters": rec.filters,
            "spec": rec.spec, "queries": m.get("queries"), "kpi_definitions": m.get("kpi_definitions"),
            "disclosures": m.get("disclosures"), "file": Path(rec.file_path).name, "file_sha256": rec.file_sha256,
            "parent_version_id": rec.parent_id, "notes": rec.notes,
        }

    def audit_frame(self, limit: int = 200) -> pd.DataFrame:
        ev = self.audit.events(limit=limit)
        return pd.DataFrame([{"seq": e.seq, "time": e.timestamp, "actor": e.actor, "event": e.event_type,
                              "ref": e.correlation_id, "hash": e.hash[:12], "prev": e.prev_hash[:12],
                              "payload": e.payload} for e in ev])


def csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode()
