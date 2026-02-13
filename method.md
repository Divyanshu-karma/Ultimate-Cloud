# Risk Categorization Framework

## Overview
This system implements a retrieval-grounded, deterministic trademark risk assessment framework built on a structured multi-layer architecture:
- **Layer 5** → Retrieval Integrity
- **Layer 6** → Grounded Legal Reasoning (RAG)
- **Layer 7** → Deterministic Risk Assignment

Risk levels are assigned only after legally grounded issue identification and are determined exclusively through rule-based mapping of cited TMEP section prefixes.
The LLM does not assign risk levels.

---

## Architectural Strategy

### Layer 6 — Grounded Legal Reasoning (RAG)

#### Purpose
Identify trademark examination issues strictly supported by retrieved TMEP excerpts.

#### Components
- `input_adapter.py`
- `weaviate_search.py`
- `generate_answer.py`

#### Process
1. **Structured Input Normalization**
   - Converts nested trademark JSON into deterministic natural-language query.
   - Sorts goods/services for reproducibility.
   - Normalizes missing fields.
   - Prevents embedding noise.
2. **Version-Isolated Semantic Retrieval**
   - E5 query embedding validated against expected dimension.
   - Cosine similarity enforced.
   - Minimum similarity threshold (≥ 0.72).
   - Mandatory doc_version filtering.
   - Deterministic sorting of results.
3. **Strict Grounded LLM Reasoning**
   - Low temperature (0.15) to reduce hallucination.
   - Context truncation to prevent token overflow.
   - Explicit instruction: cite exact TMEP sections only.
   - No risk assignment permitted.
   - No external legal knowledge permitted.

#### Output Format (Enforced)
description:
description>
type: ISSUE:
definition:
tmep_citation:
specific section number>
tmep_based_explanation:
grounded explanation>
---

### Layer 7 — Deterministic Risk Assignment

#### Purpose
Convert grounded legal issue identification into structured risk categories using deterministic logic.

#### Component
- `risk_engine.py`

#### Design Principle
Risk classification is rule-based, not model-based. 
The LLM:
e.g.,
does issue identification and cites sections,
the system then assigns risk categories deterministically.
---

## Risk Category Mapping
Risk levels are determined using prefix-based mapping of TMEP sections.
e.g.,
high, medium-high, medium, medium-low, low thresholds based on prefixes listed below:
| Risk Level | Triggered by prefixes |
| --- | --- |
| HIGH | 1207, 1203, 1210, 1211, 1206, 1204, 1209.01(c) |
| MEDIUM-HIGH | 1209, 1202, 1202.04, 1301, 1302, 1303, 1304, 1212 |
e.g.,
highest specificity prefixes determine the risk level when matched with citation;
brefix match is longest prefix match; if no match defaults to MEDIUM to avoid under-classification;
a longer prefix like `1209.01(c)` matches both `1209` and itself but the longer determines classification;
in case no prefix matches,
risk defaults to MEDIUM as conservative fallback;
detailed rules ensure precise classification based on normalized lowercase IDs and longest prefix matching logic with precomputed sorted rules for efficiency and accuracy;
and regex parsing enforces strict structure for citations with optional § symbol support and spacing tolerance;
system optionally validates that cited sections exist within retrieved data to prevent hallucinations;
risk assignment depends solely on section prefix and retrieval integrity—no probabilistic or subjective factors involved—only retrieved sections may be cited with version isolation enforced and minimum similarity threshold ensuring relevance;
the retrieval integrity layer guarantees all decisions originate from controlled corpus without cross-version contamination or hallucinated authority;
the end-to-end flow involves input normalization -> embedding -> vector retrieval -> similarity filtering -> grounded issue extraction -> regex parsing -> longest-prefix mapping -> report generation;

## Legal Defensibility Properties
This framework ensures:
•   Every issue is grounded in retrieved TMEP text.
•   Risk is assigned deterministically.
•	No hallucinated citations are classified.
•	Version control is enforced.
•	Outputs are reproducible.
•	LLM reasoning is separated from risk categorization.
•	A clear disclaimer is included.
## Conclusion
'the overall framework is retrieval-grounded,
deterministic,
prefix-based,
verson-isolated,
hallucination-resistant,
and legally auditable; it clearly separates Layer 6 (Issue Identification) from Layer 7 (Risk Categorization), with Layer 5 ensuring retrieval integrity.
