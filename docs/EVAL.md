# Accuracy and cost

**Bottom line:** on 50 hand-checked posts, the production tagger (Claude Haiku 5.5) gets the **role right 84%** of the time (95% CI 71–92%) and the **city right 96%** (CI 86–99%). **92%** of the posts the site shows are genuine fresher jobs. Tagging costs **$0.0985 per 1,000 posts**, measured over 1,937 tagged posts.

## How the 50 posts were checked

1. **Sample.** `python -m eval.sample` picked 50 live posts at random (seed 42), stratified by source: 37 Adzuna, 12 company boards, 1 Jooble. The production model's tags for those posts were frozen at that moment in `eval/data/eval50_predictions.csv`.
2. **Draft reference labels.** `python -m eval.reference` had **Claude Opus 5.5** (a larger model, at high effort) label the same 50 posts with the same rules. It never saw the production model's answers.
3. **Hand review.** I went through `eval/data/eval50_review.xlsx` row by row (post title, company, location, description and link next to the draft label) and marked each one correct or not. The production model's answers are not in that sheet, so I wasn't anchored on the system being graded. **Result: 49 rows correct as drafted, 1 row not a real job** (see below).
4. **Score.** `python -m eval.score` compares the production tags with the reviewed labels:
   - **role:** exact match (one of 18 roles).
   - **city:** exact match of the *set* of cities. `["Pune"]` vs `["Pune", "Mumbai"]` counts as wrong; spellings are normalized first (Bangalore → Bengaluru).
   - **precision of what's shown:** of the posts the site shows to freshers, how many really are fresher jobs.
   - A post that isn't a real job has no true role or city, so it's left out of role/city accuracy and counted only as "shouldn't have been shown".
5. **Uncertainty.** With n = 49, one post is about 2 points, so every number has a 95% Wilson interval.

## Results

All rows use the same posts and prompt v2. Role and city are over the 49 real jobs.

| Model | Role | City | Both right | Shown posts that are real fresher jobs | USD / 1,000 posts |
|---|---|---|---|---|---|
| **Claude Haiku 5.5, production tags** | **84%** (CI 71–92%) | **96%** (CI 86–99%) | **82%** | **92%** | **$0.0985** (all production tagging) |
| Claude Haiku 5.5, re-run | 88% | 96% | 84% | | $0.072 |
| Claude Sonnet 5.5, re-run | 90% | 96% | 86% | | $2.02 |

**What this says:**
- **Run-to-run noise is as big as the model gap.** Haiku scored 84% on roles with its live tags and 88–90% when re-run on the same posts with the same prompt. Sonnet's 90% is within that noise and costs **20×** more. For tagging into fixed categories, Haiku is the right choice. Money is better spent labelling more posts, to tighten the interval, than on a bigger model.
- **City is the easy part** (96%): the location field usually says it. **Role is the hard part.**

## What the hand-check caught that no model did

One sampled post, **"demo" by a company called "Test"**, was a placeholder listing on Adzuna with generic filler text. Both models tagged it confidently as a sales job open to freshers. The same company had **7** such posts live. The pipeline now drops placeholder titles and companies, with a regression test that "Test Engineer at Bosch" still gets through. See build log #12.

## Where the tagger goes wrong

From `eval/results/errors.csv` (14 rows):

| Kind | Examples | Comment |
|---|---|---|
| **Genuinely ambiguous role** | *Brand Trainee*: Marketing or Sales? *HiL Test Engineer*: Software or Core Engineering? *Research Analyst, Companies & Transactions*: Finance or Data? | Two careful people would disagree. Clearer role boundaries in the taxonomy help more than a bigger model. |
| **"Other" vs a real category** | *CoOp/Intern*, *Intern, InSight, Wardha*, *Cabochon Maker* | Vague posts; the tagger and the reference disagree on whether to commit to a category. |
| **City when the location field is vague** | *Electrical Engineers* (location `India`, description names Ahmedabad) | The answer was in the description, not the location field. |
| **Shown as fresher-friendly, but isn't** | *Customer Success Executive, Strategic Accounts*; *Specialist Total Rewards*; *Process owner, Milling and Plasma cleaning* | The costly kind: it wastes a student's application. |
| **Not a real job** | *demo* by *Test* | Fixed with a rule (above). |

## Cost per 1,000 posts

Every LLM call's token usage is logged in `llm_calls`. Cost is

```
sum(input_tokens × input price + output_tokens × output price) / posts tagged × 1,000
```

with Claude Haiku 5.5 at $0.10 / 1M input tokens and $0.50 / 1M output tokens. Across all production tagging so far (1,937 posts, $0.19 total), that's **$0.0985 per 1,000 posts**. Per post it's about 376 input and 112 output tokens, so **output is the bigger half of the bill** even though it's fewer tokens. A normal daily run tags 40–70 new posts for under one US cent.

What keeps it low:
- **Dedup before tagging.** Duplicates are never sent.
- **Only new posts are tagged.** Daily cost scales with *new* posts, not the total.
- **Batches of 20.** The ~2k-token system prompt is paid once per 20 posts.
- **Descriptions trimmed** to 1,500 characters.

Hosting is about $0 at this volume (Cloud Run, Cloud Scheduler, Neon and Vercel free tiers).

**Next levers:**
1. The Message Batches API halves token prices, and the daily job doesn't care about latency.
2. Cut output tokens (shorter field names, thinking off).
3. Rules first for unambiguous posts.
