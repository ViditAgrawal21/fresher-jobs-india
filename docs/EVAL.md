# Accuracy and cost

> **Status:** the numbers below are **provisional**. They're measured against reference labels drafted by Claude Opus 5.5; the hand review of all 50 rows is in progress. Re-run `python -m eval.score --labels eval/data/eval50_review.xlsx --compare claude-sonnet-5-5 claude-haiku-5-5` after the review. The comparison answers are cached, so this is free and doesn't re-roll the models.

## How the 50 posts were checked

1. **Sample.** `python -m eval.sample` picks 50 live posts at random (seed 42), stratified by source so each source is represented in proportion: 37 Adzuna, 12 company boards, 1 Jooble. The production model's tags for those posts are frozen at that moment in `eval/data/eval50_predictions.csv`.
2. **Draft reference labels.** `python -m eval.reference` has **Claude Opus 5.5** (a larger model, run at high effort) label the same 50 posts with the same rules. It never sees what the production model said.
3. **Hand review.** I go through `eval/data/eval50_review.xlsx` row by row: read the post, mark the draft `Y` (correct) or `N`, and fix what's wrong. The production model's answers are not in that sheet, so the review is anchored on Opus at worst, never on the system being graded.
4. **Score.** `python -m eval.score` compares the production tags with the reviewed labels:
   - **role:** exact match (one of 18 roles).
   - **city:** exact match of the *set* of cities. `["Pune"]` vs `["Pune", "Mumbai"]` counts as wrong; spellings are normalized first (Bangalore → Bengaluru).
   - **fresher precision:** of the posts the site shows, how many a fresher can really apply to.
5. **Uncertainty.** With n = 50, one post is 2 points, so every number has a 95% Wilson interval.

**Why a drafted reference instead of labelling from a blank sheet?** It makes a careful pass over 50 posts take ~20 minutes instead of an hour. The risk is anchoring on the draft. That's why the drafting model is a different, stronger one, and why the review sheet hides the production model's answers.

## Results (provisional)

All rows: the same 50 posts and prompt v2.

| Model | Role | City | Both right | Fresher precision | USD / 1,000 posts |
|---|---|---|---|---|---|
| **Claude Haiku 5.5, production tags** | **84%** (CI 72–92%) | **96%** (CI 86–99%) | **82%** | **94%** | **$0.101** (live pipeline average) |
| Claude Haiku 5.5, re-run | 88–90% | 96% | 84–86% | | $0.07–0.10 |
| Claude Sonnet 5.5, re-run | 90% | 96% | 86% | 100% | $2.02 |

**What this says:**
- **Same model, same prompt, different scores.** Haiku scored 84%, 90% and 88% on roles across three runs. A 6-point swing between runs of the *same* model means a few points between models is noise at n = 50.
- **Sonnet is within that noise of Haiku and costs 20× more.** For tagging into fixed categories, Haiku is the right choice. The money is better spent on more labelled posts, to measure accuracy more tightly, than on a bigger model.
- **City is the easy part** (96%): the location field usually says it. **Role is the hard part.**

## Where it goes wrong

From `eval/results/errors.csv` (13 disagreements on 50 posts):

| Kind | Examples | Comment |
|---|---|---|
| **Genuinely ambiguous role** | *Brand Trainee*: Marketing or Sales? *HiL Test Engineer*: Software or Core Engineering? *Research Analyst, Companies & Transactions*: Finance or Data? | Two careful people would disagree. A taxonomy fix (clearer boundaries in the role descriptions) helps more than a bigger model. |
| **"Other" vs a real category** | *CoOp/Intern*, *Intern, InSight, Wardha*, *Cabochon Maker* | Vague posts. The model and the reference disagree on whether to commit to a category. |
| **City when the location field is vague** | *Electrical Engineers* (location `India`, description names Ahmedabad) | The model chose "Other"/"Unspecified" where the description had the answer. |
| **Fresher precision** | *Customer Success Executive, Strategic Accounts*; *Specialist Total Rewards* | Shown as fresher-friendly, but the post wants experience. These are the costly errors, since they waste a student's application. |

## Cost per 1,000 posts

Every LLM call's token usage is logged in `llm_calls`. Cost is

```
sum(input_tokens × input price + output_tokens × output price) / posts tagged × 1,000
```

with Claude Haiku 5.5 at $0.10 / 1M input tokens and $0.50 / 1M output tokens. It's measured, not estimated, and the About page shows the live number. On the first full run (916 posts, prompt v2), input was about 350k tokens and output about 107k, so **output costs more than input** even though it's a third of the tokens.

What keeps it low:
- **Dedup before tagging.** Duplicates are never sent.
- **Only new posts are tagged.** A post is tagged once unless its text changes, so the daily cost scales with *new* posts (a few dozen a day), not the total.
- **Batches of 20.** The ~2k-token system prompt is paid once per 20 posts.
- **Descriptions trimmed** to 1,500 characters.

Infra is about $0 at this volume (Cloud Run, Cloud Scheduler, Neon and Vercel free tiers).

**Next levers:**
1. The Message Batches API halves token prices, and the daily job doesn't care about latency.
2. Cut output tokens (shorter field names, thinking off), since output is the bigger half of the bill.
3. Rules first for unambiguous posts.
