from pydantic import BaseModel, Field
from typing import List, Optional


class PantryItemIn(BaseModel):
    name: str
    quantity: Optional[str] = None
    unit: Optional[str] = None


class FiltersIn(BaseModel):
    mealType: str = "Any"
    cuisine: str = "Any"
    time: str = "Any"
    servings: int = Field(2, ge=1, le=8)
    pantryOnly: bool = True
    maxMissingIngredients: int = Field(2, ge=1, le=4)


class GenerateRecipesRequest(BaseModel):
    count: int = Field(2, ge=1, le=3)
    filters: FiltersIn
    dietaryPreference: Optional[str] = None
    avoidIngredients: List[str] = []
    pantryItems: List[PantryItemIn] = []
    preferenceSummary: Optional[dict] = None


class RecipeIngredientOut(BaseModel):
    name: str
    quantity: Optional[str] = None
    unit: Optional[str] = None


class RecipeOut(BaseModel):
    id: str
    title: str
    description: str
    estimatedTimeMinutes: int
    ingredients: List[RecipeIngredientOut]
    missingIngredients: List[RecipeIngredientOut] = []
    steps: List[str]


class GenerateRecipesResponse(BaseModel):
    recipes: List[RecipeOut]
    quota: Optional[dict] = None


class SavedRecipeRequest(BaseModel):
    recipe: dict


class SavedRecipesResponse(BaseModel):
    recipes: List[dict]


class RecipeFeedbackRequest(BaseModel):
    recipe: dict
    feedback: str


class RecipeFeedbackEntryOut(BaseModel):
    recipe: dict
    feedback: str
    updated_at: Optional[str] = None


class RecipeFeedbackResponse(BaseModel):
    entries: List[RecipeFeedbackEntryOut]
