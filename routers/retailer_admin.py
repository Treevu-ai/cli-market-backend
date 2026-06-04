"""Admin endpoints for retailer application review."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from retailer_onboarding import (
    approve_retailer_application,
    db_get_retailer_application,
    db_list_retailer_applications,
    db_public_application,
    guess_store_id,
    reject_retailer_application,
)
from server_deps import require_admin
from store_credentials import credential_summary, get_default_stores, invalidate_credential_cache

router = APIRouter(prefix="/admin", tags=["admin-retailers"])


class ApproveApplicationBody(BaseModel):
    store_id: str = Field("", description="Override the guessed store_id slug")
    magento_token: str = Field("", description="Magento/Shopify API token")
    storefront_token: str = Field("", description="Storefront access token")
    vtex_app_key: str = Field("", description="VTEX app key (vtexappkey-...)")
    vtex_app_token: str = Field("", description="VTEX app token")
    line: str = Field("supermercados", description="Business line (supermercados|farmacias|electro|hogar|moda|departamentales)")
    review_notes: str = Field("", description="Internal notes recorded on the application")


class RejectApplicationBody(BaseModel):
    review_notes: str = Field("", description="Reason for rejection (stored on the application)")


@router.get("/retailer-applications")
def list_retailer_applications(
    status: str | None = "pending",
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    rows = db_list_retailer_applications(status=status or None)
    return {
        "applications": [db_public_application(r) for r in rows],
        "count": len(rows),
    }


@router.get("/retailer-applications/{app_id}")
def get_retailer_application(
    app_id: str,
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    row = db_get_retailer_application(app_id)
    if not row:
        raise HTTPException(status_code=404, detail="application_not_found")
    public = db_public_application(row)
    public["suggested_store_id"] = guess_store_id(
        row.get("website", ""),
        row.get("platform", ""),
        row.get("country", ""),
    )
    return public


@router.post("/retailer-applications/{app_id}/approve")
def approve_application(
    app_id: str,
    body: ApproveApplicationBody = ApproveApplicationBody(),
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    try:
        result = approve_retailer_application(
            app_id,
            store_id=body.store_id.strip() or None,
            magento_token=body.magento_token.strip(),
            storefront_token=body.storefront_token.strip(),
            vtex_app_key=body.vtex_app_key.strip(),
            vtex_app_token=body.vtex_app_token.strip(),
            line=body.line.strip(),
            review_notes=body.review_notes.strip(),
        )
    except ValueError as e:
        code = str(e)
        if code == "application_not_found":
            raise HTTPException(status_code=404, detail=code) from e
        if code.startswith("application_not_pending"):
            raise HTTPException(status_code=409, detail=code) from e
        if code == "credentials_required_for_platform":
            raise HTTPException(
                status_code=400,
                detail="Magento/Shopify require api_token on apply or token fields in approve body",
            ) from e
        if code == "website_or_credentials_required":
            raise HTTPException(
                status_code=400,
                detail="VTEX public stores need website URL or VTEX app credentials",
            ) from e
        raise HTTPException(status_code=400, detail=code) from e

    invalidate_credential_cache()
    result["active_catalog_size"] = len(get_default_stores())
    return result


@router.post("/retailer-applications/{app_id}/reject")
def reject_application(
    app_id: str,
    body: RejectApplicationBody = RejectApplicationBody(),
    authorization: str | None = Header(None),
):
    require_admin(authorization)
    try:
        return reject_retailer_application(
            app_id,
            review_notes=body.review_notes.strip(),
        )
    except ValueError as e:
        code = str(e)
        if code == "application_not_found":
            raise HTTPException(status_code=404, detail=code) from e
        if code.startswith("application_not_pending"):
            raise HTTPException(status_code=409, detail=code) from e
        raise HTTPException(status_code=400, detail=code) from e


@router.get("/store-credentials")
def list_store_credentials(authorization: str | None = Header(None)):
    require_admin(authorization)
    return {
        "credentials": credential_summary(),
        "active_catalog_size": len(get_default_stores()),
    }
