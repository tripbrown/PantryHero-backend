import logging
from typing import Any, Dict, List

from services.supabase_client import get_supabase_client

TABLE = "saved_recipes"


def fetch_saved_recipes(user_key: str) -> List[Dict[str, Any]]:
    client = get_supabase_client()
    response = (
        client.table(TABLE)
        .select("recipe,created_at")
        .eq("user_key", user_key)
        .order("created_at", desc=True)
        .execute()
    )
    rows = response.data or []
    recipes: List[Dict[str, Any]] = []
    for row in rows:
        recipe = row.get("recipe")
        if isinstance(recipe, dict):
            recipes.append(recipe)
    return recipes


def save_recipe(user_key: str, recipe: Dict[str, Any]) -> None:
    recipe_id = str(recipe.get("id") or "").strip()
    if not recipe_id:
        raise ValueError("recipe.id is required")
    client = get_supabase_client()
    payload = {
        "user_key": user_key,
        "recipe_id": recipe_id,
        "recipe": recipe,
    }
    client.table(TABLE).upsert(payload, on_conflict="user_key,recipe_id").execute()
    logging.info("saved_recipes upsert key=%s recipe_id=%s", user_key[:8], recipe_id[:8])


def delete_recipe(user_key: str, recipe_id: str) -> None:
    client = get_supabase_client()
    client.table(TABLE).delete().eq("user_key", user_key).eq("recipe_id", recipe_id).execute()
    logging.info("saved_recipes delete key=%s recipe_id=%s", user_key[:8], recipe_id[:8])
