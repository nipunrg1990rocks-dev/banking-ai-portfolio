# Databricks notebook source
# MAGIC %md
# MAGIC # RegRadar on Databricks: regulatory change impact analysis
# MAGIC
# MAGIC **What this does:** runs the RegRadar agent crew on every circular in the library, with a
# MAGIC **Databricks Foundation Model** as the Challenger's second opinion and the Reporter's writer, and
# MAGIC stores the results as Delta tables in Unity Catalog:
# MAGIC
# MAGIC | Layer | Table | Contents |
# MAGIC |---|---|---|
# MAGIC | Bronze | `regradar.bronze_circulars` | raw circular text and metadata |
# MAGIC | Silver | `regradar.silver_provisions` | one row per sentence: duty type, figures |
# MAGIC | Silver | `regradar.silver_changes` | new / amended / removed provisions with parameter moves |
# MAGIC | Gold | `regradar.gold_gap_register` | the gap register: status, policy, risk score, review flag |
# MAGIC | Gold | `regradar.gold_actions` | draft remediation actions (pending human sign-off) |
# MAGIC | Gold | `regradar.gold_evaluation` | scores against the answer key, offline vs model-assisted |
# MAGIC | Audit | `regradar.agent_events` | every step every agent took |
# MAGIC
# MAGIC It also shows **Databricks AI Functions**: `ai_query()` classifies every provision in SQL, and the
# MAGIC result is compared with the rules engine.
# MAGIC
# MAGIC **Cost and keys:** none. The model is called with this notebook's own sign-in, inside the workspace's
# MAGIC free quota. If no endpoint answers, everything still runs offline and the notebook says so.
# MAGIC
# MAGIC **How to run:** open this notebook from the repository's Git folder and click **Run all**.

# COMMAND ----------

# MAGIC %pip install -q rank-bm25==0.2.2 nltk==3.10.4

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog catalog")
dbutils.widgets.text("endpoint", "", "Model endpoint (blank = pick automatically)")
dbutils.widgets.dropdown("use_model", "yes", ["yes", "no"], "Use a foundation model")
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = f"{CATALOG}.regradar"

import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

