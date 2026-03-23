"""Company API endpoints — manage companies via DB."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("")
async def list_companies():
    """List all companies from DB."""
    from app.services.company_manager import list_companies as _list

    return await _list()


@router.get("/{company_id}")
async def get_company(company_id: str):
    """Get details of a specific company with agents."""
    from app.services.company_manager import get_company_with_agents

    result = await get_company_with_agents(company_id)
    if not result:
        raise HTTPException(status_code=404, detail="Company not found")
    return result
