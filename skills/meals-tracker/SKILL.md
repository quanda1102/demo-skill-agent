---
name: meals-tracker
description: Personal meals tracker to log daily food intake with calories and nutrition. Supports adding meals, viewing daily summaries, generating weekly ASCII charts, and exporting data to JSON.
version: 0.1.0
owner: skill-agent
runtime: python
status: published
domain:
  - meals
  - nutrition
  - health
supported_actions:
  - create
  - read
  - write
forbidden_actions:
  - update
  - delete
  - list
  - move
  - copy
  - rename
  - archive
  - extract
  - count
  - search
  - summarize
  - parse
  - format
  - validate
  - transform
  - convert
  - encode
  - decode
  - sort
  - filter
  - split
  - join
  - hash
  - append
  - fetch
side_effects:
  - file_read
  - file_write
entrypoints:
  - type: skill_md
    path: SKILL.md
  - type: script
    path: scripts/run.py
---

# Meals Tracker

A personal meals tracker to log daily food intake with calories and nutrition data.

## Usage

Read JSON input from stdin to perform one of the following actions:

### Add a Meal

```json
{
  "action": "add",
  "name": "Banhmi",
  "calories": 400,
  "protein": 15,
  "carbs": 50,
  "fat": 12,
  "type": "breakfast",
  "date": "2024-01-15",
  "notes": "Lunch at the cafe"
}
```

- `name` (required): meal name, non-empty string
- `calories` (required): positive integer
- `protein`, `carbs`, `fat` (optional): nutrition values, default 0
- `type` (optional): meal type (breakfast, lunch, dinner, snack), defaults to "snack"
- `date` (optional): ISO date string, defaults to today
- `notes` (optional): free-text notes

### View Daily Summary

```json
{
  "action": "summary",
  "date": "2024-01-15"
}
```

Returns total calories and macro breakdown for the specified date.

### View Weekly Chart

```json
{
  "action": "chart"
}
```

Returns a 7-day ASCII bar chart of daily calorie totals.

### Export Data

```json
{
  "action": "export"
}
```

Exports all meals to `meals_data.json` in the current directory.

## Data Storage

Meals are persisted in `meals_data.json` in the current working directory.

## Error Handling

- Empty meal name: returns error message
- Negative calories: returns error message
- Missing date: defaults to today
- No data: shows empty summary with zero totals