PROJECT_DIR = os.path.abspath(os.path.join(os.getcwd(), ".."))
REPO_DIR = os.path.abspath(os.path.join(PROJECT_DIR, "..", ".."))
for p in (PROJECT_DIR, REPO_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from regradar.agents import DEMO_AS_OF, run_assessment
from regradar.corpus import load_circular_pairs
from regradar.evaluation import format_report, run_evaluation
from regradar.llm import DatabricksLLM, LLMError
from regradar.params import extract_quantities

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
print("Writing to", SCHEMA)

# COMMAND ----------

# MAGIC %md ## 1. Find a foundation-model endpoint (no API key: the notebook's own sign-in)

# COMMAND ----------

llm = None
if dbutils.widgets.get("use_model") == "yes":
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    token = w.config.authenticate()["Authorization"].split(" ", 1)[1]
    try:
        llm = DatabricksLLM.discover(w.config.host, token, dbutils.widgets.get("endpoint").strip() or None)
        print("Using", llm.label)
    except LLMError as exc:
        print("No model available, running offline:", exc)
else:
    print("Running offline (rules engine only).")

# COMMAND ----------

# MAGIC %md ## 2. Bronze and Silver: circulars, provisions and changes

# COMMAND ----------

pairs = load_circular_pairs()
bronze, provisions = [], []
for pair in pairs:
    for version, circ in (("current", pair.current), ("previous", pair.previous)):
        if circ is None:
            continue
        bronze.append({"key": pair.key, "version": version, "circular_id": circ.circular_id, "title": circ.title,
                       "regulator": circ.regulator, "published": circ.published,
                       "effective_date": circ.effective_date, "domain": circ.domain, "body": circ.body})
        for p in circ.provisions:
            provisions.append({"key": pair.key, "version": version, "pid": p.pid, "section": p.section_title,
                               "text": p.text, "modality": p.modality,
                               "figures": json.dumps([q.text for q in extract_quantities(p.text)])})
spark.createDataFrame(pd.DataFrame(bronze)).write.mode("overwrite").saveAsTable(f"{SCHEMA}.bronze_circulars")
spark.createDataFrame(pd.DataFrame(provisions)).write.mode("overwrite").saveAsTable(f"{SCHEMA}.silver_provisions")
display(spark.table(f"{SCHEMA}.silver_provisions").groupBy("key", "version", "modality").count()
        .orderBy("key", "version"))

# COMMAND ----------

# MAGIC %md ## 3. Run the agent crew on every circular

# COMMAND ----------

assessments = {p.key: run_assessment(p, DEMO_AS_OF, llm=llm) for p in pairs}
changes, gaps, actions, events = [], [], [], []
for key, a in assessments.items():
    for c in a.changes:
        changes.append({"key": key, "pid": c.pid, "change_type": c.change_type, "materiality": c.materiality,
                        "headline": c.headline, "text": c.provision.text})
    for f in a.findings:
        op = f.llm_opinion or {}
        gaps.append({"key": key, "finding": f.fid, "pid": f.provision.pid, "status": f.status, "rating": f.rating,
                     "score": f.risk.score if f.risk else 0, "policy_refs": ", ".join(f.policy_refs),
                     "confidence": f.confidence, "needs_review": f.needs_review,
                     "review_reasons": " | ".join(f.review_reasons), "rationale": " | ".join(f.rationale),
                     "model_status": str(op.get("status") or ""), "model_agrees": str(op.get("agrees", "")),
                     "model_rationale": str(op.get("rationale") or "")})
    for x in a.actions:
        actions.append({"key": key, "action": x.aid, "finding": x.finding_id, "kind": x.kind, "title": x.title,
                        "owner": x.owner, "due": x.due, "priority": x.priority, "before": x.before,
                        "after": x.after, "decision": x.decision})
    for e in a.events:
        events.append({"key": key, "run_id": a.run_id, "agent": e.agent, "kind": e.kind, "text": e.text,
                       "t_ms": float(e.t_ms)})
    print(f"{key}: {a.counts()} · summary by {a.summary_source}")

for name, rows in (("silver_changes", changes), ("gold_gap_register", gaps), ("gold_actions", actions),
                   ("agent_events", events)):
    spark.createDataFrame(pd.DataFrame(rows)).write.mode("overwrite").option("overwriteSchema", "true") \
        .saveAsTable(f"{SCHEMA}.{name}")
display(spark.table(f"{SCHEMA}.gold_gap_register").orderBy("score", ascending=False))

# COMMAND ----------

# MAGIC %md ## 4. Executive summary of the most urgent circular

# COMMAND ----------

urgent = min(assessments.values(), key=lambda a: a.circular.effective_date)
print(urgent.circular.circular_id, "· summary source:", urgent.summary_source)
print(urgent.summary)

# COMMAND ----------

# MAGIC %md ## 5. Databricks AI Functions: classify every provision with `ai_query()` in SQL

# COMMAND ----------

ai_rows = None
if llm is not None:
    try:
        ai_rows = spark.sql(f"""
            SELECT pid, modality AS rules_modality,
                   lower(trim(ai_query('{llm.endpoint}',
                       concat('Classify this regulatory sentence as exactly one word: obligation, prohibition, ',
                              'permission or information. Sentence: ', text)))) AS ai_modality
            FROM {SCHEMA}.silver_provisions WHERE version = 'current'""")
        ai_rows.write.mode("overwrite").saveAsTable(f"{SCHEMA}.silver_ai_modality")
        agree = spark.sql(f"""SELECT avg(CASE WHEN ai_modality LIKE concat('%', rules_modality, '%') THEN 1 ELSE 0 END)
                              AS agreement FROM {SCHEMA}.silver_ai_modality""").first()["agreement"]
        print(f"ai_query and the rules engine agree on {agree:.0%} of provisions")
        display(spark.table(f"{SCHEMA}.silver_ai_modality"))
    except Exception as exc:  # noqa: BLE001 - AI Functions availability varies by workspace
        print("ai_query is not available here:", str(exc).split("\n")[0][:300])
else:
    print("Skipped: no model endpoint.")

# COMMAND ----------

# MAGIC %md ## 6. Score against the answer key: offline vs model-assisted

# COMMAND ----------

offline = run_evaluation(DEMO_AS_OF)
print(format_report(offline))
reports = {"offline": offline}
if llm is not None:
    assisted = run_evaluation(DEMO_AS_OF, llm=llm)
    reports["model_assisted"] = assisted
    m = assisted["metrics"]
    print(f"\nWith {llm.label}: wrong statuses sent to human review {m['wrong_status_flagged_for_review']} of "
          f"{m['wrong_status']} (rules verdicts are unchanged; the model can only add review flags).")
rows = [{"mode": k, **{mk: (float(mv) if isinstance(mv, (int, float)) else None) for mk, mv in r["metrics"].items()}}
        for k, r in reports.items()]
spark.createDataFrame(pd.DataFrame(rows)).write.mode("overwrite").option("overwriteSchema", "true") \
    .saveAsTable(f"{SCHEMA}.gold_evaluation")
display(spark.table(f"{SCHEMA}.gold_evaluation"))

# COMMAND ----------

# MAGIC %md ## 7. Save evidence to the Git folder (commit and push it from Databricks to publish)

# COMMAND ----------

evidence = {
    "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "spark_version": spark.version,
    "schema": SCHEMA,
    "model": llm.label if llm else None,
    "model_usage": llm.usage.__dict__ if llm else None,
    "tables": {t: spark.table(f"{SCHEMA}.{t}").count() for t in
               ("bronze_circulars", "silver_provisions", "silver_changes", "gold_gap_register", "gold_actions",
                "agent_events")},
    "counts": {k: a.counts() for k, a in assessments.items()},
    "evaluation": {k: r["metrics"] for k, r in reports.items()},
}
out = os.path.join(PROJECT_DIR, "docs", "evidence")
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "databricks_run.json"), "w") as fh:
    json.dump(evidence, fh, indent=2, default=str)
print(json.dumps(evidence, indent=2, default=str))
