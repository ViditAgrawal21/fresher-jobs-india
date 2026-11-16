# Accuracy and cost

## How the 50 posts were checked

1. **Sample.** `python -m eval.sample` picks 50 live posts at random (seed 42), stratified by source so each source is represented in proportion.
2. **Label blind.** The labelling sheet contains only the post: title, company, location, description and link. The model's tags are **not** in it, so my labels aren't anchored by its answers. The model's tags at sampling time are frozen in `eval/data/eval50_predictions.csv`.
3. **Score.** `python -m eval.score` compares them:
   - **role:** exact match with my label (one of 18 roles).
   - **city:** exact match of the *set* of cities. `["Pune"]` vs `["Pune", "Mumbai"]` counts as wrong; spelling is normalized first (Bangalore → Bengaluru).
   - **fresher precision:** of the posts shown on the site, how many a fresher can really apply to.
4. **Uncertainty.** With n = 50, one post is 2 points, so each number comes with a 95% Wilson interval.
5. **Comparison.** The same 50 posts are re-tagged with Claude Sonnet 5.5 (`--compare claude-sonnet-5-5`) to see whether a model costing 20x more is worth it.

## Results

_To be filled in after labelling._

| Model | Prompt | Role | City | Both | Fresher precision | USD / 1,000 posts |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 (production) | v1 | | | | | |
| Claude Sonnet 5.5 | v1 | | | | | |

**Errors by type:** see `eval/results/errors.csv`.

## Cost per 1,000 posts

Every LLM call's token usage is logged in `llm_calls`. Cost is

```
sum(input_tokens × input price + output_tokens × output price) / posts tagged × 1,000
```

with Claude Haiku 5.5 at $0.10 / 1M input tokens and $0.50 / 1M output tokens. This is measured, not estimated; the About page shows the live number.

What keeps it low:
- **Dedup before tagging.** Duplicates are never sent.
- **Only new posts are tagged.** A post is tagged once in its life unless its text changes.
- **Batches of 20.** The system prompt (taxonomy + rules + 5 examples) is paid once per 20 posts.
- **Descriptions trimmed** to 1,500 characters. Role, city and experience are almost always in the first part.

Infra is about $0 at this volume (Cloud Run, Cloud Scheduler and Neon free tiers).

**Next lever:** the Message Batches API halves token prices, and the daily job doesn't care about latency.
