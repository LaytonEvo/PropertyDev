# Technology Plan v2 — Lean Build

Replaces the build sequencing in *Technology Architecture* (v1). The v1 design principles still stand.
The changes below follow from the critical review of Business Plan v2, the Competitive Landscape
Analysis and the Technology Architecture v1.

## What changed and why

| v1 | v2 | Reason |
|---|---|---|
| Months 1–3 spent building a PostGIS data store before the first letter | No data store in year 1. Constraint lookups use the free **planning.data.gov.uk** API plus a few local GeoJSON layers; LandInsight stays for the full year | Year-1 volume is 1–2 agreements from 400–600 letters. The data store fails v1's own Principle 5 and delays the business |
| Screening engine is the *first* build | **Decision tools come first**: site economics model, nutrient filter, settlement shortlist | The two checks that decide whether the venture works (nutrient exposure and per-site economics) were never done |
| CRM (System 5) built in months 5–7, after outreach starts | **Built-in CRM (`landpromo crm`) in week 1** | Outreach responses and spend caps need somewhere to live from day one; setup takes 1–2 weeks |
| The economics check sits outside the tech | **Economics is built into screening.** Every appraisal includes an expected-value calculation with refusal and appeal probabilities | The £300k "minimum uplift" rule ignores refusal risk. At that floor the expected profit is about £19k per deal |
| Outreach SQL assumes bulk owner names for private landowners | Owners bought **per title** for shortlisted parcels only. Targets are fields at the settlement edge, not whole titles filtered to 1–5 acres | INSPIRE has no owner names; CCOD/OCOD only cover companies. Edge fields usually sit inside large farm titles |
| GDPR not mentioned | Legitimate-interests assessment, Article 14 privacy notice, ICO registration and a data processing agreement with the LLM provider **before the first letter** | Landowners are private individuals |
| System 4 (document drafting) in months 7–9 | Starts **when the first agreement is signed**, not on a calendar date | Nothing to draft before then; Heads of Terms come from a solicitor's template |
| System 6 (predictive planning) in months 9–12 | **Dropped from the 12-month plan** | About 10 sites of own data. Kept as a "similar decisions" lookup inside screening at most |
| Landowner live dashboard | **Dropped** | Breaks "internal only"; a quarterly update drafted by Claude does the same job |
| Generic "data-led" wedge | Tech serves **speed of decision and local depth** (the wedge), not the claim of being "more advanced than Gleeson" | The tech speeds up the cheap ~£1k part of each deal; the moat is local relationships and fast yes/no |

## Year-1 stack

```
                ┌─────────────────────────────────────────────┐
  site / settlement ──▶│ landpromo (Python)                                    │
                │  economics  – EV model, minimum viable size   │
                │  constraints – planning.data.gov.uk + local    │──▶ Markdown appraisal
                │               GeoJSON (nutrient catchments,   │    (founder reviews)
                │               settlement boundaries)          │
                │  screening  – rules RAG + Claude narrative    │
                │  shortlist  – settlements × nutrient filter   │
                └─────────────────────────────────────────────┘
                       │                       ▲
                       ▼                       │
                 CRM (built-in)          LandInsight (licensed,
                 (stages, spend caps)    titles/ownership/comps)
```

* **Python package and CRM web app** in this repo, callable from the CLI now. It can sit behind a small HTTP endpoint later, so n8n workflows (the group's standard pattern) can call it.
* **Claude API** for the narrative appraisal only. The red/amber/green rating is decided by the rules and economics in code, so it can be audited. Claude explains the rating and flags what to check; it does not decide.
* **Humans in the loop**: every appraisal is a draft for founder review, as in v1.

## Build sequence

| When | Build | Done when |
|---|---|---|
| Weeks 1–2 | **Economics model** (`landpromo economics`, `min-acres`) | Worked residential and industrial examples, with values confirmed by a local agent, and the minimum viable site size known for each target authority |
| Weeks 1–2 | **Nutrient filter + settlement shortlist** (`landpromo shortlist`) | Each candidate settlement tagged as residential tier 1 or industrial only. Go/no-go on the geography |
| Week 1 | **CRM** (`landpromo crm serve`: stages and spend caps below) | Live before any outreach |
| Weeks 2–4 | **Screening v1** (`landpromo screen`): constraints + economics + Claude narrative | First 20 sites screened and checked by hand against CarneySweeney's desktop view |
| Months 2–3 | **Local Plan monitor** for the ~10 target authorities (n8n + Claude extraction of plan stage and call-for-sites; Slack/email alerts) | Local Plan stage and next consultation dates logged for every authority. Also tracks the Hampshire reorganisation timetable |
| Month 3 | **Pool sizing**: count edge-of-settlement parcels per settlement (LandInsight export + local layers) | The 400–600 letter target is checked against the real number of parcels |
| Months 3–5 | **Outreach drafting**: letter drafts from CRM records, founder-signed; GDPR pack in place | First 100 letters out |
| First agreement | **Document drafting** (landowner updates first, then planning statement support) | — |
| Revisit at month 9 | Data store / LandInsight replacement | Only if screening volume exceeds ~50 sites a month or LandInsight gaps are hurting |

## CRM pipeline spec (built: `src/landpromo/crm`)

Stages and spend caps, reconciled with Business Plan §2.2. Pre-commitment spend is capped at £1k in total; Heads of Terms legal work is post-commitment.

| Stage | Cumulative cap | Exit criteria |
|---|---|---|
| Identified | £0 | Screen run; rules rating not RED |
| Outreach | £200 | Landowner responded |
| Engaged | £1,000 | Consultant desktop view and EV ≥ target |
| HoTs | £1,000 + approved legal budget (incl. landowner's legal fees) | HoTs signed; restriction on title registered |
| Agreement signed | £60–75k, drawn in tranches | — |
| Application submitted / Consent / Sold / Killed | — | Kill reason recorded |

Custom fields: site ID, authority, land use, gross acres, rules rating, EV (£), spend to date, nutrient catchment, Local Plan stage, next deadline.
Automations: spend over cap → founder approval; 30 days stalled → kill/continue prompt; stage change → checklist.

## Running cost (year 1)

LandInsight £2.5–4k · Land Registry per-title £0.5–1.5k · Claude API about £0.5–1.5k at this volume (v1's £3–8k is likely an overestimate) · hosting about £0–0.5k · PI insurance £2–4k (a business cost, not a tech cost). **About £6–11k**, against £12.5–36k in v1.

## Open questions for the founders

1. Plan-led (Local Plan allocation) or speculative (tilted-balance applications) as the primary route? The answer changes the monitor's priorities and the economics timeline.
2. Who owns the build hours (James / Joems / Luke)? What is their equity or payment arrangement?
3. The placeholder values in `scenarios/*.toml` need replacing with figures from a local agent before any number from the model is quoted.
