"""Skills API — browse, manage, and query the skills marketplace."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import verify_api_key
from app.skills.registry import skill_registry

router = APIRouter(prefix="/api/skills", tags=["skills"], dependencies=[Depends(verify_api_key)])


@router.get("")
async def list_skills(category: str | None = None, enabled_only: bool = False):
    """List all available skills, optionally filtered by category."""
    if category:
        from app.skills.base import SkillCategory
        try:
            cat = SkillCategory(category)
            skills = skill_registry.get_by_category(cat)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown category: {category}")
    elif enabled_only:
        skills = skill_registry.get_enabled()
    else:
        skills = skill_registry.get_all()

    return {
        "skills": [s.to_dict() for s in skills],
        "total": len(skills),
    }


@router.get("/categories")
async def list_categories():
    """List all skill categories with counts."""
    from app.skills.base import SkillCategory

    all_skills = skill_registry.get_all()
    category_counts = {}
    for cat in SkillCategory:
        count = sum(1 for s in all_skills if s.category == cat)
        if count > 0:
            category_counts[cat.value] = count

    return {"categories": category_counts}


@router.get("/status")
async def skills_status():
    """Get overall skills status for dashboard."""
    return skill_registry.get_status()


@router.get("/{skill_name}")
async def get_skill(skill_name: str):
    """Get details of a specific skill."""
    skill = skill_registry.get(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_name}")

    return skill.to_dict()


@router.post("/{skill_name}/reload")
async def reload_skill(skill_name: str):
    """Hot-reload a skill by re-importing its module."""
    result = await skill_registry.reload_skill(skill_name)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/{skill_name}/tools")
async def get_skill_tools(skill_name: str):
    """Get tool schemas for a specific skill."""
    skill = skill_registry.get(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_name}")

    return {
        "skill": skill_name,
        "tools": skill.get_tools_schema(),
    }
