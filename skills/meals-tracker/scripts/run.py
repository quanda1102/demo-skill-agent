#!/usr/bin/env python3
"""Meals Tracker - Personal meals tracker to log daily food intake with calories and nutrition."""

import json
import sys
import os
from datetime import datetime, timedelta

DATA_FILE = "meals_data.json"


def load_data():
    """Load meals data from JSON file."""
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_data(meals):
    """Save meals data to JSON file."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(meals, f, indent=2)


def add_meal(meal_data):
    """Add a new meal to the data store."""
    name = meal_data.get("name", "").strip()
    if not name:
        print("Error: Meal name cannot be empty", file=sys.stderr)
        sys.exit(1)

    calories = meal_data.get("calories", 0)
    if not isinstance(calories, int) or calories < 0:
        print("Error: Calories must be a non-negative integer", file=sys.stderr)
        sys.exit(1)

    date = meal_data.get("date")
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    meal = {
        "name": name,
        "calories": calories,
        "protein": meal_data.get("protein", 0),
        "carbs": meal_data.get("carbs", 0),
        "fat": meal_data.get("fat", 0),
        "type": meal_data.get("type", "snack"),
        "date": date,
        "notes": meal_data.get("notes", "")
    }

    meals = load_data()
    meals.append(meal)
    save_data(meals)
    print(f"added: {name}")


def view_summary(meal_data):
    """View daily summary of meals."""
    date = meal_data.get("date")
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    meals = load_data()
    day_meals = [m for m in meals if m.get("date") == date]

    total_calories = sum(m.get("calories", 0) for m in day_meals)
    total_protein = sum(m.get("protein", 0) for m in day_meals)
    total_carbs = sum(m.get("carbs", 0) for m in day_meals)
    total_fat = sum(m.get("fat", 0) for m in day_meals)

    print(f"Summary for {date}")
    print(f"  Meals: {len(day_meals)}")
    print(f"  Calories: {total_calories}")
    print(f"  Protein: {total_protein}g")
    print(f"  Carbs: {total_carbs}g")
    print(f"  Fat: {total_fat}g")


def view_chart(meal_data):
    """View 7-day calorie chart."""
    meals = load_data()
    today = datetime.now()

    print("7-Day Calorie Chart")
    print("-" * 40)

    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        day_meals = [m for m in meals if m.get("date") == date_str]
        calories = sum(m.get("calories", 0) for m in day_meals)
        bar = "#" * min(calories // 50, 40)
        day_label = day.strftime("%a")
        print(f"{day_label} {date_str}: {calories:4d} {bar}")


def export_data(meal_data):
    """Export all meals to JSON file."""
    meals = load_data()
    export_file = meal_data.get("filename", "meals_data.json")

    with open(export_file, "w", encoding="utf-8") as f:
        json.dump(meals, f, indent=2)

    print(f"exported: {len(meals)} meals to {export_file}")


def main():
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("Usage: cat input.json | python run.py")
        print("Actions: add, summary, chart, export")
        sys.exit(0)

    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    action = input_data.get("action", "")
    if action == "add":
        add_meal(input_data)
    elif action == "summary":
        view_summary(input_data)
    elif action == "chart":
        view_chart(input_data)
    elif action == "export":
        export_data(input_data)
    else:
        print(f"Error: Unknown action '{action}'", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()