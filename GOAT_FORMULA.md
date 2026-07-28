# PL Bot — GOAT Formula Documentation

## Repo
`https://github.com/JBRYAN333/pl-bot.git`

## Modified file
`bot_pl.py` — function `compute_goat` (line 1032+)

---

## The Formula

```python
score = (WR × 0.40
        + (Wins ÷ MaxWins) × 0.25
        + (MP ÷ MaxMP) × 0.15
        + titles_won × 0.15
        + titles_defended × 0.10)
```

Score is multiplied by 100 for display (e.g. 0.85 → 85.0).

---

## Evolution of changes

### 1. Original problem
Only **ranked players** (Top 10) were considered. Hawk, Peww and other inactive legends were invisible.

**Fix:** added loop to populate `player_stats` from `records` (fight history) for anyone not in ranking.

### 2. Larry's feedback — round 1
- Remove FOTN from formula
- Increase title weight: 0.10 → **0.15**, defense 0.05 → **0.10**
- Remove cap on titles/defenses
- Min fights: 5 → **10** (waived for champions)
- **Champions always above non-champions** (two-tier sorting)

### 3. Current champion detection
Players with `"Champion"` position in the ranking now start with `titles_won = 1` (solved Preguiça 6-0 being invisible).

### 4. Title detection regex — flexible
Old regex: `r'\bwon\b.+championship'` (rigid, required exact phrasing)

New regex:
```python
_re_title = r'(?:won|for|claim|captur|became).{0,40}(?:championship|title|belt)'
_re_def   = r'\bdefend(?:ed|ing|s)?\b|\bretain(?:ed|ing|s)?\b'
```

This catches "for the NA championship" (Kymora) but NOT "title eliminator" (Motley false positive fix).

---

## Eligibility
- Player must have **10+ fights** OR **at least 1 title** (titles_won > 0)

## Sorting
1. All champions (titles_won > 0) sorted by score descending
2. All non-champions sorted by score descending

---

## Current Issues
- Kymora's annotations in the PDF may still lack championship keywords
- The HCL/PL collab stream is being planned
