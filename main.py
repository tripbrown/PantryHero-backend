import logging
from fastapi import FastAPI, File, Form, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from models import (
    GenerateRecipesRequest,
    GenerateRecipesResponse,
    SavedRecipeRequest,
    SavedRecipesResponse,
    RecipeFeedbackRequest,
    RecipeFeedbackResponse,
)
from services.openai_service import describe_image_bytes, extract_ingredients_from_image_bytes, generate_recipes
from services.recipe_feedback_store import fetch_feedback_entries, upsert_feedback, delete_feedback
from services.saved_recipes_store import fetch_saved_recipes, save_recipe, delete_recipe
from services.usage_limits import enforce_limits, log_decision, log_rate_event, record_success, DEFAULT_WEEKLY_LIMIT

app = FastAPI(title="PantryHero Scan API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/scan")
async def scan(file: UploadFile = File(...), scan_kind: str = Form("receipt")):
    image_bytes = await file.read()
    scan_kind_safe = scan_kind.lower().strip()
    if scan_kind_safe not in ("receipt", "fridge"):
        scan_kind_safe = "receipt"
    items = extract_ingredients_from_image_bytes(image_bytes, scan_kind=scan_kind_safe)
    return {"items": items}


@app.post("/debug_vision")
async def debug_vision(file: UploadFile = File(...)):
    image_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    try:
        description = describe_image_bytes(image_bytes)
        return {
            "byte_length": len(image_bytes),
            "content_type": content_type,
            "description": description,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": str(exc),
                "byte_length": len(image_bytes),
                "content_type": content_type,
            },
        )


@app.post("/generate_recipes", response_model=GenerateRecipesResponse)
async def generate_recipes_endpoint(request: GenerateRecipesRequest, http_request: Request):
    user_key = get_user_key(http_request)
    logging.info("generate_recipes key=%s", user_key[:12])
    result = enforce_limits(user_key)
    log_rate_event(
        user_key,
        result.debug.get("now", 0),
        result.debug.get("last_attempt", 0),
        result.debug.get("delta", 0),
        result.allowed,
        result.debug.get("retry_after", 0),
    )
    debug_headers = {
        "X-Debug-User-Key": user_key,
        "X-Debug-Delta-Seconds": f"{result.debug.get('delta', 0):.2f}",
        "X-Debug-Last-Attempt": f"{result.debug.get('last_attempt', 0):.2f}",
        "X-Debug-Limit-Reason": result.reason,
    }
    if not result.allowed:
        if result.error.get("error") == "rate_limited":
            retry_after = int(result.error.get("retry_after_seconds", 1))
            log_decision(user_key, False, "rate_limited", 0, retry_after)
            return JSONResponse(
                status_code=429,
                content=result.error,
                headers={"Retry-After": str(retry_after), **debug_headers},
            )
        if result.error.get("error") == "quota_exceeded":
            reset_in = int(result.error.get("reset_in_seconds", 0))
            log_decision(user_key, False, "quota_exceeded", 0, reset_in)
            return JSONResponse(
                status_code=429,
                content=result.error,
                headers={"Retry-After": str(reset_in), **debug_headers},
            )

    count = min(max(request.count, 1), 3)
    filters = request.filters
    items = generate_recipes(
        pantry_items=request.pantryItems,
        filters=filters.model_dump(),
        dietary_preference=request.dietaryPreference,
        avoid_ingredients=request.avoidIngredients,
        preference_summary=request.preferenceSummary,
        count=count,
    )

    quota_after = record_success(user_key)
    remaining = int(quota_after.get("remaining", DEFAULT_WEEKLY_LIMIT))
    reset_in = int(quota_after.get("reset_in_seconds", 0))
    log_decision(user_key, True, "ok", remaining, reset_in)
    response_body = {
        "recipes": items,
        "quota": {
            "limit": int(quota_after.get("limit", DEFAULT_WEEKLY_LIMIT)),
            "remaining": remaining,
            "reset_in_seconds": reset_in,
        },
    }
    return JSONResponse(
        status_code=200,
        content=response_body,
        headers={
            "X-Quota-Limit": str(quota_after.get("limit", DEFAULT_WEEKLY_LIMIT)),
            "X-Quota-Remaining": str(remaining),
            "X-Quota-Reset-Seconds": str(reset_in),
            **debug_headers,
        },
    )


@app.get("/saved_recipes", response_model=SavedRecipesResponse)
async def get_saved_recipes(http_request: Request):
    user_key = get_user_key(http_request)
    recipes = fetch_saved_recipes(user_key)
    return {"recipes": recipes}


@app.post("/saved_recipes")
async def create_saved_recipe(request: SavedRecipeRequest, http_request: Request):
    user_key = get_user_key(http_request)
    save_recipe(user_key, request.recipe)
    return {"ok": True}


@app.delete("/saved_recipes/{recipe_id}")
async def remove_saved_recipe(recipe_id: str, http_request: Request):
    user_key = get_user_key(http_request)
    delete_recipe(user_key, recipe_id)
    return {"ok": True}


@app.get("/recipe_feedback", response_model=RecipeFeedbackResponse)
async def get_recipe_feedback(http_request: Request):
    user_key = get_user_key(http_request)
    entries = fetch_feedback_entries(user_key)
    return {"entries": entries}


@app.post("/recipe_feedback")
async def set_recipe_feedback(request: RecipeFeedbackRequest, http_request: Request):
    user_key = get_user_key(http_request)
    feedback = request.feedback.lower().strip()
    if feedback not in ("liked", "disliked", "none"):
        return JSONResponse(status_code=400, content={"error": "invalid_feedback"})
    if feedback == "none":
        recipe_id = str(request.recipe.get("id") or "").strip()
        if recipe_id:
            delete_feedback(user_key, recipe_id)
        return {"ok": True}
    upsert_feedback(user_key, request.recipe, feedback)
    return {"ok": True}


@app.delete("/recipe_feedback/{recipe_id}")
async def clear_recipe_feedback(recipe_id: str, http_request: Request):
    user_key = get_user_key(http_request)
    delete_feedback(user_key, recipe_id)
    return {"ok": True}


def get_user_key(http_request: Request) -> str:
    device_id = http_request.headers.get("X-Device-Id")
    if device_id:
        return device_id
    if http_request.client and http_request.client.host:
        return http_request.client.host
    return "unknown"
