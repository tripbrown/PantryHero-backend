import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import OpenAI
from fastapi import HTTPException

load_dotenv()

_api_key = os.getenv("OPENAI_API_KEY")
_client = OpenAI(api_key=_api_key) if _api_key else None

STAPLES = {
    "salt",
    "black pepper",
    "pepper",
    "water",
    "olive oil",
    "vegetable oil",
    "canola oil",
}

STEP_INGREDIENT_HINTS = [
    "ginger",
    "green chili",
    "chili",
    "chilli",
    "jalapeno",
    "garlic",
    "butter",
    "cream",
    "milk",
    "yogurt",
    "coconut milk",
    "soy sauce",
    "vinegar",
    "lemon",
    "lime",
    "flour",
    "salt",
]


def _data_url_from_image_bytes(image_bytes: bytes) -> str:
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _split_quantity_unit(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    trimmed = value.strip()
    if not trimmed:
        return None, None
    # Capture leading numeric quantity and optional unit text.
    match = re.match(r"^(\d+(?:[.,]\d+)?)\s*(.*)$", trimmed)
    if match:
        quantity = match.group(1).replace(",", ".")
        unit = match.group(2).strip() or None
        return quantity, _normalize_unit_token(unit)
    return None, None


def _normalize_unit_token(unit: Optional[str]) -> Optional[str]:
    if unit is None:
        return None
    cleaned = unit.strip().lower()
    if not cleaned:
        return None
    if cleaned in ("lbs", "lb"):
        return "lb"
    if cleaned in ("ct", "count"):
        return "count"
    if cleaned in ("ea", "each"):
        return "ea"
    return cleaned


def normalize_name(value: str) -> str:
    trimmed = value.strip().lower()
    trimmed = re.sub(r"[^\w\s]", "", trimmed)
    collapsed = re.sub(r"\s+", " ", trimmed)
    if collapsed.endswith("s") and len(collapsed) > 3:
        return collapsed[:-1]
    return collapsed


def extract_ingredients_from_image_bytes(image_bytes: bytes, scan_kind: str) -> List[Dict[str, Any]]:
    if _client is None:
        raise RuntimeError("OPENAI_API_KEY is not set")

    image_url = _data_url_from_image_bytes(image_bytes)
    system_prompt = (
        "You extract grocery items and quantities from images. "
        "Return ONLY strict JSON with the schema: {\"items\": [{\"name\": string, \"quantity\": string|null, "
        "\"unit\": string|null, \"suggestedLocation\": \"fridge\"|\"freezer\"|\"pantry\"}]}. "
        "No markdown, no commentary, no extra keys."
    )

    if scan_kind == "fridge":
        user_prompt = (
            "Scan type: fridge. Extract ONLY edible food/ingredients visible in the photo. "
            "Ignore non-food objects (utensils, containers, appliances, cleaning products unless clearly edible). "
            "Quantity MUST be numeric only (e.g., \"1.25\"). "
            "If quantity or unit are not explicitly visible, set quantity=\"1\" and unit=null. "
            "If unit tokens are explicitly visible (e.g., \"12 ct\", \"16 oz\", \"1 lb\", \"2 kg\"), "
            "set quantity and unit accordingly and normalize units. "
            "Do NOT guess units if not present. "
            "SuggestedLocation must be one of: \"fridge\", \"freezer\", \"pantry\" (best guess by item type). "
            "Return JSON only."
        )
    else:
        user_prompt = (
            f"Scan type: {scan_kind}. Extract ONLY edible food/ingredients from grocery receipts. "
            "Exclude non-food items like paper goods, soap, detergent, cosmetics, pet supplies, medicine, gift cards, bags, "
            "service fees, and store metadata (subtotal, tax, change, total, dates). "
            "If unclear whether an item is food, exclude it. "
            "Quantity MUST be numeric only (e.g., \"1.25\"). "
            "Unit MUST be set when the receipt explicitly includes a unit token near the quantity/weight. "
            "Do NOT drop explicit units. Do NOT guess units if not present. "
            "Examples: "
            "\"BANANAS 1.25lb\" -> quantity=\"1.25\", unit=\"lb\"; "
            "\"APPLES 0.62 kg\" -> quantity=\"0.62\", unit=\"kg\"; "
            "\"EGGS 12 ct\" -> quantity=\"12\", unit=\"count\"; "
            "If only a number appears with no unit marker: unit=null. "
            "If quantity or unit is missing, set it to null. "
            "If unsure about suggestedLocation, use \"pantry\". "
            "Return JSON only."
        )

    response = _client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
    )

    raw_text = response.choices[0].message.content or ""
    parsed: Any = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(raw_text[start : end + 1])
            except json.JSONDecodeError:
                parsed = None
        if parsed is None:
            snippet = raw_text[:200].replace("\n", " ")
            raise HTTPException(status_code=500, detail=f"Failed to parse model JSON: {snippet}")

    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = parsed.get("items", [])
    else:
        items = []
    normalized: List[Dict[str, Any]] = []
    unit_markers = ["lb", "lbs", "oz", "kg", "g", "ct", "ea", "each", "count", "bunch", "bag", "pkg", "pack"]
    unit_regex = re.compile(r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>lb|lbs|oz|kg|g|ct|ea|each|count)", re.IGNORECASE)
    for item in items:
        if not isinstance(item, dict):
            continue
        name_value = item.get("name") or item.get("item") or ""
        name = str(name_value).strip()
        if not name:
            continue
        quantity = item.get("quantity")
        unit = item.get("unit")
        suggested = str(item.get("suggestedLocation", "pantry")).lower()
        allowed_locations = ("fridge", "pantry", "freezer") if scan_kind == "fridge" else ("fridge", "pantry")
        if suggested not in allowed_locations:
            suggested = "pantry"

        normalized_quantity = None
        normalized_unit = None
        if isinstance(quantity, (int, float)):
            normalized_quantity = str(quantity)
            normalized_unit = None
        elif isinstance(quantity, str):
            normalized_quantity, normalized_unit = _split_quantity_unit(quantity)
        if normalized_quantity is None and isinstance(unit, str) and unit.strip():
            normalized_unit = _normalize_unit_token(unit)
        elif normalized_unit is None and isinstance(unit, str) and unit.strip():
            normalized_unit = _normalize_unit_token(unit)

        # Heuristic: if unit marker appears near a quantity in raw_text, fill unit when missing.
        if normalized_unit is None and isinstance(raw_text, str):
            raw_lower = raw_text.lower()
            if any(marker in raw_lower for marker in unit_markers):
                lines = [line for line in raw_text.splitlines() if name.lower() in line.lower()]
                candidates = lines if lines else [raw_text]
                for candidate in candidates:
                    match = unit_regex.search(candidate)
                    if match:
                        normalized_unit = _normalize_unit_token(match.group("unit"))
                        break

        normalized.append(
            {
                "name": name,
                "quantity": normalized_quantity,
                "unit": normalized_unit,
                "suggestedLocation": suggested,
            }
        )

    if scan_kind == "receipt" and normalized and all(item["unit"] is None for item in normalized):
        logging.warning("WARNING: unit extraction returned all null; check prompt/heuristics")

    return normalized


