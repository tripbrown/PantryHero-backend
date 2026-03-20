import logging
from typing import Any, Dict, List

from services.supabase_client import get_supabase_client

TABLE = "recipe_feedback"


def fetch_feedback_entries(user_key: str) -> List[Dict[str, Any]]:
    client = get_supabase_client()
    response = (
        client.table(TABLE)
        .select("recipe,feedback,updated_at")
        .eq("user_key", user_key)
        .order("updated_at", desc=True)
        .execute()
    )
    rows = response.data or []
    entries: List[Dict[str, Any]] = []
    for row in rows:
        recipe = row.get("recipe")
        feedback = row.get("feedback")
        if isinstance(recipe, dict) and isinstance(feedback, str):
            entries.append(
                {
                    "recipe": recipe,
                    "feedback": feedback,
                    "updated_at": row.get("updated_at"),
                }
            )
    return entries


def upsert_feedback(user_key: str, recipe: Dict[str, Any], feedback: str) -> None:
    recipe_id = str(recipe.get("id") or "").strip()
    if not recipe_id:
        raise ValueError("recipe.id is required")
    client = get_supabase_client()
    payload = {
        "user_key": user_key,
        "recipe_id": recipe_id,
        "recipe": recipe,
        "feedback": feedback,
    }
    client.table(TABLE).upsert(payload, on_conflict="user_key,recipe_id").execute()
    logging.info("recipe_feedback upsert key=%s recipe_id=%s feedback=%s", user_key[:8], recipe_id[:8], feedback)


def delete_feedback(user_key: str, recipe_id: str) -> None:
    client = get_supabase_client()
    client.table(TABLE).delete().eq("user_key", user_key).eq("recipe_id", recipe_id).execute()
    logging.info("recipe_feedback delete key=%s recipe_id=%s", user_key[:8], recipe_id[:8])
