# PantryHero Scan API

## Setup

```bash
cd backend_api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment

Create a `.env` file in `backend_api` with:

```
OPENAI_API_KEY=your_key_here
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
```

## Run

```bash
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

## Endpoint

`POST /scan` with multipart/form-data field `file`.

Response:

```json
{
  "items": [
    { "name": "Milk", "quantity": "1", "unit": "gallon", "suggestedLocation": "fridge" }
  ]
}
```

`POST /generate_recipes` with JSON body. Example:

```json
{
  "count": 2,
  "filters": {
    "mealType": "Dinner",
    "cuisine": "Italian",
    "time": "<30",
    "servings": 2,
    "pantryOnly": true,
    "maxMissingIngredients": 2
  },
  "dietaryPreference": "Vegetarian",
  "avoidIngredients": ["nuts"],
  "pantryItems": [
    { "name": "Pasta", "quantity": "1", "unit": "box" }
  ]
}
```