def describe_image_bytes(image_bytes: bytes) -> str:
    if _client is None:
        raise RuntimeError("OPENAI_API_KEY is not set")

    image_url = _data_url_from_image_bytes(image_bytes)
    response = _client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "Describe what you see in the image in one sentence.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
    )

    return (response.choices[0].message.content or "").strip()


def generate_recipes(
    pantry_items: List[Dict[str, Optional[str]]],
    filters: Dict[str, Any],
    dietary_preference: Optional[str],
    avoid_ingredients: List[str],
    preference_summary: Optional[Dict[str, Any]],
    count: int,
) -> List[Dict[str, Any]]:
    if _client is None:
        raise RuntimeError("OPENAI_API_KEY is not set")

    count = max(1, min(count, 3))
    pantry_only = bool(filters.get("pantryOnly", True))
    max_missing = int(filters.get("maxMissingIngredients", 2))
    servings = int(filters.get("servings", 2))

    logging.info(
        "generate_recipes count=%s pantryOnly=%s maxMissing=%s servings=%s",
        count,
        pantry_only,
        max_missing,
        servings,
    )

    pantry_list = [item.name for item in pantry_items if item.name]
    pantry_text = ", ".join(pantry_list) if pantry_list else "none"
    avoid_text = ", ".join(avoid_ingredients) if avoid_ingredients else "none"
    preference_text = dietary_preference or "none"
    logging.info("generate_recipes pantry_count=%s pantry_only=%s", len(pantry_list), pantry_only)

    staples_text = ", ".join(sorted(STAPLES))
    system_prompt = (
        "You are an expert home cook and recipe developer who specializes in creating flavorful meals from limited pantry ingredients. "
        "Your recipes should be realistic, well-seasoned, and appealing to cook at home while obeying all ingredient constraints provided. "
        "You prioritize strong flavor combinations, good cooking technique, and practical recipes that a home cook could make on a weeknight. "
        "When generating multiple recipes, ensure they feel meaningfully different in flavor profile or cooking method. "
        "Return ONLY strict JSON matching the schema exactly with no markdown, commentary, or extra keys: "
        "{\"recipes\": [{\"id\": string, \"title\": string, \"description\": string, "
        "\"estimatedTimeMinutes\": number, "
        "\"ingredients\": [{\"name\": string, \"quantity\": string|null, \"unit\": string|null}], "
        "\"missingIngredients\": [{\"name\": string, \"quantity\": string|null, \"unit\": string|null}], "
        "\"steps\": [string]}]}"
    )

    preference_block = ""
    if preference_summary:
        liked_ing = ", ".join(preference_summary.get("likedIngredients", [])[:5])
        disliked_ing = ", ".join(preference_summary.get("dislikedIngredients", [])[:5])
        liked_cuisine = ", ".join(preference_summary.get("likedCuisines", [])[:3])
        disliked_cuisine = ", ".join(preference_summary.get("dislikedCuisines", [])[:3])
        preference_block = (
            "User preference signals (soft, do not override constraints): "
            f"Likes ingredients: {liked_ing or 'none'}. "
            f"Dislikes ingredients: {disliked_ing or 'none'}. "
            f"Likes cuisines: {liked_cuisine or 'none'}. "
            f"Dislikes cuisines: {disliked_cuisine or 'none'}. "
        )

    user_prompt = (
        f"Generate {count} high-quality recipes using the following constraints.. "
        f"Meal type: {filters.get('mealType','Any')}. "
        f"Cuisine: {filters.get('cuisine','Any')}. "
        f"Time: {filters.get('time','Any')}. "
        f"Servings: {servings}. "
        f"Pantry only: {pantry_only}. "
        f"Max missing ingredients: {max_missing}. "
        f"Pantry items: {pantry_text}. "
        f"Dietary preference: {preference_text}. "
        f"Avoid ingredients: {avoid_text}. "
        f"{preference_block}"
        "STAPLES (allowed even if not in pantry; do NOT count as missing): "
        f"{staples_text}. NOTHING ELSE is a staple. Butter is NOT a staple. "
        
        "Recipe quality guidelines: "
        "Recipes should be flavorful, intentional, and appealing rather than generic pantry combinations. "
        "Use good cooking techniques such as sautéing, roasting, browning, seasoning, acidity, herbs, spices, sauces, or texture contrast when appropriate. "
        "Prefer recipes that maximize flavor while minimizing extra ingredients. "
        "Titles should be specific and appetizing. "
    
        "Cuisine guidance: "
        "If a cuisine is specified, reflect recognizable flavor profiles, ingredients, or cooking techniques from that cuisine. "
        "If strict authenticity is impossible due to pantry constraints, create a dish inspired by that cuisine's flavor logic. "
    
        "Recipe diversity rules: "
        "When generating multiple recipes, ensure they are meaningfully different in cooking method, format, or flavor profile. "
        "Do not return two very similar recipes. "
    
        "Ingredient rules: "
        "If pantryOnly=true: ONLY pantry items + staples are allowed. "
        "If pantryOnly=false: pantry items + staples PLUS at most maxMissingIngredients other ingredients. "
        "List those extra items in missingIngredients. "
        "Example: maxMissingIngredients=2 means missingIngredients length must be 0-2, never more. "
        "You MUST use pantry items as primary ingredients. "
        "If pantry items are provided, include at least 3 distinct pantry items (or all if fewer than 3). "
        "Do NOT return recipes that use only staples. "
    
        "Ingredient consistency rules: "
        "Every non-staple ingredient mentioned in steps MUST appear in the ingredients list. "
        "If you mention an ingredient in steps, it must be listed in ingredients with quantity and unit if possible. "
    
        "Allowed staples that MAY appear in steps even if not listed in ingredients: "
        f"{staples_text}. "
    
        "Do NOT mention any other ingredients unless included in ingredients (and therefore counted toward missing if not in pantry). "
    
        "Recipe format constraints: "
        "Return 6-12 ingredients max, 6-10 steps max, and description 1-2 sentences. "
    
        "Return JSON only."
    )

    logging.info("generate_recipes preference_summary=%s", preference_summary)
    logging.info("generate_recipes prompt=%s", user_prompt)

    response = _client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
    )

    raw_text = response.choices[0].message.content or ""
    logging.info("generate_recipes raw_response=%s", raw_text)
    parsed: Any = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(raw_text[start : end + 1])
            except json.JSONDecodeError:
                parsed = None
        if parsed is None:
            snippet = raw_text[:200].replace("\n", " ")
            raise HTTPException(status_code=500, detail=f"Failed to parse model JSON: {snippet}")

    recipes = []
    if isinstance(parsed, dict):
        recipes = parsed.get("recipes", [])
    elif isinstance(parsed, list):
        recipes = parsed
    logging.info("generate_recipes parsed=%s", parsed)
    if not recipes:
        logging.warning("generate_recipes parsed no recipes")

    normalized: List[Dict[str, Any]] = []
    pantry_name_set = {normalize_name(name) for name in pantry_list if name}
    staples_set = {normalize_name(name) for name in STAPLES}
    for recipe in recipes[:count]:
        if not isinstance(recipe, dict):
            continue
        title = str(recipe.get("title", "Recipe")).strip() or "Recipe"
        description = str(recipe.get("description", "")).strip()
        estimated_time = int(recipe.get("estimatedTimeMinutes", 30) or 30)
        steps = recipe.get("steps", [])
        if not isinstance(steps, list):
            steps = []
        ingredients = recipe.get("ingredients", [])
        if not isinstance(ingredients, list):
            ingredients = []

        normalized_ingredients = []
        for ing in ingredients:
            if not isinstance(ing, dict):
                continue
            name = str(ing.get("name", "")).strip()
            if not name:
                continue
            quantity = ing.get("quantity")
            unit = ing.get("unit")
            if isinstance(quantity, str):
                quantity, unit_from_quantity = _split_quantity_unit(quantity)
                if unit is None:
                    unit = unit_from_quantity
            normalized_ingredients.append(
                {"name": name, "quantity": quantity, "unit": unit}
            )

        missing_list = []
        kept_ingredients = []
        for ing in normalized_ingredients:
            normalized_name = normalize_name(ing["name"])
            is_staple = normalized_name in staples_set
            is_in_pantry = normalized_name in pantry_name_set
            is_missing = (not is_staple) and (not is_in_pantry)
            if is_missing:
                missing_list.append(ing)
            kept_ingredients.append(ing)

        missing_before = len(missing_list)
        missing_after = missing_before
        if pantry_only:
            kept_ingredients = [
                ing for ing in kept_ingredients
                if normalize_name(ing["name"]) in pantry_name_set
                or normalize_name(ing["name"]) in staples_set
            ]
            missing_list = []
            missing_after = 0
            if kept_ingredients and all(normalize_name(ing["name"]) in staples_set for ing in kept_ingredients):
                logging.warning("pantry_only filtered to staples only; pantry_items may be missing or mismatched")
        else:
            if len(missing_list) > max_missing:
                allowed_missing = set(
                    normalize_name(ing["name"]) for ing in missing_list[:max_missing]
                )
                kept_ingredients = [
                    ing for ing in kept_ingredients
                    if normalize_name(ing["name"]) in pantry_name_set
                    or normalize_name(ing["name"]) in staples_set
                    or normalize_name(ing["name"]) in allowed_missing
                ]
                removed_names = [
                    ing["name"] for ing in missing_list[max_missing:]
                ]
                missing_list = missing_list[:max_missing]
                missing_after = len(missing_list)

                filtered_steps = [
                    step for step in steps
                    if not any(rn.lower() in step.lower() for rn in removed_names)
                ]
                if len(filtered_steps) >= 3:
                    steps = filtered_steps
            else:
                missing_after = len(missing_list)

        # Enforce step/ingredient consistency.
        ingredient_name_set = {normalize_name(ing["name"]) for ing in kept_ingredients}
        step_only_ingredients = []
        for hint in STEP_INGREDIENT_HINTS:
            hint_norm = normalize_name(hint)
            if hint_norm in staples_set:
                continue
            for step in steps:
                if hint_norm and hint_norm in normalize_name(step):
                    if hint_norm not in ingredient_name_set:
                        step_only_ingredients.append(hint_norm)
                    break

        step_only_ingredients = list(dict.fromkeys(step_only_ingredients))
        added_to_ingredients_count = 0
        removed_step_lines_count = 0

        if step_only_ingredients:
            if pantry_only:
                original_steps = steps
                steps = [
                    step for step in steps
                    if not any(hint in normalize_name(step) for hint in step_only_ingredients)
                ]
                removed_step_lines_count = len(original_steps) - len(steps)
            else:
                current_missing = [
                    ing for ing in kept_ingredients
                    if normalize_name(ing["name"]) not in pantry_name_set
                    and normalize_name(ing["name"]) not in staples_set
                ]
                remaining_allowance = max(0, max_missing - len(current_missing))
                to_add = step_only_ingredients[:remaining_allowance]
                for name in to_add:
                    kept_ingredients.append({"name": name, "quantity": None, "unit": None})
                    ingredient_name_set.add(name)
                    added_to_ingredients_count += 1
                leftover = step_only_ingredients[remaining_allowance:]
                if leftover:
                    original_steps = steps
                    steps = [
                        step for step in steps
                        if not any(hint in normalize_name(step) for hint in leftover)
                    ]
                    removed_step_lines_count = len(original_steps) - len(steps)

        logging.info(
            "step_only_found=%s added_to_ingredients=%s removed_steps=%s",
            len(step_only_ingredients),
            added_to_ingredients_count,
            removed_step_lines_count,
        )

        logging.info(
            "recipe_missing before=%s after=%s pantryOnly=%s maxMissing=%s",
            missing_before,
            missing_after,
            pantry_only,
            max_missing,
        )

        normalized.append(
            {
                "id": str(recipe.get("id", "")) or str(os.urandom(8).hex()),
                "title": title,
                "description": description,
                "estimatedTimeMinutes": estimated_time,
                "ingredients": kept_ingredients[:12],
                "missingIngredients": missing_list,
                "steps": steps[:10],
            }
        )

    return normalized
