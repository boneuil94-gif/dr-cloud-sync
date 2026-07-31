"""Creative content generation for Marketing proposals.

This module turns existing ContentProposal rows into reviewable copy and creative
specifications while keeping the canonical product identity and PRIMARY media as
the authority.  It performs no external publication and no PrestaShop mutation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from .marketing import BrandKit, CreativeBrief, MarketingRepository, ProposalStatus, now


class CreativeGenerationError(RuntimeError):
    """Raised when a proposal cannot be generated safely."""


@dataclass(frozen=True)
class ProductCreativeFacts:
    product_key: str
    display_name: str
    base_name: str
    variant_name: str
    attributes: Mapping[str, Any]
    reference: str
    ean: str
    primary_media_id: str


@dataclass(frozen=True)
class CopyResult:
    headline: str
    body: str
    cta: str
    hashtags: tuple[str, ...] = ()
    legal_text: str = ""


@dataclass(frozen=True)
class VisualResult:
    format: str
    composition: Mapping[str, Any]


class CreativeContentGeneratorPort(Protocol):
    """Provider-neutral contract for a future AI implementation."""

    def generate_copy(
        self,
        brief: CreativeBrief,
        products: Sequence[ProductCreativeFacts],
        brand_kit: BrandKit,
    ) -> CopyResult: ...

    def generate_visual(
        self,
        brief: CreativeBrief,
        products: Sequence[ProductCreativeFacts],
        brand_kit: BrandKit,
        format: str,
    ) -> VisualResult: ...


class DeterministicCreativeGenerator:
    """Safe baseline generator using canonical facts only.

    It deliberately avoids invented product claims.  A real AI adapter can replace
    this class later without changing CreativeAIService or persistence contracts.
    """

    def generate_copy(self, brief, products, brand_kit):
        names=" · ".join(product.display_name for product in products)
        headline=names if len(products)==1 else f"Sélection DrCloud — {names}"
        body=f"{headline}. Disponible chez DrCloud."
        return CopyResult(headline=headline, body=body, cta=brief.cta or "Disponible chez DrCloud.")

    def generate_visual(self, brief, products, brand_kit, format):
        return VisualResult(format=format, composition={
            "background":"DRCLOUD_BRAND",
            "logo_asset":brand_kit.logo_asset,
            "product_assets":[{"product_key":p.product_key,"media_id":p.primary_media_id,
                               "policy":"PRESERVE_ORIGINAL"} for p in products],
            "packaging_policy":"PRESERVE_ORIGINAL",
            "style":brief.style,
        })


class CreativeAIService:
    """Hydrate a ContentProposal into generated copy + creative variants."""

    GENERATABLE={ProposalStatus.DRAFT.value, ProposalStatus.READY_FOR_REVIEW.value}

    def __init__(self, repository: MarketingRepository, catalogue: Any, media_repository: Any,
                 generator: CreativeContentGeneratorPort | None=None):
        self.repo=repository
        self.catalogue=catalogue
        self.media=media_repository
        self.generator=generator or DeterministicCreativeGenerator()

    @staticmethod
    def _hash(*parts: str) -> str:
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    @staticmethod
    def _brief(raw: str) -> CreativeBrief:
        value=json.loads(raw)
        for key in ("required_assets","formats"):
            if key in value:
                value[key]=tuple(value[key])
        return CreativeBrief(**value)

    @staticmethod
    def _brand_kit(value: Mapping[str,Any]) -> BrandKit:
        value=dict(value)
        for key in ("colors","typography_rules","preferred_ctas","allowed_elements","forbidden_elements","templates"):
            if key in value:
                value[key]=tuple(value[key])
        return BrandKit(**value)

    def _facts(self, proposal_id: str) -> list[ProductCreativeFacts]:
        keys=[row[0] for row in self.repo.db.execute(
            "SELECT product_key FROM marketing_proposal_products WHERE proposal_id=? ORDER BY position",
            (proposal_id,))]
        if not keys:
            raise CreativeGenerationError("Proposal has no products")
        facts=[]
        for key in keys:
            product=self.catalogue.get(key)
            if product is None:
                raise CreativeGenerationError(f"Unknown canonical product: {key}")
            media=self.media.primary(key)
            if media is None:
                raise CreativeGenerationError(f"PRIMARY media missing for {key}")
            display_name=str(getattr(product,"display_name",None) or getattr(product,"name",None) or "").strip()
            if not display_name:
                raise CreativeGenerationError(f"Canonical display name missing for {key}")
            facts.append(ProductCreativeFacts(
                product_key=key,
                display_name=display_name,
                base_name=str(getattr(product,"base_name",None) or getattr(product,"name",None) or ""),
                variant_name=str(getattr(product,"variant_name",None) or ""),
                attributes=dict(getattr(product,"attributes",None) or {}),
                reference=str(getattr(product,"reference",None) or ""),
                ean=str(getattr(product,"ean",None) or ""),
                primary_media_id=str(media.media_id),
            ))
        return facts

    def generate(self, proposal_id: str, actor: str="authenticated") -> dict[str,Any]:
        row=self.repo.db.execute("SELECT * FROM marketing_proposals WHERE proposal_id=?",(proposal_id,)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        if row["status"] not in self.GENERATABLE:
            raise CreativeGenerationError("Only DRAFT or READY_FOR_REVIEW proposals can be generated")

        facts=self._facts(proposal_id)
        brief=self._brief(row["creative_brief_json"])
        if brief.packaging_policy != "PRESERVE_ORIGINAL":
            raise CreativeGenerationError("Unsupported packaging policy")
        brand_kit=self._brand_kit(self.repo.settings()["brand_kit"])
        formats=tuple(json.loads(row["formats_json"])) or brief.formats
        fingerprint=self._hash(
            proposal_id,
            json.dumps([asdict(f) for f in facts],ensure_ascii=False,sort_keys=True),
            json.dumps(asdict(brief),ensure_ascii=False,sort_keys=True),
            json.dumps(asdict(brand_kit),ensure_ascii=False,sort_keys=True),
            json.dumps(formats),
        )

        previous=self.repo.db.execute(
            "SELECT details_json FROM marketing_audit WHERE event_type='CREATIVE_GENERATED' AND entity_id=? ORDER BY rowid DESC LIMIT 1",
            (proposal_id,)).fetchone()
        if previous and json.loads(previous[0]).get("fingerprint")==fingerprint:
            return self.get(proposal_id) | {"idempotent":True}

        copy=self.generator.generate_copy(brief,facts,brand_kit)
        if not copy.headline.strip() or not copy.body.strip() or not copy.cta.strip():
            raise CreativeGenerationError("Generator returned incomplete copy")

        visuals=[]
        for format_name in formats:
            visual=self.generator.generate_visual(brief,facts,brand_kit,str(format_name))
            if visual.format != str(format_name):
                raise CreativeGenerationError("Generator changed requested format")
            if visual.composition.get("packaging_policy") != "PRESERVE_ORIGINAL":
                raise CreativeGenerationError("Generated visual does not preserve packaging")
            visuals.append(visual)

        stamp=now()
        with self.repo.db:
            self.repo.db.execute(
                "UPDATE marketing_proposals SET headline=?,body=?,cta=?,hashtags_json=?,legal_text=?,status=?,updated_at=? WHERE proposal_id=?",
                (copy.headline.strip(),copy.body.strip(),copy.cta.strip(),json.dumps(list(copy.hashtags),ensure_ascii=False),
                 copy.legal_text.strip(),ProposalStatus.READY_FOR_REVIEW.value,stamp,proposal_id))
            self.repo.db.execute("DELETE FROM marketing_assets WHERE proposal_id=? AND source='CREATIVE_AI' AND status='PREVIEW'",(proposal_id,))
            for visual in visuals:
                self.repo.db.execute(
                    "INSERT INTO marketing_assets VALUES(?,?,?,?,?,?,?,?)",
                    (f"creative:{uuid4()}",proposal_id,visual.format,"CREATIVE_AI","PREVIEW",
                     json.dumps(dict(visual.composition),ensure_ascii=False,sort_keys=True),"PRESERVE_ORIGINAL",stamp))
            self.repo.audit("CREATIVE_GENERATED","ContentProposal",proposal_id,actor,{
                "fingerprint":fingerprint,
                "formats":[visual.format for visual in visuals],
                "product_keys":[fact.product_key for fact in facts],
                "packaging_policy":"PRESERVE_ORIGINAL",
            })
        return self.get(proposal_id) | {"idempotent":False}

    def get(self, proposal_id: str) -> dict[str,Any]:
        row=self.repo.db.execute("SELECT * FROM marketing_proposals WHERE proposal_id=?",(proposal_id,)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        proposal=self.repo._decoded(dict(row))
        assets=self.repo.rows("marketing_assets",limit=500)
        proposal["creative_assets"]=[asset for asset in assets if asset["proposal_id"]==proposal_id]
        return proposal
