"""Human review workflow for Creative AI proposals.

Approval is explicit and local: this module never schedules or publishes content.
"""
from __future__ import annotations

from typing import Any

from .marketing import MarketingRepository, ProposalStatus, now


class CreativeReviewError(RuntimeError):
    pass


class CreativeReviewService:
    """Review generated copy and PREVIEW assets before any downstream action."""

    def __init__(self, repository: MarketingRepository):
        self.repo = repository

    def detail(self, proposal_id: str) -> dict[str, Any]:
        row = self.repo.db.execute(
            "SELECT * FROM marketing_proposals WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        if not row:
            raise KeyError(proposal_id)
        proposal = self.repo._decoded(dict(row))
        products = [r[0] for r in self.repo.db.execute(
            "SELECT product_key FROM marketing_proposal_products WHERE proposal_id=? ORDER BY position",
            (proposal_id,),
        )]
        assets = [a for a in self.repo.rows("marketing_assets", limit=500)
                  if a["proposal_id"] == proposal_id]
        proposal["product_keys"] = products
        proposal["creative_assets"] = assets
        proposal["reviewable"] = (
            proposal["status"] == ProposalStatus.READY_FOR_REVIEW.value
            and bool(proposal.get("headline"))
            and bool(proposal.get("body"))
            and bool(assets)
            and all(a["status"] == "PREVIEW" and a["packaging_policy"] == "PRESERVE_ORIGINAL" for a in assets)
        )
        return proposal

    def approve(self, proposal_id: str, actor: str = "authenticated") -> dict[str, Any]:
        detail = self.detail(proposal_id)
        if not detail["reviewable"]:
            raise CreativeReviewError("Proposal is not safely reviewable")
        stamp = now()
        with self.repo.db:
            self.repo.db.execute(
                "UPDATE marketing_proposals SET status=?,updated_at=? WHERE proposal_id=?",
                (ProposalStatus.APPROVED.value, stamp, proposal_id),
            )
            self.repo.db.execute(
                "UPDATE marketing_assets SET status='APPROVED' WHERE proposal_id=? AND status='PREVIEW'",
                (proposal_id,),
            )
            self.repo.audit("CREATIVE_APPROVED", "ContentProposal", proposal_id, actor, {
                "creative_ids": [a["creative_id"] for a in detail["creative_assets"]],
                "product_keys": detail["product_keys"],
                "packaging_policy": "PRESERVE_ORIGINAL",
            })
        return self.detail(proposal_id)

    def reject(self, proposal_id: str, reason: str, actor: str = "authenticated") -> dict[str, Any]:
        detail = self.detail(proposal_id)
        if detail["status"] != ProposalStatus.READY_FOR_REVIEW.value:
            raise CreativeReviewError("Only READY_FOR_REVIEW proposals can be rejected")
        reason = reason.strip()
        if not reason:
            raise CreativeReviewError("A rejection reason is required")
        with self.repo.db:
            self.repo.db.execute(
                "UPDATE marketing_proposals SET status=?,updated_at=? WHERE proposal_id=?",
                (ProposalStatus.REJECTED.value, now(), proposal_id),
            )
            self.repo.db.execute(
                "UPDATE marketing_assets SET status='REJECTED' WHERE proposal_id=? AND status='PREVIEW'",
                (proposal_id,),
            )
            self.repo.audit("CREATIVE_REJECTED", "ContentProposal", proposal_id, actor, {"reason": reason})
        return self.detail(proposal_id)
