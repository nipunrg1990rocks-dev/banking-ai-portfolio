---
doc_id: MRM-006
title: Model Risk Management and AI Governance Policy
domain: Model Risk & AI
version: 1.4
owner: Head of Model Risk Management
effective_date: 2026-05-20
classification: Internal (synthetic demonstration document)
---

# Model Risk Management and AI Governance Policy

This is a synthetic policy for the fictional Ledgerline Bank. It is used for demonstration only.

## 1. Purpose and Scope

This policy governs the development, validation, use and monitoring of models and artificial intelligence (AI) systems, including statistical models, machine learning models and generative AI applications such as large language model (LLM) assistants.

## 2. Model and AI Inventory

Every model and AI use case must be registered in the Model and AI Inventory before use, with an owner, purpose, risk tier, data sources and validation status. Using an unregistered model or AI tool for a business decision is a policy breach.

## 3. Risk Tiering

AI and models are tiered as Tier 1 (high impact: used in credit decisions, financial crime decisions, capital or regulatory reporting, or customer-facing advice), Tier 2 (moderate impact: internal decision support) or Tier 3 (low impact: productivity tools with human review). Generative AI tools that answer policy questions for employees are Tier 2 unless their outputs are used directly in regulatory decisions, in which case they are Tier 1.

## 4. Independent Validation

Tier 1 models must be independently validated before first use and at least annually. Tier 2 models must be validated before first use and every two years. Validation must assess conceptual soundness, data quality, performance, stability, bias and fairness, and explainability.

## 5. Human Oversight

AI systems must not make final decisions on regulated matters, including customer onboarding approval, SAR filing, sanctions alert release, credit approval or customer complaints outcomes. A qualified human must review and approve AI outputs before they are used for any such decision. The human reviewer's identity, decision and rationale must be logged.

## 6. Generative AI Controls

Generative AI applications must: retrieve answers from approved, version-controlled sources; show citations to the source documents; display a confidence or uncertainty indicator; refuse or escalate questions outside their approved scope; prevent customer personal data from being sent to external model providers unless approved by the Chief Data Officer; and log prompts, retrieved sources, responses and reviewer decisions in a tamper-evident audit trail retained for at least five years.

## 7. Performance Monitoring

Model owners must monitor performance at least quarterly against documented thresholds. For generative AI, monitoring must include retrieval accuracy on a maintained evaluation set, citation accuracy and the rate of answers escalated to human review. Material performance degradation must be reported to Model Risk Management within 10 business days.

## 8. Third-Party and Vendor Models

Models and AI services provided by vendors are subject to this policy and to the Third-Party Risk Management Policy (TPR-008). The model owner remains accountable for vendor model outputs.
