"""WSGI application for the unified, authenticated DrCloud OS interface."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib, hmac, json, logging, os, secrets, time, uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote
from .connectors import DisabledConnector
from .domain import Product, ProductStatus, PurchaseOrderStatus, SupplierStatus
from .inventory import InventoryError, InventoryRepository, InventoryService
from .os_config import OSSettings, parse_prestashop_state_ids
from .repositories import SQLiteOSRepository
from .roadmap import RoadmapService
from .services import AssignBarcodeService, BarcodeError, StockProjectionService
from .admin_status import AdminStatusService, application_metadata
from .modules import available_pages, render_navigation
from .external_stock import ExternalStockQueryService
from .purchasing import (GoodsReceiptService, PurchaseOrderService,
                         SQLiteGoodsReceiptRepository, SQLitePurchaseOrderRepository,
                         SQLiteSupplierRepository, SupplierService)
from .security import AuthorizationService, CredentialStore, SecurityStore
from .media import (MAX_FILE_SIZE, LocalMediaStorage, MediaError, ProductMediaService,
                    SQLiteProductMediaRepository)
from .domain import MediaVariantKind
from .hydration import ProductHydrationService
from .admin_rehydration import AdminCatalogueRehydration, RehydrationConflict
from .rehydration import packaged_historical_snapshot
from .backup_service import BackupService, configured_backup_dir
from .prestashop import PrestaShopClient, PrestaShopError
from .media_import import PrestaShopIntegrationUnavailable, PrestaShopMediaProvider
from .marketing import MarketingAutopilot, MarketingRepository
from .marketing_intelligence import MarketingIntelligenceService, SocialAnalyticsLiveService
from .marketing_operations import MarketingOperationsService
from .creative_ai import CreativeAIService, CreativeGenerationError
from .creative_review import CreativeReviewError, CreativeReviewService
from .sales import SalesLedger, SocialAnalyticsService
from .sales_ingestion import PrestaShopSalesProvider, SalesSyncService, ShopCaisseAPISalesProvider, ShopCaisseCSVProvider, ShopCaisseSalesProvider
from .shopcaisse import ShopCaisseClient, ShopCaisseError
from .social import MarketingSchedulingService, SocialConnectionService, SocialPublishingService
from .data_hub import BatchAlreadyRunning, DataHub, JobDefinition
from .connector_diagnostics import ConnectorDiagnostic
from .bank import BankLedger, DisabledQontoProvider
from .qonto import (EnvironmentSecretProvider, QontoBankProvider, QontoError,
                    credential_is_valid)
from .sumup import SumUpProvider, SumUpError, SumUpTransactionLedger, PaymentSettlementLedger
from .sumup_migrations import sumup_schema_diagnostic
from .schema_diagnostics import ExpectedSchema, diagnose_schema
from .sqlite_diagnostics import register_runtime, runtime_diagnostics
from .reconciliation import ReconciliationService
from .finance import FinanceProjection
from .settlements import PaymentSettlementService
from .financial_reconciliation import FinancialReconciliationService
from .purchase_cost import PurchaseCostLedger
from .replenishment import ReplenishmentEngine
from .crm import CRMService

ROOT = Path(__file__).parent / "static"
LOG = logging.getLogger("drcloud.os")

# One declaration drives the shared shell. Adding a real module only requires a
# route, a content template and an entry here; pages never duplicate navigation.
PAGES = available_pages()

def _commercial_attributes(value):
    if isinstance(value, dict):
        return value
    result = {}
    for item in value or []:
        if isinstance(item, dict):
            group=item.get("groupe") or item.get("group") or item.get("name")
            label=item.get("nom") or item.get("value") or item.get("label")
            if group and label: result[str(group)]=str(label)
    return result

class InventoryApp:
    def __init__(self, service: InventoryService, report_output: Path | None = None, os_repository=None,
                 roadmap_service: RoadmapService | None = None, settings: OSSettings | None = None):
        self.service=service; self.report_output=report_output; self.settings=settings
        products=[Product(i["drcloud_product_key"],i["prestashop_key"],i.get("product_id"),i.get("combination_id"),i["shopcaisse_item_id"],service._name(i),service._ean(i),None,i.get("stock_prestashop"),i.get("stock_shopcaisse"),str(i.get("reference") or i.get("référence") or ""),base_name=str(i.get("base_name") or service._name(i)),variant_name=str(i.get("variant_name") or i.get("variation_name") or i.get("declinaison") or ""),attributes=_commercial_attributes(i.get("attributes") or i.get("attributs")),name_source=str(i.get("name_source") or "PRESTASHOP"),variant_source="PRESTASHOP" if i.get("combination_id") and (i.get("attributes") or i.get("attributs") or i.get("variant_name")) else "",reference_source="PRESTASHOP" if i.get("reference") else "",ean_source=str(i.get("ean_source") or ("PRESTASHOP" if service._ean(i) else ""))) for i in service.items]
        self.os_repository=os_repository or SQLiteOSRepository(service.repo.path,products)
        self.barcodes=AssignBarcodeService(self.os_repository,self.os_repository,DisabledConnector(),DisabledConnector())
        self.roadmap_service=roadmap_service or RoadmapService(); self.failures={}
        data_dir = settings.data_dir if settings else service.repo.path.parent
        self.backup_service = BackupService(configured_backup_dir(data_dir))
        # Startup only prepares/tests infrastructure; it never mutates business data.
        self.backup_service.health(create=True)
        self.admin_status=AdminStatusService(service.repo.path, backup_service=self.backup_service)
        self.stock=StockProjectionService(service.repo,self.os_repository)
        self.external_stock=ExternalStockQueryService(service.repo.path,self.os_repository.all(),service.repo)
        self.suppliers=SupplierService(SQLiteSupplierRepository(service.repo.path),self.os_repository)
        self.purchase_orders=PurchaseOrderService(SQLitePurchaseOrderRepository(service.repo.path),self.suppliers,self.os_repository,self.os_repository)
        self.purchase_costs=PurchaseCostLedger(service.repo.path,self.os_repository)
        self.replenishment=ReplenishmentEngine(service.repo.path,self.os_repository)
        self.goods_receipts=GoodsReceiptService(SQLiteGoodsReceiptRepository(service.repo.path,service.repo.db),self.purchase_orders,service.repo,self.os_repository,self.purchase_costs)
        self.security=SecurityStore(service.repo.path,settings.admin_username,settings.admin_password) if settings else None
        self.credentials=CredentialStore(service.repo.path,settings.admin_password) if settings else None
        self.authorization=AuthorizationService(self.security) if self.security else None
        self.password_failures={}
        media_root=data_dir/"media"/"products"
        self.media=ProductMediaService(SQLiteProductMediaRepository(service.repo.path),LocalMediaStorage(media_root),self.os_repository,self.os_repository)
        self.marketing_repository=MarketingRepository(service.repo.path)
        self.sales=SalesLedger(service.repo.path,self.os_repository,cost_ledger=self.purchase_costs)
        self.crm=CRMService(self.sales.db)
        self.data_hub=DataHub(service.repo.path)
        self.bank=BankLedger(service.repo.path)
        self.sumup_transactions=SumUpTransactionLedger(self.bank.db);self.sumup_settlements=PaymentSettlementLedger(self.bank.db)
        self.settlements=PaymentSettlementService(self.bank.db,
            priority_window_seconds=int(os.environ.get("SETTLEMENT_PRIORITY_WINDOW_SECONDS","120")),
            window_seconds=int(os.environ.get("SETTLEMENT_MATCH_WINDOW_SECONDS","600")),
            rounding_tolerance=os.environ.get("SETTLEMENT_ROUNDING_TOLERANCE","0.01"),
            transit_window_days=int(os.environ.get("SUMUP_TRANSIT_WINDOW_DAYS","14")))
        self.financial_reconciliation=FinancialReconciliationService(self.bank.db)
        register_runtime(self.bank.db, "web")
        secrets_provider=EnvironmentSecretProvider(os.environ,{
            "qonto.production": "QONTO_CREDENTIAL",
            "prestashop.production": "PRESTASHOP_API_KEY",
            "shopcaisse.production": "SHOPCAISSE_API_KEY",
            "sumup.production": "SUMUP_API_KEY",
        })
        explicit_ref=os.environ.get("QONTO_SECRET_REF") or os.environ.get("QONTO_CREDENTIAL_REF")
        secret_ref=explicit_ref if explicit_ref is not None else "env:QONTO_CREDENTIAL"
        selected_reference_present=bool(secret_ref and secret_ref.strip())
        environment_key_present=bool(secret_ref.startswith("env:") and secret_ref.removeprefix("env:") in os.environ) if selected_reference_present else False
        resolved=secrets_provider.resolve(secret_ref) if selected_reference_present else None
        self.qonto_configuration={"selected_reference_present":"OUI" if selected_reference_present else "NON",
            "environment_key_present":"OUI" if environment_key_present else "NON",
            "credential_resolved":"OUI" if resolved is not None else "NON",
            "format_structurally_valid":"OUI" if credential_is_valid(resolved) else "NON",
            "authorization_sent":"NON","authentication_method":"API key organisation",
            "basic_base64_bearer":"NON","api_environment":"Production",
            "health_attempted":"NON","health_status":"NOT_RUN",
            "reference_selection":"EXPLICIT" if explicit_ref is not None else "DEFAULT"}
        candidate=QontoBankProvider(secret_ref,secrets_provider,timeout=float(os.environ.get("QONTO_TIMEOUT_SECONDS","8")),base_url=os.environ.get("QONTO_API_URL") or None)
        qonto_connected=False; qonto_configured=bool(resolved)
        self.bank_provider=candidate if qonto_configured else DisabledQontoProvider()
        sumup_ref=os.environ.get("SUMUP_SECRET_REF") or ("sumup.production" if os.environ.get("SUMUP_API_KEY") else "")
        self.sumup_provider=SumUpProvider(os.environ.get("SUMUP_MERCHANT_CODE"),sumup_ref,secrets_provider,base_url=os.environ.get("SUMUP_API_URL","https://api.sumup.com"),timeout=float(os.environ.get("SUMUP_TIMEOUT_SECONDS","8")))
        sumup_connected=False
        if self.sumup_provider.configured:
            try: sumup_connected=self.sumup_provider.health()["status"]=="CONNECTED"
            except SumUpError: pass
        self.reconciliation=ReconciliationService(service.repo.path)
        self.finance=FinanceProjection(self.bank,self.sales,self.purchase_orders,self.purchase_costs,self.sumup_transactions,self.sumup_settlements)
        sales_providers={}; shopcaisse_connected=False; shopcaisse_client=None
        shopcaisse_key=(secrets_provider.resolve("shopcaisse.production") or "").strip()
        if shopcaisse_key:
            try:
                shopcaisse_client=ShopCaisseClient(shopcaisse_key,
                    api_url=os.environ.get("SHOPCAISSE_API_URL","https://api.shop-caisse.com/v1"),
                    timeout=float(os.environ.get("SHOPCAISSE_TIMEOUT_SECONDS","8")))
            except ShopCaisseError:
                shopcaisse_client=None
        try: paid_states=parse_prestashop_state_ids(os.environ.get("PRESTASHOP_PAID_STATE_IDS"),required=True)
        except ValueError: paid_states=()
        self.prestashop_paid_states_configured=bool(paid_states)
        state_ids=lambda name: [x.strip() for x in os.environ.get(name,"").split(",") if x.strip()]
        prestashop_ref=os.environ.get("PRESTASHOP_SECRET_REF") or ("prestashop.production" if os.environ.get("PRESTASHOP_API_KEY") else "")
        prestashop_client=None; prestashop_connected=False
        if os.environ.get("PRESTASHOP_API_URL") and prestashop_ref:
            try:
                prestashop_client=PrestaShopClient.from_secret_ref(os.environ["PRESTASHOP_API_URL"],prestashop_ref,secrets_provider)
            except (PrestaShopError,ValueError): pass
        if prestashop_client and paid_states:
            sales_providers["PRESTASHOP"]=PrestaShopSalesProvider(prestashop_client,paid_states,
                cancelled_state_ids=state_ids("PRESTASHOP_CANCELLED_STATE_IDS"),
                refunded_state_ids=state_ids("PRESTASHOP_REFUNDED_STATE_IDS"),
                partially_refunded_state_ids=state_ids("PRESTASHOP_PARTIALLY_REFUNDED_STATE_IDS"))
        configured_ps="PRESTASHOP" in sales_providers
        intervals={"sales":int(os.environ.get("DATA_HUB_SALES_INTERVAL_SECONDS","900")),"bank":int(os.environ.get("QONTO_SYNC_INTERVAL_SECONDS",os.environ.get("DATA_HUB_BANK_INTERVAL_SECONDS","10800"))),"projection":int(os.environ.get("DATA_HUB_PROJECTION_INTERVAL_SECONDS","900"))}
        self.data_hub.register_source("shopcaisse_sales","SHOPCAISSE_SALES","ShopCaisse",
            status="UNAVAILABLE" if shopcaisse_key else "NOT_CONFIGURED",
            capabilities=("READ_SALES","READ_PAYMENTS","READ_STOCK"),stale_after_seconds=intervals["sales"]*2)
        if shopcaisse_client:
            _, health_diagnostic=self.data_hub.connector_health_check("shopcaisse_sales","ShopCaisse",
                shopcaisse_client.health,operation="authentication",stage="startup_health",endpoint_path="/authentication")
            shopcaisse_connected=health_diagnostic.success
            if shopcaisse_connected:
                sales_providers["SHOPCAISSE"]=ShopCaisseAPISalesProvider(shopcaisse_client,self.sales.db,
                    stock_board_type=os.environ.get("SHOPCAISSE_STOCK_BOARD_TYPE","DEFAULT"))
        shopcaisse_inbox=os.environ.get("SHOPCAISSE_SALES_INBOX","")
        # Existing offline policy: a configured CSV inbox is runnable only when no
        # API credential asks us to enforce the authenticated startup health gate.
        if not shopcaisse_key and shopcaisse_inbox and Path(shopcaisse_inbox).is_dir():
            sales_providers["SHOPCAISSE"]=ShopCaisseSalesProvider(Path(shopcaisse_inbox))
            self.data_hub.register_source("shopcaisse_sales","SHOPCAISSE_SALES","ShopCaisse",
                configured=True,capabilities=("READ_SALES",),stale_after_seconds=intervals["sales"]*2)
        self.sales_sync=SalesSyncService(self.sales,sales_providers)
        for args in (("prestashop_sales","PRESTASHOP_SALES","PrestaShop",configured_ps),("prestashop_catalog","PRESTASHOP_CATALOG","PrestaShop",prestashop_connected),("purchases","PURCHASES","LOCAL",True),("stock","STOCK","LOCAL",True)):
            # An authenticated Qonto health check makes the source runnable, but it
            # remains unavailable (never CONNECTED/FRESH) until BANK has imported a
            # real page and DataHub.run records the successful synchronization.
            initial_status="UNAVAILABLE" if args[0]=="bank" and args[3] else None
            self.data_hub.register_source(*args[:3],configured=args[3],status=initial_status,capabilities=("READ",),stale_after_seconds=intervals["bank"]*2 if args[0]=="bank" else intervals["sales"]*2)
        self.data_hub.register_source("bank","BANK","Qonto",configured=qonto_configured,
            status="UNAVAILABLE" if qonto_configured else "NOT_CONFIGURED",capabilities=("READ",),stale_after_seconds=intervals["bank"]*2)
        if qonto_configured:
            self.qonto_configuration.update({"health_attempted":"OUI","authorization_sent":"OUI" if credential_is_valid(resolved) else "NON"})
            _, diagnostic=self.data_hub.connector_health_check("bank","Qonto",candidate.health,
                operation="QONTO_HEALTH",stage="organization",endpoint_path="/v2/organization")
            qonto_connected=diagnostic.success
            self.qonto_configuration["health_status"]="CONNECTED" if qonto_connected else "ERROR"
        else:
            cause="SECRET_REFERENCE_UNRESOLVED" if selected_reference_present else "CONFIGURATION_ABSENT"
            self.data_hub.diagnostics.add(ConnectorDiagnostic("bank","Qonto","QONTO_HEALTH","secret_resolution",
                "CONFIGURATION",f"Configuration Qonto absente du runtime. {cause}",datetime.now(timezone.utc).isoformat(),
                response_excerpt=json.dumps(self.qonto_configuration,ensure_ascii=False)))
        self.settlements.qonto_configured=qonto_connected
        # Finance must reflect the live authenticated capability, not merely
        # stale bank rows left in SQLite by an older valid configuration.
        self.finance.qonto_configured=qonto_connected
        sumup_interval=int(os.environ.get("SUMUP_SYNC_INTERVAL_SECONDS","900"))
        for source_id,source_type in (("sumup_merchant","SUMUP_MERCHANT"),("sumup_transactions","SUMUP_TRANSACTIONS"),("sumup_payouts","SUMUP_PAYOUTS"),("sumup_fees","SUMUP_FEES"),("sumup_refunds","SUMUP_REFUNDS"),("sumup_chargebacks","SUMUP_CHARGEBACKS"),("sumup_readers","SUMUP_READERS")):
            self.data_hub.register_source(source_id,source_type,"SumUp",configured=sumup_connected,capabilities=("READ_ONLY",),stale_after_seconds=sumup_interval*2)
        if prestashop_client:
            self.data_hub.register_source("prestashop_catalog","PRESTASHOP_CATALOG","PrestaShop",status="UNAVAILABLE",capabilities=("READ",),stale_after_seconds=intervals["sales"]*2)
            if configured_ps:self.data_hub.register_source("prestashop_sales","PRESTASHOP_SALES","PrestaShop",status="UNAVAILABLE",capabilities=("READ",),stale_after_seconds=intervals["sales"]*2)
        # Audited V1 inventory: these adapters are not fabricated.  They remain
        # visible and non-green until a homologated provider and opaque secret_ref
        # are supplied to the server runtime.
        self.data_hub.register_source("supplier_documents","SUPPLIER_DOCUMENTS","NONE",status="NOT_CONFIGURED",capabilities=("READ_STRUCTURED","PREVIEW_UNSTRUCTURED"),stale_after_seconds=int(os.environ.get("SUPPLIER_SYNC_INTERVAL_SECONDS","3600")))
        for channel in ("instagram","facebook","snapchat","tiktok"):
            self.data_hub.register_source(f"social_{channel}","SOCIAL_ANALYTICS",channel.title(),status="NOT_CONFIGURED",capabilities=("READ_ANALYTICS",),stale_after_seconds=int(os.environ.get("SOCIAL_ANALYTICS_INTERVAL_SECONDS","21600")))
            self.data_hub.register_job(JobDefinition(f"sync_social_analytics_{channel}",f"social_{channel}","SOCIAL_ANALYTICS",int(os.environ.get("SOCIAL_ANALYTICS_INTERVAL_SECONDS","21600"))))
        self.data_hub.register_source("marketing_intelligence","MARKETING_INTELLIGENCE","LOCAL",configured=True,capabilities=("PROPOSE","MEASURE"),stale_after_seconds=86400)
        for job in (JobDefinition("sync_shopcaisse_sales","shopcaisse_sales","SHOPCAISSE_SALES",int(os.environ.get("SHOPCAISSE_SYNC_INTERVAL_SECONDS","600"))),JobDefinition("sync_prestashop_sales","prestashop_sales","PRESTASHOP_SALES",intervals["sales"]),JobDefinition("sync_prestashop_catalog","prestashop_catalog","PRESTASHOP_CATALOG",intervals["sales"]),JobDefinition("sync_bank_transactions","bank","BANK",intervals["bank"]),JobDefinition("refresh_sales_metrics","prestashop_sales","SALES_METRICS",intervals["projection"],("sync_prestashop_sales",)),JobDefinition("refresh_crm","prestashop_sales","CRM_REFRESH",intervals["projection"]),JobDefinition("reconcile_bank_sales","bank","RECONCILE",intervals["projection"],("sync_bank_transactions",)),JobDefinition("refresh_finance","bank","FINANCE",intervals["projection"],("reconcile_bank_sales",)),JobDefinition("refresh_dashboard","purchases","DASHBOARD",intervals["projection"]),JobDefinition("refresh_marketing_signals","prestashop_sales","MARKETING",intervals["projection"],("refresh_sales_metrics",))): self.data_hub.register_job(job)
        self.data_hub.register_job(JobDefinition("sync_sumup_transactions","sumup_transactions","SUMUP_TRANSACTIONS",sumup_interval))
        self.data_hub.register_job(JobDefinition("sync_sumup_payouts","sumup_payouts","SUMUP_PAYOUTS",sumup_interval,("sync_sumup_transactions",)))
        self.data_hub.register_job(JobDefinition("sync_payment_settlements","sumup_transactions","PAYMENT_SETTLEMENTS",sumup_interval,("sync_shopcaisse_sales","sync_sumup_transactions")))
        self.prestashop_client=prestashop_client
        self.shopcaisse_client=shopcaisse_client
        self.sales_import_preview=None
        self.social_analytics=SocialAnalyticsService(self.marketing_repository.db)
        self.marketing=MarketingAutopilot(self.marketing_repository,self.os_repository,self.media.repository,sales=self.sales)
        self.social_live=SocialAnalyticsLiveService(self.marketing_repository.db)
        self.marketing_intelligence=MarketingIntelligenceService(self.marketing_repository,self.os_repository,self.stock,self.sales,self.purchase_costs)
        self.marketing_operations=MarketingOperationsService(self.marketing_repository,self.social_live,self.marketing_intelligence)
        for job in (JobDefinition("generate_stock_marketing_proposals","marketing_intelligence","STOCK_MARKETING",86400,("refresh_dashboard",)),JobDefinition("generate_margin_marketing_proposals","marketing_intelligence","MARGIN_MARKETING",86400,("refresh_finance",)),JobDefinition("generate_marketing_recommendations","marketing_intelligence","STOCK_MARKETING",86400,("refresh_dashboard",)),JobDefinition("prepare_editorial_calendar","marketing_intelligence","MARKETING_CALENDAR",86400),JobDefinition("expire_old_proposals","marketing_intelligence","MARKETING_EXPIRE",86400),JobDefinition("measure_campaigns","marketing_intelligence","MEASURE_MARKETING",21600),JobDefinition("refresh_marketing_cockpit","marketing_intelligence","MARKETING_COCKPIT",3600),JobDefinition("notify_pending_reviews","marketing_intelligence","MARKETING_NOTIFY",3600),JobDefinition("measure_marketing_outcomes","marketing_intelligence","MEASURE_MARKETING",21600),JobDefinition("refresh_learning_loop","marketing_intelligence","LEARNING_LOOP",21600,("measure_marketing_outcomes",))): self.data_hub.register_job(job)
        self.creative_ai=CreativeAIService(self.marketing_repository,self.os_repository,self.media.repository)
        self.creative_review=CreativeReviewService(self.marketing_repository)
        self.social_connections=SocialConnectionService(self.marketing_repository)
        self.marketing_scheduling=MarketingSchedulingService(self.marketing_repository)
        self.social_publishing=SocialPublishingService(self.marketing_repository)
        # Optional external integration: configuration and client are resolved only
        # when an authenticated operator explicitly requests PREVIEW/APPLY.
        self.media_import=PrestaShopMediaProvider(service.repo.path,self.media,self.os_repository,
                                                   backup_service=self.backup_service)
        self.media_import_preview=None
        self.hydration=ProductHydrationService(self.os_repository)
        self.admin_status.media_diagnostics=self.media.diagnostics
        self.admin_status.prestashop_diagnostics=self.media_import.status
        self.catalogue_rehydration = AdminCatalogueRehydration(
            service.repo.path,
            Path(os.environ["DRCLOUD_REHYDRATION_SNAPSHOT"]) if os.environ.get("DRCLOUD_REHYDRATION_SNAPSHOT") else packaged_historical_snapshot(),
            self.backup_service.root,
            environment=settings.environment if settings else "development",
            safe_mode=settings.safe_mode if settings else True)

    def global_schema_diagnostic(self):
        from .sumup import SCHEMA as SUMUP_SCHEMA
        from .bank import SCHEMA as BANK_SCHEMA
        from .sales_ingestion import OPERATIONAL_SCHEMA
        from .settlements import SCHEMA as SETTLEMENT_SCHEMA
        from .data_hub import SCHEMA as DATA_HUB_SCHEMA
        return diagnose_schema(self.bank.db, (
            ExpectedSchema("sumup", SUMUP_SCHEMA),
            ExpectedSchema("bank", BANK_SCHEMA),
            ExpectedSchema("sales", OPERATIONAL_SCHEMA),
            ExpectedSchema("settlements", SETTLEMENT_SCHEMA),
            ExpectedSchema("data_hub", DATA_HUB_SCHEMA),
        ), scope="production-sensitive")

    def automation_operations(self):
        def sales(source):
            report=self.sales_sync.sync(source,actor="automation")
            state=self.sales.db.execute("SELECT cursor FROM sales_sync_states WHERE source=?",(source,)).fetchone()
            return {**report,"rows_imported":report["imported"],"cursor":state[0] if state else None}
        def catalog(cursor):
            if not self.prestashop_client: raise PrestaShopError("PrestaShop credential is not configured")
            products=sum(1 for _ in self.prestashop_client.iter_resource("products")); combinations=sum(1 for _ in self.prestashop_client.iter_resource("combinations"))
            return {"rows_imported":products+combinations,"products_read":products,"combinations_read":combinations}
        def bank(cursor):
            report=self.bank.sync("Qonto",self.bank_provider,cursor)
            if report.get("qonto_diagnostic"):
                self.qonto_configuration["transaction_sync"]=report["qonto_diagnostic"]
            # Bank Ledger is durable before settlement recomputation; Qonto remains
            # strictly read-only while SumUp payouts can immediately find credits.
            report["settlements"]=self.settlements.recompute()
            return report
        intelligence=lambda cursor:{"rows_imported":self.marketing_intelligence.generate()["generated"]}
        internal=lambda payload:{"rows_imported":0,**payload}
        return {"SHOPCAISSE_SALES":lambda cursor:sales("SHOPCAISSE"),"PRESTASHOP_SALES":lambda cursor:sales("PRESTASHOP"),"PRESTASHOP_CATALOG":catalog,"BANK":bank,"SUMUP_TRANSACTIONS":lambda cursor:self.sumup_transactions.sync(self.sumup_provider,cursor),"SUMUP_PAYOUTS":lambda cursor:{**self.sumup_settlements.sync(self.sumup_provider,cursor),"settlements":self.sumup_settlements.reconcile()},"PAYMENT_SETTLEMENTS":lambda cursor:{"rows_imported":self.settlements.recompute()["analysed"],"summary":self.settlements.summary()},"RECONCILE":lambda cursor:{"rows_imported":self.reconciliation.reconcile_sales_bank()["created"]},"FINANCE":lambda cursor:{"rows_imported":0,"projection":self.finance.snapshot()},"DASHBOARD":lambda cursor:{"rows_imported":0},"SALES_METRICS":lambda cursor:{"rows_imported":0,"metrics":self.sales.analytics()},"CRM_REFRESH":lambda cursor:{"rows_imported":self.crm.refresh_metrics()["customers_calculated"],"evidence":self.crm.cockpit()},"MARKETING":lambda cursor:{"rows_imported":0},"MARKETING_CALENDAR":lambda cursor:internal({"calendar":self.marketing_operations.calendar({})}),"MARKETING_EXPIRE":lambda cursor:{"rows_imported":self.marketing.expire()},"MARKETING_COCKPIT":lambda cursor:internal({"cockpit":self.marketing_operations.cockpit()}),"MARKETING_NOTIFY":lambda cursor:internal({"notifications":self.marketing_operations.cockpit()["notifications"]}),"STOCK_MARKETING":intelligence,"MARGIN_MARKETING":intelligence,"MEASURE_MARKETING":lambda cursor:{"rows_imported":0,"coverage":self.marketing_intelligence.learning()["coverage"]},"LEARNING_LOOP":lambda cursor:{"rows_imported":0,"learning":self.marketing_intelligence.learning()}}

    def __call__(self, env, start):
        request_id=env.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4()); path=env.get("PATH_INFO", "/"); method=env.get("REQUEST_METHOD", "GET")
        try:
            if path == "/health":
                try: self.service.repo.db.execute("SELECT 1"); database="ok"; status="ok"
                except Exception: database="error"; status="degraded"
                return self._json(start,{"status":status,"application":"drcloud-os",**application_metadata(),"database":database}, headers=[("X-Request-ID",request_id)])
            public_assets = {
                "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
                "/service-worker.js": ("service-worker.js", "text/javascript; charset=utf-8"),
                "/pwa.js": ("pwa.js", "text/javascript; charset=utf-8"),
                "/icon.svg": ("icon.svg", "image/svg+xml"),
                "/drcloud-logo.png": ("drcloud-logo.png", "image/png"),
                "/inventory.css": ("inventory.css", "text/css; charset=utf-8"),
                **{f"/{name}": (name, "text/javascript; charset=utf-8") for name in (
                    "app-shell.js", "inventory.js", "roadmap.js", "dashboard.js",
                    "administration.js", "stock.js", "purchasing.js", "security.js", "marketing.js", "marketing-operations.js", "sales.js", "finance.js", "settlements.js", "settlement-explorer.js", "crm.js",
                )},
            }
            if path in public_assets:
                file, kind = public_assets[path]
                headers = [("X-Request-ID", request_id)]
                if path == "/service-worker.js":
                    headers.append(("Service-Worker-Allowed", "/"))
                return self._send(start, (ROOT / file).read_bytes(), kind, headers=headers)
            session=self._session(env)
            if path == "/login": return self._login(env,start,method,session,request_id)
            if not session: return self._redirect(start,"/login",request_id)
            if path.startswith("/media/") and method in {"GET","HEAD"}:
                return self._serve_media(path,start,request_id,head=method=="HEAD")
            if path == "/logout" and method == "POST":
                self._csrf(env,session)
                if self.security:
                    self.security.revoke_sessions(session["uid"],session.get("sid")); self.security.db.commit()
                    self.security.audit(session["uid"],"LOGOUT","SESSION",session.get("sid"),request_id,env.get("REMOTE_ADDR"))
                return self._redirect(start,"/login",request_id,clear=True)
            if method not in {"GET","HEAD","OPTIONS"}: self._csrf(env,session)
            if self.authorization:
                permission=self._route_permission(path,method)
                if permission=="__not_found__": return self._error(start,404,request_id)
                try: self.authorization.require(session["uid"],permission)
                except PermissionError:
                    self.security.audit(session["uid"],"AUTHORIZATION_DENIED","ROUTE",path,request_id,env.get("REMOTE_ADDR"),{"method":method,"permission":permission},False)
                    raise
            if path == "/": return self._html(start,"dashboard.html",session,request_id)
            if path == "/catalogue": return self._html(start,"catalogue.html",session,request_id)
            if path == "/inventaire": return self._html(start,"inventory.html",session,request_id)
            if path == "/roadmap": return self._html(start,"roadmap.html",session,request_id)
            if path == "/administration": return self._html(start,"administration.html",session,request_id)
            if path == "/securite": return self._html(start,"security.html",session,request_id)
            if path == "/stock": return self._html(start,"stock.html",session,request_id)
            if path == "/achats": return self._html(start,"purchasing.html",session,request_id)
            if path == "/marketing": return self._html(start,"marketing.html",session,request_id)
            if path in {"/marketing/calendar","/marketing/publishing-queue","/marketing/review","/marketing/campaigns"}: return self._html(start,"marketing-operations.html",session,request_id)
            if path == "/marketing/social-analytics": return self._html(start,"social-analytics.html",session,request_id)
            if path == "/marketing/learning": return self._html(start,"marketing-learning.html",session,request_id)
            if path == "/sales": return self._html(start,"sales.html",session,request_id)
            if path == "/finance": return self._html(start,"finance.html",session,request_id)
            if path == "/settlements": return self._html(start,"settlements.html",session,request_id)
            if path == "/settlements/explorer": return self._html(start,"settlement-explorer.html",session,request_id)
            if path in {"/crm","/crm/customers","/crm/loyalty"}: return self._html(start,"crm.html",session,request_id)
            if path == "/api/crm/cockpit" and method == "GET": return self._json(start,self.crm.cockpit())
            if path == "/api/crm/customers" and method == "GET":
                q=parse_qs(env.get("QUERY_STRING","")); reveal="crm.customer.write" in self.security.permissions_for(session["uid"])
                return self._json(start,self.crm.customers(q.get("q",[""])[0],q.get("page",[1])[0],q.get("per_page",[25])[0],reveal))
            if path == "/api/crm/duplicates" and method == "GET": return self._json(start,{"items":self.crm.duplicate_candidates()})
            if path == "/api/crm/actions" and method == "GET": return self._json(start,{"items":self.crm.action_center()})
            if path == "/api/crm/rfm-config" and method == "GET": return self._json(start,self.crm.rfm_config())
            if path.startswith("/api/crm/customers/") and method == "GET":
                cid=unquote(path.removeprefix("/api/crm/customers/")); reveal="crm.customer.write" in self.security.permissions_for(session["uid"])
                return self._json(start,self.crm.customer_360(cid,reveal))
            if path == "/api/data-hub/diagnostics" and method == "GET":
                query=parse_qs(env.get("QUERY_STRING", "")); return self._json(start,{"diagnostics":self.data_hub.diagnostics.recent(query.get("source_id",[None])[0],query.get("limit",[10])[0])})
            if path == "/api/admin/sumup-schema" and method == "GET":
                return self._json(start,{**sumup_schema_diagnostic(self.bank.db),
                    "global_schema": self.global_schema_diagnostic(),
                    "sqlite_consumers":runtime_diagnostics(self.bank.db)})
            if path == "/api/admin/sumup" and method == "GET":
                return self._json(start,self.sumup_settlements.cockpit())
            if path == "/api/data-hub/evidence" and method == "GET": return self._json(start,self.data_hub.production_evidence())
            if path == "/api/production-evidence" and method == "GET":
                from .production_evidence import backup_inventory, reconciliation_report, rollback_check, snapshot
                runtime=self.data_hub.runtime(); hearts=runtime["worker_heartbeats"]
                evidence=snapshot(database=self.settings.database,environment=self.settings.environment,expected_commit=os.environ.get("EXPECTED_COMMIT"),deployed_commit=os.environ.get("DRCLOUD_COMMIT"),public_url=os.environ.get("DRCLOUD_HTTPS_URL"),worker_state=runtime["worker_state"],last_heartbeat=hearts[-1]["seen_at"] if hearts else None,sources=self.data_hub.production_evidence()["sources"])["production_evidence_snapshot"]
                persisted={}; evidence_file=Path(os.environ.get("DRCLOUD_RECOVERY_REPORT",self.settings.data_dir/"recovery_evidence.json"))
                try: persisted=json.loads(evidence_file.read_text(encoding="utf-8"))
                except (OSError,json.JSONDecodeError): pass
                persisted_backup=persisted.get("backup",{})
                return self._json(start,{**evidence,"backup":backup_inventory(self.backup_service.root),
                    "production_backup":persisted_backup,
                    "backup_location_classification":persisted_backup.get("location_classification",persisted.get("backup_location_classification","UNKNOWN")),
                    "last_restore_test":persisted.get("restore",{"restore_result":"RESTORE_NOT_PROVEN"}),
                    "last_rollback_test":persisted.get("rollback",rollback_check()),"reconciliation":reconciliation_report(self.settings.database)})
            if path == "/api/data-hub" and method == "GET": return self._json(start,{**self.data_hub.health(),"latest_batch":self.data_hub.latest_batch(),"sales_diagnostics":self.sales_sync.diagnostics(),"runtime":{**self.data_hub.runtime(self.automation_operations()),"configuration":{"prestashop_paid_state_ids":"CONFIGURED" if self.prestashop_paid_states_configured else "MISSING","qonto":self.qonto_configuration}}})
            if path in {"/api/data-hub/sync-all","/api/data-hub/sync-all/retry"} and method == "POST":
                try: result=self.data_hub.run_all(self.automation_operations(),triggered_by=session["uid"],retry_failed=path.endswith("/retry"))
                except BatchAlreadyRunning: return self._json(start,{"error":"BATCH_ALREADY_RUNNING"},"409 Conflict")
                self.security and self.security.audit(session["uid"],"DATA_HUB_SYNC_ALL","DATA_HUB_BATCH",result["batch_id"],request_id,"api",result["summary"])
                return self._json(start,result)
            if path.startswith("/api/data-hub/sync-batches/") and method == "GET":
                result=self.data_hub.sync_batch(unquote(path.removeprefix("/api/data-hub/sync-batches/")))
                return self._json(start,result) if result else self._json(start,{"error":"Batch introuvable"},"404 Not Found")
            if path == "/api/admin/shopcaisse-sales/failures" and method == "GET":
                return self._json(start,self.sales_sync.failed_sales("SHOPCAISSE"))
            if path.startswith("/api/data-hub/sources/") and path.endswith("/test") and method == "POST":
                source_id=unquote(path.removeprefix("/api/data-hub/sources/").removesuffix("/test")); sources={s["source_id"]:s for s in self.data_hub.sources()}
                source=sources.get(source_id); client=self.shopcaisse_client if source_id=="shopcaisse_sales" else self.prestashop_client if source_id in {"prestashop_sales","prestashop_catalog"} else self.sumup_provider if source_id.startswith("sumup_") else self.bank_provider if source_id=="bank" and not isinstance(self.bank_provider,DisabledQontoProvider) else None
                if not source or not client: return self._json(start,{"error":"Connecteur non configuré"},"409 Conflict")
                operation="QONTO_HEALTH" if source_id=="bank" else "authentication" if source_id=="shopcaisse_sales" or source_id.startswith("sumup_") else "order_states"; path_value="/v2/organization" if source_id=="bank" else "/transactions/history" if source_id.startswith("sumup_") else "/authentication" if source_id=="shopcaisse_sales" else "/order_states"
                check=client.health if source_id=="bank" or source_id=="shopcaisse_sales" or source_id.startswith("sumup_") else lambda: next(client.iter_resource("order_states"),None)
                diagnostic_id, diagnostic=self.data_hub.connector_health_check(source_id,source["provider"],check,
                    operation=operation,stage="manual_health",endpoint_path=path_value,request_id=request_id)
                status="OK" if diagnostic.success else "ERROR"
                if source_id=="bank":
                    self.settlements.qonto_configured=diagnostic.success
                    self.qonto_configuration.update({"health_attempted":"OUI","health_status":"CONNECTED" if diagnostic.success else "ERROR"})
                self.security and self.security.audit(session["uid"],"CONNECTOR_DIAGNOSTIC_TEST","DATA_SOURCE",source_id,request_id,"api",{"diagnostic_id":diagnostic_id,"result":status})
                return self._json(start,{"status":status,"diagnostic_id":diagnostic_id,"diagnostic":self.data_hub.diagnostics.recent(source_id,1,failures_only=False)[0]})
            if path.startswith("/api/data-hub/jobs/") and path.endswith("/run") and method == "POST":
                job_id=unquote(path.removeprefix("/api/data-hub/jobs/").removesuffix("/run"))
                operations=self.automation_operations()
                job=self.data_hub.job(job_id)
                if not job or job["job_type"] not in operations: return self._json(start,{"error":"Job non exécutable ou connecteur absent"},"409 Conflict")
                result=self.data_hub.run(job_id,operations[job["job_type"]],manual=True)
                self.security and self.security.audit(session["uid"],"DATA_HUB_MANUAL_SYNC","DATA_HUB_JOB",job_id,request_id,"api",{"job_type":job["job_type"]})
                return self._json(start,result)
            if path == "/api/finance" and method == "GET": return self._json(start,{"projection":self.finance.snapshot(),"transactions":self.bank.transactions(),"reconciliations":self.reconciliation.list()})
            if path == "/api/finance/summary" and method == "GET": return self._json(start,self.finance.summary())
            if path == "/api/finance/cockpit" and method == "GET": return self._json(start,self.finance.finance_cockpit())
            if path == "/api/finance/cashflow" and method == "GET": return self._json(start,self.finance.cashflow())
            if path == "/api/finance/tax" and method == "GET": return self._json(start,self.finance.tax())
            if path == "/api/finance/profitability" and method == "GET": return self._json(start,self.finance.profitability())
            if path == "/api/finance/reconciliations" and method == "GET": return self._json(start,{"counts":self.finance._reconciliation_counts(),"items":self.reconciliation.list()})
            if path == "/api/finance/bank-ledger" and method == "GET":
                query=parse_qs(env.get("QUERY_STRING","")); return self._json(start,self.financial_reconciliation.ledger(limit=query.get("limit",[100])[0],offset=query.get("offset",[0])[0]))
            if path == "/api/finance/reconciliation" and method == "GET": return self._json(start,{**self.financial_reconciliation.matches(),"evidence":self.financial_reconciliation.evidence()})
            if path == "/api/finance/anomalies" and method == "GET":
                query=parse_qs(env.get("QUERY_STRING","")); return self._json(start,self.financial_reconciliation.anomalies(query.get("status",[None])[0]))
            if path == "/api/finance/reconciliation/recompute" and method == "POST":
                result=self.financial_reconciliation.recompute(); self.security and self.security.audit(session["uid"],"FINANCE_RECONCILIATION_RECOMPUTED","FINANCE_RECONCILIATION",result["run_id"],request_id,"finance",result); return self._json(start,result,"202 Accepted")
            if path.startswith("/api/finance/reconciliation/") and path.endswith(("/confirm","/reject")) and method == "POST":
                action="confirm" if path.endswith("/confirm") else "reject"; match_id=unquote(path.removeprefix("/api/finance/reconciliation/").removesuffix("/"+action))
                try: result=self.financial_reconciliation.review(match_id,"CONFIRM" if action=="confirm" else "REJECT",session["uid"])
                except KeyError: return self._json(start,{"error":"Rapprochement introuvable"},"404 Not Found")
                self.security and self.security.audit(session["uid"],"FINANCE_MATCH_"+action.upper(),"FINANCE_RECONCILIATION",match_id,request_id,"finance"); return self._json(start,result)
            if path == "/api/settlements/summary" and method == "GET": return self._json(start,self.settlements.summary())
            if path == "/api/settlements/shopcaisse-audit" and method == "GET": return self._json(start,self.settlements.shopcaisse_audit())
            if path.startswith("/api/settlements/aggregates/") and method == "GET":
                name=path.rsplit("/",1)[-1]; summary=self.settlements.summary()
                if name not in {"cash_summary","in_transit","expected_payouts","anomaly_breakdown","settlement_coverage","daily_trends"}: return self._json(start,{"error":"Agrégat inconnu"},"404 Not Found")
                return self._json(start,{name:summary[name]})
            if path == "/api/settlements/backfill-preview" and method == "GET": return self._json(start,self.settlements.backfill_preview())
            if path == "/api/settlements/explorer" and method == "GET":
                query={k:v[0] for k,v in parse_qs(env.get("QUERY_STRING","")).items()}; return self._json(start,self.settlements.explorer(query))
            if path == "/api/settlements/anomalies" and method == "GET":
                query={k:v[0] for k,v in parse_qs(env.get("QUERY_STRING","")).items()}; return self._json(start,self.settlements.anomalies(query))
            if path in {"/api/settlements/transactions","/api/settlements/payouts","/api/settlements/qonto"} and method == "GET":
                query={k:v[0] for k,v in parse_qs(env.get("QUERY_STRING","")).items()}; kind=path.rsplit("/",1)[-1]; query.update({"source_type":"SUMUP_PAYOUT"} if kind=="payouts" else {"target_type":"QONTO_CREDIT"} if kind=="qonto" else {"target_type":"SUMUP_TRANSACTION"}); return self._json(start,self.settlements.explorer(query))
            if path == "/api/settlements/matches" and method == "GET": return self._json(start,{"items":self.settlements.matches()})
            if path == "/api/settlements/conflicts" and method == "GET": return self._json(start,{"items":self.settlements.matches("CONFLICT")})
            if path.startswith("/api/settlements/") and path.endswith("/details") and method == "GET":
                sid=unquote(path.removeprefix("/api/settlements/").removesuffix("/details"))
                try:return self._json(start,self.settlements.details(sid))
                except KeyError:return self._json(start,{"error":"Rapprochement introuvable"},"404 Not Found")
            if path.startswith("/api/settlements/") and path.endswith(("/timeline","/evidence")) and method == "GET":
                action="timeline" if path.endswith("/timeline") else "evidence"; sid=unquote(path.removeprefix("/api/settlements/").removesuffix("/"+action))
                try:return self._json(start,getattr(self.settlements,action)(sid))
                except KeyError:return self._json(start,{"error":"Rapprochement introuvable"},"404 Not Found")
            if path.startswith("/api/settlements/") and "/" not in path.removeprefix("/api/settlements/") and method == "GET":
                sid=unquote(path.removeprefix("/api/settlements/"))
                try:return self._json(start,self.settlements.details(sid))
                except KeyError:return self._json(start,{"error":"Rapprochement introuvable"},"404 Not Found")
            if path.startswith("/api/settlements/payouts/") and method == "GET":
                try: return self._json(start,self.settlements.payout(unquote(path.removeprefix("/api/settlements/payouts/"))))
                except KeyError: return self._json(start,{"error":"Payout introuvable"},"404 Not Found")
            if path.startswith("/api/settlements/") and path.endswith(("/confirm","/reject")) and method == "POST":
                action="confirm" if path.endswith("/confirm") else "reject"; sid=unquote(path.removeprefix("/api/settlements/").removesuffix("/"+action))
                try: result=self.settlements.review(sid,"MATCHED" if action=="confirm" else "REJECTED",session["uid"])
                except KeyError: return self._json(start,{"error":"Rapprochement introuvable"},"404 Not Found")
                self.security and self.security.audit(session["uid"],"SETTLEMENT_CONFIRMED" if action=="confirm" else "SETTLEMENT_REJECTED","PAYMENT_SETTLEMENT",sid,request_id,"settlements",{"match_method":result["match_method"]})
                return self._json(start,result)
            if path.startswith("/api/settlements/") and path.endswith(("/detach","/review")) and method == "POST":
                action="detach" if path.endswith("/detach") else "review"; sid=unquote(path.removeprefix("/api/settlements/").removesuffix("/"+action))
                try:result=self.settlements.review(sid,"UNMATCHED" if action=="detach" else "POSSIBLE",session["uid"])
                except (KeyError,ValueError):return self._json(start,{"error":"Rapprochement ou décision invalide"},"404 Not Found")
                self.security and self.security.audit(session["uid"],"SETTLEMENT_"+action.upper(),"PAYMENT_SETTLEMENT",sid,request_id,"settlements")
                return self._json(start,result)
            if path.startswith("/api/settlements/") and path.endswith(("/note","/notes")) and method == "POST":
                suffix="/notes" if path.endswith("/notes") else "/note"; sid=unquote(path.removeprefix("/api/settlements/").removesuffix(suffix))
                try:result=self.settlements.note(sid,self._body(env).get("note"),session["uid"])
                except KeyError:return self._json(start,{"error":"Rapprochement introuvable"},"404 Not Found")
                except ValueError:return self._json(start,{"error":"Note invalide"},"400 Bad Request")
                self.security and self.security.audit(session["uid"],"SETTLEMENT_NOTE_ADDED","PAYMENT_SETTLEMENT",sid,request_id,"settlements")
                return self._json(start,result)
            if path == "/api/settlements/backfill" and method == "POST":
                # The settlement projection cannot recover payments skipped by an
                # already-advanced sales cursor. Re-read the complete read-only
                # ShopCaisse history first; payment upserts make this idempotent.
                imported=self.sales_sync.sync("SHOPCAISSE",force=True,actor=session.get("u","authenticated"))
                result={"shopcaisse_import":imported,**self.settlements.backfill(batch_size=int(self._body(env).get("batch_size",500)))}; self.security and self.security.audit(session["uid"],"SETTLEMENT_BACKFILL_STARTED","PAYMENT_SETTLEMENT_RUN",result["run_id"],request_id,"settlements",result); return self._json(start,result,"202 Accepted")
            if path == "/api/settlements/recompute" and method == "POST":
                result=self.settlements.recompute(); self.security and self.security.audit(session["uid"],"SETTLEMENT_RECOMPUTED","PAYMENT_SETTLEMENT",None,request_id,"settlements",result); return self._json(start,result)
            if path == "/api/purchasing/costs" and method == "GET": return self._json(start,{"events":self.purchase_costs._rows("SELECT * FROM purchase_cost_events ORDER BY received_at DESC")})
            if path == "/api/purchasing/cost-lots" and method == "GET": return self._json(start,{"lots":self.purchase_costs._rows("SELECT * FROM inventory_cost_lots ORDER BY received_at,cost_lot_id")})
            if path == "/api/purchasing/stock-value" and method == "GET": return self._json(start,self.purchase_costs.stock_value())
            if path == "/api/purchasing/profitability" and method == "GET": return self._json(start,self.purchase_costs.profitability())
            if path == "/api/purchasing/invoices/preview" and method == "POST": return self._json(start,self.purchase_costs.preview_csv(str(self._body(env).get("csv") or "")))
            if path == "/api/purchasing/invoices/apply" and method == "POST":
                body=self._body(env); return self._json(start,self.purchase_costs.apply_csv(str(body.get("csv") or ""),str(body.get("preview_id") or ""),session.get("u","authenticated")),"201 Created")
            if path == "/api/purchasing/mappings" and method == "POST":
                body=self._body(env); result=self.purchase_costs.map_product(str(body.get("supplier_id") or ""),str(body.get("supplier_reference") or ""),str(body.get("product_key") or ""),ean=body.get("supplier_ean"),actor=session.get("u","authenticated")); self.security and self.security.audit(session["uid"],"SUPPLIER_PRODUCT_MAPPED","SUPPLIER_PRODUCT_MAPPING",result["mapping_id"],request_id,"purchasing",{"product_key":result["product_key"]}); return self._json(start,{"mapping":result},"201 Created")
            if path.startswith("/api/purchasing/invoices/"):
                tail=path.removeprefix("/api/purchasing/invoices/"); parts=tail.split("/"); iid=parts[0]
                if len(parts)==1 and method=="GET": return self._json(start,{"invoice":self.purchase_costs.invoice(iid)})
                if parts[1:]==["control"] and method=="GET": return self._json(start,self.purchase_costs.control_invoice(iid))
                if parts[1:]==["validate"] and method=="POST":
                    value=self.purchase_costs.validate_invoice(iid,session.get("u","authenticated")); self.security and self.security.audit(session["uid"],"SUPPLIER_INVOICE_VALIDATED","SUPPLIER_INVOICE",iid,request_id,"purchasing",{}); return self._json(start,{"invoice":value})
            if path.startswith("/api/finance/bank-transactions/") and path.endswith("/classification") and method == "PATCH":
                transaction_id=unquote(path.removeprefix("/api/finance/bank-transactions/").removesuffix("/classification"));body=self._body(env)
                row=self.bank.classify(transaction_id,str(body.get("category") or "UNKNOWN"),confirmed=True)
                self.security and self.security.audit(session["uid"],"BANK_CLASSIFICATION_CONFIRMED","BANK_TRANSACTION",transaction_id,request_id,"finance",{"category":row["category"]})
                return self._json(start,{"transaction":row})
            if path == "/api/sales/cockpit" and method == "GET":
                return self._json(start,{"analytics":self.sales.analytics(),"sources":self.sales_sync.diagnostics(),"sales":self.sales_sync.sales(),"unmatched":self.sales_sync.unmatched()})
            if path == "/api/sales/shopcaisse/preview" and method == "POST":
                content=str(self._body(env).get("csv") or "");provider=ShopCaisseCSVProvider(content)
                report=self.sales_sync.preview(provider);self.sales_import_preview={"content":content,"report":report}
                self.sales._audit("SALES_IMPORT_PREVIEWED",session.get("u","authenticated"),report);return self._json(start,report)
            if path == "/api/sales/shopcaisse/apply" and method == "POST":
                content=str(self._body(env).get("csv") or "")
                if not self.sales_import_preview or content!=self.sales_import_preview["content"]: raise ValueError("a matching preview is required")
                provider=ShopCaisseCSVProvider(content);self.sales_sync.providers["SHOPCAISSE"]=provider
                result=self.sales_sync.sync("SHOPCAISSE",actor=session.get("u","authenticated"));self.sales._audit("SALES_IMPORT_APPLIED",session.get("u","authenticated"),result);self.sales_import_preview=None
                return self._json(start,result)
            if path == "/api/sales/mappings" and method == "POST":
                body=self._body(env);self.sales_sync.create_mapping(str(body.get("source") or ""),str(body.get("external_product_id") or ""),str(body.get("external_variant_id") or ""),str(body.get("product_key") or ""),session.get("u","authenticated"));return self._json(start,{"created":True},"201 Created")
            if path == "/api/sales/sync/prestashop" and method == "POST":
                return self._json(start,self.sales_sync.sync("PRESTASHOP",actor=session.get("u","authenticated")))
            if path == "/api/sales/status" and method == "GET": return self._json(start,self.sales.status())
            if path == "/api/sales/metrics" and method == "GET": return self._json(start,self.sales.analytics())
            if path.startswith("/api/sales/products/") and method == "GET":
                return self._json(start,self.sales.product_metrics(unquote(path.removeprefix("/api/sales/products/"))))
            if path == "/api/sales/import/preview" and method == "POST":
                body=self._body(env); return self._json(start,self.sales.preview_csv(str(body.get("csv") or ""),session.get("u","authenticated")))
            if path == "/api/sales/import/apply" and method == "POST":
                body=self._body(env); return self._json(start,self.sales.apply_csv(str(body.get("batch_id") or ""),str(body.get("csv") or ""),session.get("u","authenticated")))
            if path == "/api/marketing/analytics" and method == "GET": return self._json(start,self.sales.analytics())
            if path == "/api/marketing/analytics/products" and method == "GET": return self._json(start,{"products":self.sales.analytics()["products"]})
            if path == "/api/marketing/analytics/social" and method == "GET": return self._json(start,self.social_analytics.summary())
            if path == "/api/marketing/social-analytics/live" and method == "GET": return self._json(start,self.social_live.cockpit(int(parse_qs(env.get("QUERY_STRING","")).get("days",[30])[0])))
            if path == "/api/marketing/intelligence/proposals" and method == "POST": return self._json(start,self.marketing_intelligence.generate(session.get("u","authenticated")))
            if path == "/api/marketing/learning" and method == "GET": return self._json(start,self.marketing_intelligence.learning())
            if path == "/api/marketing/cockpit" and method == "GET":
                return self._json(start,self.marketing_operations.cockpit(int(parse_qs(env.get("QUERY_STRING","")).get("days",[30])[0])))
            if path in {"/api/marketing/calendar","/api/marketing/publishing-queue","/api/marketing/review","/api/marketing/campaigns"} and method == "GET":
                query={k:v[0] for k,v in parse_qs(env.get("QUERY_STRING","")).items()}; name=path.rsplit("/",1)[-1].replace("publishing-queue","queue")
                return self._json(start,getattr(self.marketing_operations,name)(query))
            if path == "/api/marketing/dashboard" and method == "GET":
                return self._json(start,{"settings":self.marketing_repository.settings(),"preview":self.marketing.preview(),"analytics":self.sales.analytics(),"social_analytics":self.social_analytics.summary(),"proposals":self.marketing_repository.rows("marketing_proposals"),"opportunities":self.marketing_repository.rows("marketing_opportunities"),"schedules":self.marketing_repository.rows("marketing_schedules"),"connections":self.marketing_repository.rows("social_connections")})
            if path == "/api/marketing/social-connections" and method == "POST":
                body=self._body(env); value=self.social_connections.configure(str(body.get("channel") or ""),str(body.get("account_id") or ""),str(body.get("credential_reference") or ""),session.get("u","authenticated"),body.get("display_name"))
                return self._json(start,{"connection":value},"201 Created")
            if path.startswith("/api/marketing/social-connections/") and path.endswith("/check") and method == "POST":
                identifier=unquote(path.removeprefix("/api/marketing/social-connections/").removesuffix("/check"))
                return self._json(start,{"connection":self.social_connections.check_connection(identifier,session.get("u","authenticated"))})
            if path.startswith("/api/marketing/schedules/") and path.endswith("/cancel") and method == "POST":
                identifier=unquote(path.removeprefix("/api/marketing/schedules/").removesuffix("/cancel"))
                return self._json(start,{"schedule":self.marketing_scheduling.cancel(identifier,session.get("u","authenticated"))})
            if path.startswith("/api/marketing/schedules/") and path.endswith("/prerequisites") and method == "GET":
                identifier=unquote(path.removeprefix("/api/marketing/schedules/").removesuffix("/prerequisites")); item=self.marketing_scheduling.get(identifier)
                check=self.marketing_scheduling.prerequisites(item["proposal_id"],item["creative_id"],item["channel"],item["account_id"],item["scheduled_at"],item["timezone"])
                return self._json(start,{"publishable":check.publishable,"reasons":check.reasons})
            if path.startswith("/api/marketing/schedules/") and method == "PATCH":
                identifier=unquote(path.removeprefix("/api/marketing/schedules/")); body=self._body(env)
                return self._json(start,{"schedule":self.marketing_scheduling.update(identifier,str(body.get("scheduled_at") or ""),str(body.get("timezone") or "UTC"),session.get("u","authenticated"))})
            if path.startswith("/api/marketing/proposals/") and "/creative" in path:
                tail=path.removeprefix("/api/marketing/proposals/"); identifier=unquote(tail.split("/creative",1)[0])
                action=tail.split("/creative",1)[1].strip("/")
                actor=session.get("u","authenticated")
                if not action and method=="GET":
                    detail=self.creative_ai.get(identifier)
                    review=self.creative_review.detail(identifier)
                    detail.update({k:review[k] for k in ("reviewable","blocking_reasons","rejection_reason")})
                    return self._json(start,detail)
                if action=="generate" and method=="POST": return self._json(start,self.creative_ai.generate(identifier,actor))
                if action=="regenerate" and method=="POST": return self._json(start,self.creative_ai.regenerate(identifier,actor))
                if action=="approve" and method=="POST": return self._json(start,self.creative_review.approve(identifier,actor))
                if action=="reject" and method=="POST":
                    return self._json(start,self.creative_review.reject(identifier,str(self._body(env).get("reason") or ""),actor))
                raise KeyError(path)
            if path == "/api/marketing/autopilot/preview" and method == "POST":
                return self._json(start,self.marketing.preview())
            if path == "/api/marketing/autopilot/activation" and method == "POST":
                enabled=self._body(env).get("enabled") is True
                self.marketing_repository.set_automation(enabled,session.get("u","authenticated"))
                return self._json(start,{"automation_enabled":enabled})
            if path == "/api/marketing/autopilot/run" and method == "POST":
                return self._json(start,self.marketing.run(session.get("u","authenticated")))
            if path == "/api/marketing/proposals" and method == "POST":
                body=self._body(env); identifier=self.marketing.create_manual_proposal(str(body.get("title") or ""),body.get("product_keys") or [],str(body.get("reason") or ""),session.get("u","authenticated"))
                return self._json(start,{"proposal_id":identifier},"201 Created")
            if path.startswith("/api/marketing/proposals/") and path.endswith("/status") and method == "POST":
                identifier=unquote(path.removeprefix("/api/marketing/proposals/").removesuffix("/status"));body=self._body(env)
                return self._json(start,self.marketing.transition(identifier,str(body.get("status") or ""),session.get("u","authenticated"),str(body.get("reason") or "")))
            if path.startswith("/api/marketing/proposals/") and path.endswith("/schedule") and method == "POST":
                identifier=unquote(path.removeprefix("/api/marketing/proposals/").removesuffix("/schedule"));body=self._body(env)
                schedule=self.marketing_scheduling.create(identifier,str(body.get("creative_id") or ""),str(body.get("channel") or ""),str(body.get("account_id") or ""),str(body.get("scheduled_at") or ""),str(body.get("timezone") or "UTC"),session.get("u","authenticated"))
                return self._json(start,{"schedule":schedule},"201 Created")
            if path == "/api/marketing/media" and method == "GET":
                q=parse_qs(env.get("QUERY_STRING",""));usage=q.get("usage",[""])[0]
                rows=self.media.repository.db.execute("SELECT * FROM product_media WHERE active=1 ORDER BY product_key,role").fetchall()
                values=[dict(row) for row in rows if not usage or row["marketing_usage"]==usage]
                return self._json(start,{"media":values,"filters":["ALLOWED","UNKNOWN","FORBIDDEN","PRODUCTS","CAMPAIGNS","GENERATED"]})
            if path.startswith("/achats/fournisseurs/"):
                return self._html(start,"purchasing.html",session,request_id)
            if path.startswith("/achats/commandes"):
                return self._html(start,"purchasing.html",session,request_id)
            if path.startswith("/achats/receptions"):
                return self._html(start,"purchasing.html",session,request_id)
            if path == "/api/replenishment" and method == "GET":
                return self._json(start,{"products":[self.replenishment.snapshot(p.drcloud_product_key) for p in self.os_repository.all()],"evidence":self.replenishment.evidence()})
            if path == "/api/replenishment/refresh" and method == "POST":
                return self._json(start,self.replenishment.refresh())
            if path == "/api/replenishment/settings" and method == "PATCH":
                return self._json(start,self.replenishment.configure(**self._body(env)))
            if path.startswith("/api/replenishment/suggestions/") and path.endswith("/status") and method == "POST":
                identifier=unquote(path.removeprefix("/api/replenishment/suggestions/").removesuffix("/status"))
                return self._json(start,self.replenishment.transition(identifier,self._body(env)["status"]))
            if path == "/api/goods-receipts" and method == "GET":
                return self._json(start,{"goods_receipts":[self._goods_receipt(r) for r in self.goods_receipts.repository.list()]})
            if path.startswith("/api/goods-receipts/"):
                tail=path.removeprefix("/api/goods-receipts/"); parts=tail.split("/"); rid=parts[0]
                if len(parts)==1 and method=="GET":
                    receipt,lines=self.goods_receipts.detail(rid); return self._json(start,{"goods_receipt":self._goods_receipt(receipt),"lines":[self._with_media(asdict(x),x.product_key) for x in lines],"movements":[self._stock_movement(x) for x in self.service.repo.list() if x.source_type=="GOODS_RECEIPT" and x.source_id==rid]})
                if parts[1:]==["apply"] and method=="POST": return self._json(start,{"goods_receipt":self._goods_receipt(self.goods_receipts.apply(rid,session.get("u","authenticated")))})
            if path == "/api/purchase-orders" and method == "GET":
                q=parse_qs(env.get("QUERY_STRING","")); status=q.get("status",[None])[0]
                rows=self.purchase_orders.list(status)
                return self._json(start,{"purchase_orders":[self._purchase_order(row) for row in rows]})
            if path == "/api/purchase-orders" and method == "POST":
                row=self.purchase_orders.create(self._body(env),session.get("u","authenticated"))
                return self._json(start,{"purchase_order":self._purchase_order(row)},"201 Created")
            if path.startswith("/api/purchase-orders/"):
                tail=path.removeprefix("/api/purchase-orders/"); parts=tail.split("/"); oid=parts[0]
                if len(parts)==1 and method=="GET":
                    row=self.purchase_orders.get(oid)
                    if not row:return self._json(start,{"error":"Commande fournisseur introuvable"},"404 Not Found")
                    return self._json(start,{"purchase_order":self._purchase_order(row),"activities":[asdict(a) for a in self.purchase_orders.activities(oid)]})
                if len(parts)==1 and method=="PATCH": return self._json(start,{"purchase_order":self._purchase_order(self.purchase_orders.update(oid,self._body(env),session.get("u","authenticated")))})
                if parts[1:]==["receivable"] and method=="GET": return self._json(start,{"lines":[self._with_media(x,x["product_key"]) for x in self.goods_receipts.receivable(oid)]})
                if parts[1:]==["receipts"] and method=="POST": return self._json(start,{"goods_receipt":self._goods_receipt(self.goods_receipts.create(oid,self._body(env),session.get("u","authenticated")))} ,"201 Created")
                if parts[1:]==["status"] and method=="POST": return self._json(start,{"purchase_order":self._purchase_order(self.purchase_orders.transition(oid,self._body(env)["status"],session.get("u","authenticated")))})
                if parts[1:]==["lines"] and method=="POST": return self._json(start,{"line":asdict(self.purchase_orders.add_line(oid,self._body(env),session.get("u","authenticated")))},"201 Created")
                if len(parts)==3 and parts[1]=="lines" and method=="PATCH": return self._json(start,{"line":asdict(self.purchase_orders.update_line(oid,parts[2],self._body(env),session.get("u","authenticated")))})
                if len(parts)==3 and parts[1]=="lines" and method=="DELETE":
                    self.purchase_orders.remove_line(oid,parts[2],session.get("u","authenticated")); return self._json(start,{"deleted":True})
            if path == "/api/suppliers" and method == "GET":
                q=parse_qs(env.get("QUERY_STRING","")); status=q.get("status",[None])[0]
                rows=self.suppliers.list(q.get("q",[""])[0],SupplierStatus(status) if status else None)
                return self._json(start,{"suppliers":[asdict(row) for row in rows]})
            if path == "/api/suppliers" and method == "POST":
                row,duplicates=self.suppliers.create(self._body(env),session.get("u","authenticated"))
                return self._json(start,{"supplier":asdict(row),"possible_duplicates":[asdict(x) for x in duplicates]},"201 Created")
            if path.startswith("/api/suppliers/"):
                tail=path.removeprefix("/api/suppliers/"); is_status=tail.endswith("/status")
                supplier_id=tail.removesuffix("/status")
                if is_status and method == "POST":
                    return self._json(start,asdict(self.suppliers.transition(supplier_id,self._body(env)["status"],session.get("u","authenticated"))))
                if method == "GET":
                    row=self.suppliers.get(supplier_id)
                    if not row: return self._json(start,{"error":"Fournisseur introuvable"},"404 Not Found")
                    return self._json(start,{"supplier":asdict(row),"activities":[asdict(a) for a in self.suppliers.activities(supplier_id)]})
                if method == "PATCH":
                    row,duplicates=self.suppliers.update(supplier_id,self._body(env),session.get("u","authenticated"))
                    return self._json(start,{"supplier":asdict(row),"possible_duplicates":[asdict(x) for x in duplicates]})
            if path == "/api/dashboard":
                road=self.roadmap_service.load(); return self._json(start,{"progress_percent":road["global_progress_percent"],"next":next((m["next"] for m in road["modules"] if m.get("next")),None),"catalogue":len(self.service.items),"inventory":{"session":self.service.session(),"progress":self.service.progress()},"systems":self.admin_status.collect(),"sales":{"last_7_days":self.sales.metrics(None,7),"last_30_days":self.sales.metrics(None,30),"freshness":self.sales.status()["freshness"]},"purchase_costs":{"stock_value":self.purchase_costs.stock_value(),"profitability":self.purchase_costs.profitability(),"invoices_to_review":self.purchase_costs.db.execute("SELECT count(*) FROM supplier_invoices WHERE status!=\'VALIDATED\'").fetchone()[0]}},headers=[("X-Request-ID",request_id)])
            if path == "/api/state": return self._json(start,{"session":self.service.session(),"progress":self.service.progress(),"proposal":self.service.proposal()})
            if path == "/api/roadmap": return self._json(start,self.roadmap_service.load())
            if path == "/api/roadmap/health": return self._json(start,self.roadmap_service.diagnostic())
            if path == "/api/admin/status": return self._json(start,self.admin_status.collect(),headers=[("X-Request-ID",request_id)])
            if path == "/api/admin/product-media-import/preview" and method == "POST":
                try:
                    self.media_import_preview=self.media_import.service().preview()
                except PrestaShopIntegrationUnavailable as exc:
                    return self._json(start,{"error":str(exc),"integration":self.media_import.status()},"503 Service Unavailable")
                except PrestaShopError:
                    self.media_import.mark_unavailable()
                    return self._json(start,{"error":"Import PrestaShop indisponible — service externe inaccessible","integration":self.media_import.status()},"503 Service Unavailable")
                return self._json(start,self.media_import_preview)
            if path == "/api/admin/product-media-import/candidate-image" and method == "GET":
                q=parse_qs(env.get("QUERY_STRING","")); key=q.get("product_key",[""])[0]; image_id=q.get("image_id",[""])[0]
                data,mime=self.media_import.service().download_manual_candidate(key,image_id)
                return self._send(start,data,mime,headers=[("X-Request-ID",request_id),
                    ("Cache-Control","private, no-store"),("Content-Length",str(len(data)))],cache=False)
            if path == "/api/admin/product-media-import/resolve" and method == "POST":
                body=self._body(env)
                result=self.media_import.service().resolve_manual(
                    str(body.get("drcloud_product_key") or ""),str(body.get("image_id") or ""),
                    actor=session.get("u","authenticated"))
                self.media_import_preview=None
                return self._json(start,result)
            if path == "/api/admin/product-media-import/apply" and method == "POST":
                if not self.media_import_preview:
                    return self._json(start,{"error":"Un PREVIEW courant est requis"},"409 Conflict")
                try:
                    result=self.media_import.service().apply(actor=session.get("u","authenticated"))
                except PrestaShopIntegrationUnavailable as exc:
                    return self._json(start,{"error":str(exc),"integration":self.media_import.status()},"503 Service Unavailable")
                self.media_import_preview=None
                return self._json(start,result)
            if path == "/api/admin/catalogue-rehydration/status" and method == "GET":
                return self._json(start,self.catalogue_rehydration.status())
            if path == "/api/admin/catalogue-rehydration/preview" and method == "POST":
                return self._json(start,self.catalogue_rehydration.request_preview(session.get("u","authenticated")),"202 Accepted")
            if path == "/api/admin/catalogue-rehydration/report" and method == "GET":
                q=parse_qs(env.get("QUERY_STRING","")); page=max(1,int(q.get("page",[1])[0])); per_page=min(100,max(1,int(q.get("per_page",[25])[0])))
                classification=q.get("classification",["ALL"])[0]
                if classification not in {"ALL","SAFE","AMBIGUOUS","NO_DATA"}: raise ValueError("Filtre invalide")
                return self._json(start,self.catalogue_rehydration.report(q.get("report_id",[None])[0],page=page,per_page=per_page,classification=classification,search=q.get("search",[""])[0]))
            if path == "/api/admin/catalogue-rehydration/apply" and method == "POST":
                report_id=str(self._body(env).get("report_id") or "")
                return self._json(start,self.catalogue_rehydration.request_apply(report_id,session.get("u","authenticated")),"202 Accepted")
            if path == "/api/security/change-password" and method == "POST":
                return self._change_password(env,start,session,request_id)
            if path == "/api/security/overview" and method == "GET":
                return self._json(start,{"users":self.security.users(),"roles":[{"name":r,"permissions":sorted(self.security.permissions_for_role(r))} for r in ("ADMIN","MANAGER","STAFF","READ_ONLY")],"sessions":self.security.active_sessions(),"events":self.security.audits(30),"password_policy":{"minimum_length":14,"hash":"PBKDF2-HMAC-SHA256","iterations":600000},"protections":{"csrf":True,"session_versioning":True,"default_deny":True,"security_headers":True},"secret_references":[dict(r) for r in self.security.db.execute("SELECT secret_ref,provider,purpose,status,created_at,updated_at,last_rotated_at FROM secret_references ORDER BY provider,purpose")]})
            if path == "/api/security/audits" and method == "GET":
                q=parse_qs(env.get("QUERY_STRING","")); boolean=q.get("success",[None])[0]
                return self._json(start,{"events":self.security.audits(q.get("limit",[50])[0],actor=q.get("actor",[None])[0],entity_type=q.get("domain",[None])[0],action=q.get("action",[None])[0],success=None if boolean is None else boolean.lower() in {"1","true","yes"},since=q.get("since",[None])[0],until=q.get("until",[None])[0])})
            if path == "/api/security/settings" and method == "GET": return self._json(start,{"settings":self.security.settings()})
            if path.startswith("/api/security/settings/") and method in {"PUT","POST"}:
                key=unquote(path.removeprefix("/api/security/settings/")); body=self._body(env)
                return self._json(start,{"setting":self.security.set_setting(key,body.get("value"),session["uid"],request_id=request_id)})
            if path == "/api/security/secret-references" and method == "POST":
                body=self._body(env); ref=self.security.register_secret_reference(str(body.get("secret_ref") or ""),str(body.get("provider") or ""),str(body.get("purpose") or ""),session["uid"],status=str(body.get("status") or "ACTIVE"),request_id=request_id)
                return self._json(start,{"secret_reference":ref},"201 Created")
            if path == "/api/security/users" and method == "POST":
                body=self._body(env); user=self.security.create_user(str(body.get("username") or ""),str(body.get("display_name") or ""),str(body.get("password") or ""),body.get("roles") or [],session["uid"]); self.security.audit(session["uid"],"USER_CREATED","USER",user["user_id"],request_id,env.get("REMOTE_ADDR"),{"roles":body.get("roles")}); return self._json(start,{"user":{**user,"roles":self.security.roles_for(user["user_id"])}},"201 Created")
            if path.startswith("/api/security/users/"):
                parts=path.removeprefix("/api/security/users/").split("/"); user_id=unquote(parts[0]); action=parts[1] if len(parts)>1 else ""
                body=self._body(env) if method=="POST" else {}
                if action=="status" and method=="POST": result=self.security.set_status(user_id,str(body.get("status") or ""),session["uid"]); self.security.audit(session["uid"],"USER_STATUS_CHANGED","USER",user_id,request_id,env.get("REMOTE_ADDR"),{"status":body.get("status")}); return self._json(start,{"user":result})
                if action=="roles" and method=="POST": self.security.assign_roles(user_id,body.get("roles") or [],session["uid"]); self.security.audit(session["uid"],"USER_ROLES_CHANGED","USER",user_id,request_id,env.get("REMOTE_ADDR"),{"roles":body.get("roles")}); return self._json(start,{"roles":self.security.roles_for(user_id)})
                if action=="reset-password" and method=="POST": self.security.reset_password(user_id,str(body.get("password") or "")); self.security.audit(session["uid"],"PASSWORD_RESET","USER",user_id,request_id,env.get("REMOTE_ADDR")); return self._json(start,{"success":True})
            if path.startswith("/api/security/sessions/") and path.endswith("/revoke") and method=="POST":
                sid=unquote(path.removeprefix("/api/security/sessions/").removesuffix("/revoke")); row=self.security.db.execute("SELECT user_id FROM security_sessions WHERE session_id=?",(sid,)).fetchone()
                if not row: raise KeyError(sid)
                self.security.revoke_sessions(row["user_id"],sid); self.security.db.commit(); self.security.audit(session["uid"],"SESSION_REVOKED","SESSION",sid,request_id,env.get("REMOTE_ADDR")); return self._json(start,{"revoked":True})
            if path == "/api/stock":
                return self._json(start,{"positions":[self._with_media(asdict(p),p.drcloud_product_key) for p in self.stock.positions()],"statistics":self.service.repo.aggregate_statistics(),"comparison_statistics":self.external_stock.statistics()})
            if path == "/api/stock/comparison":
                return self._json(start,{"comparisons":self.external_stock.comparisons(),"statistics":self.external_stock.statistics()})
            if path == "/api/stock/sync-status":
                return self._json(start,self.external_stock.sync_status())
            if path == "/api/stock/movements":
                q=parse_qs(env.get("QUERY_STRING","")); product=q.get("product",[None])[0]
                movements=self.service.repo.movements_for_product(product) if product else self.service.repo.recent_movements(50)
                return self._json(start,{"movements":[self._stock_movement(m) for m in movements]})
            if path.startswith("/api/stock/products/"):
                key=unquote(path.removeprefix("/api/stock/products/")); position=self.stock.position(key)
                if position is None: return self._json(start,{"error":"Produit sans position de stock"},"404 Not Found")
                comparison=next((r for r in self.external_stock.comparisons() if r["drcloud_product_key"]==key),None)
                return self._json(start,{"position":self._with_media(asdict(position),key,display=True),"observations":comparison,"movements":[self._stock_movement(m) for m in self.service.repo.movements_for_product(key)]})
            if path.startswith("/api/products/") and "/media" in path:
                return self._media_api(path,method,env,start,session)
            if path == "/api/catalogue": return self._json(start,self._catalogue(parse_qs(env.get("QUERY_STRING", ""))))
            if path == "/api/catalogue/quality": return self._json(start,self._catalogue_quality())
            if path.startswith("/api/catalogue/products/") and path.endswith("/commercial"):
                key=unquote(path.removeprefix("/api/catalogue/products/").removesuffix("/commercial"))
                if hasattr(self.os_repository, "reload"):
                    self.os_repository.reload()
                if method == "GET":
                    product=self.os_repository.get(key)
                    if product is None: raise KeyError(key)
                    row=self._with_media(asdict(product),key,display=True); row["display_name"]=product.display_name
                    row["observations"]=self.os_repository.observations(key); row["diagnostics"]=self.os_repository.diagnostics(key)
                    return self._json(start,row)
                if method == "POST":
                    return self._json(start,asdict(self.hydration.update_manual(key,self._body(env),session.get("u","authenticated"))))
            if path.startswith("/api/catalogue/products/") and path.endswith("/status") and method == "POST":
                key=unquote(path.removeprefix("/api/catalogue/products/").removesuffix("/status"))
                product=self.os_repository.set_status(key,ProductStatus(self._body(env)["status"]))
                self.security and self.security.audit(session["uid"],"PRODUCT_STATUS_CHANGED","PRODUCT",key,request_id,"api",{"status":product.status.value})
                return self._json(start,asdict(product))
            if path == "/api/items":
                q=parse_qs(env.get("QUERY_STRING", "")); rows=self.service.search(q.get("q",[""])[0],q.get("view",["ALL"])[0],q.get("without_ean",["0"])[0]=="1"); return self._json(start,[self._with_media(x,x.get("drcloud_product_key") or f"drc:{x.get('prestashop_key')}") for x in rows])
            if path == "/api/scan":
                result=self.service.scan(parse_qs(env.get("QUERY_STRING", "")).get("ean",[""])[0]); result["items"]=[self._with_media(x,x.get("drcloud_product_key") or f"drc:{x.get('prestashop_key')}") for x in result.get("items",[])]; return self._json(start,result)
            if path == "/api/count" and method == "POST":
                data=self._body(env); result=self.service.count(data["prestashop_key"],data.get("physical_quantity"),data.get("source","MANUAL"),data.get("action","COUNT")); self.security and self.security.audit(session["uid"],"INVENTORY_COUNT_RECORDED","INVENTORY",str(data["prestashop_key"]),request_id,"api",{"source":data.get("source","MANUAL")}); return self._json(start,result)
            if path == "/api/barcodes/propose" and method == "POST":
                data=self._body(env); return self._json(start,asdict(self.barcodes.propose(data["drcloud_product_key"],data["ean"])))
            if path == "/api/barcodes/confirm" and method == "POST":
                identifier=str(self._body(env)["id"]); result=self.barcodes.confirm(identifier); self.security and self.security.audit(session["uid"],"BARCODE_ASSIGNMENT_CONFIRMED","BARCODE_ASSIGNMENT",identifier,request_id,"api"); return self._json(start,asdict(result))
            if path == "/api/history": return self._json(start,self.service.repo.history(self.service.session()["id"]))
            if path == "/api/complete" and method == "POST": return self._json(start,self.service.complete())
            if path == "/api/inventory/session" and method == "POST": return self._json(start,self.service.new_session())
            if path == "/api/inventory/proposal": return self._json(start,self.service.proposal())
            if path == "/api/inventory/proposal/validate" and method == "POST":
                result=self.service.validate(session.get("u") or "authenticated"); self.security and self.security.audit(session["uid"],"INVENTORY_VALIDATED","INVENTORY",None,request_id,"api"); return self._json(start,result)
            if path == "/api/inventory/proposal/apply" and method == "POST":
                result=self.service.apply(session.get("u") or "authenticated"); self.security and self.security.audit(session["uid"],"INVENTORY_APPLIED","INVENTORY",None,request_id,"api"); return self._json(start,result)
            if path == "/api/inventory/proposal/validate-and-apply" and method == "POST":
                self.service.validate(session.get("u") or "authenticated")
                return self._json(start,self.service.apply(session.get("u") or "authenticated"))
            if path == "/api/report": return self._json(start,self.service.report(self.report_output))
            if path == "/api/export.csv": return self._send(start,self.service.csv().encode(),"text/csv; charset=utf-8",headers=[("Content-Disposition","attachment; filename=inventaire-drcloud.csv")])
            return self._error(start,404,request_id)
        except PermissionError: return self._error(start,403,request_id)
        except KeyError as exc: return self._json(start,{"error":str(exc).strip(chr(39))},"404 Not Found")
        except RehydrationConflict as exc: return self._json(start,{"error":str(exc)},"409 Conflict")
        except (InventoryError,BarcodeError,MediaError,CreativeGenerationError,CreativeReviewError,ValueError,json.JSONDecodeError) as exc: return self._json(start,{"error":str(exc)},"400 Bad Request")
        except Exception:
            LOG.exception("request_failed request_id=%s path=%s",request_id,path); return self._error(start,500,request_id)

    def _login(self,env,start,method,session,request_id):
        if not self.settings: return self._error(start,401,request_id)
        if method=="GET": return self._html(start,"login.html",None,request_id)
        remote=env.get("REMOTE_ADDR",""); now=time.monotonic(); attempts=[x for x in self.failures.get(remote,[]) if now-x<300]; self.failures[remote]=attempts
        if len(attempts)>=5: return self._error(start,429,request_id)
        data=parse_qs(self._raw_body(env).decode("utf-8")); user=data.get("username",[""])[0]; password=data.get("password",[""])[0]
        identity=self.security.authenticate(user,password) if self.security else None
        if not identity:
            attempts.append(now); LOG.warning("login_failed request_id=%s remote=%s",request_id,remote)
            self.security.audit(None,"LOGIN_FAILED","USER",None,request_id,remote,{"username":user},False)
            return self._html(start,"login.html",None,request_id,status="401 Unauthorized")
        self.failures.pop(remote,None); self.security.mark_login(identity["user_id"]); credential=self.security.credential(identity["user_id"]); expires=int(time.time())+28800
        expires_iso=datetime.fromtimestamp(expires,timezone.utc).isoformat(); sid=self.security.create_session(identity["user_id"],expires_iso,remote)
        token={"u":identity["username"],"uid":identity["user_id"],"sid":sid,"exp":expires,"csrf":secrets.token_urlsafe(24),"sv":credential.session_version}; cookie=self._encode(token)
        self.security.audit(identity["user_id"],"LOGIN_SUCCEEDED","SESSION",sid,request_id,remote)
        return self._redirect(start,"/",request_id,cookie=cookie)

    def _change_password(self,env,start,session,request_id):
        remote=env.get("REMOTE_ADDR",""); now=time.monotonic()
        attempts=[x for x in self.password_failures.get(remote,[]) if now-x<300]
        self.password_failures[remote]=attempts
        if len(attempts)>=5: return self._error(start,429,request_id)
        data=self._body(env); current=data.get("current_password",""); new=data.get("new_password","")
        if new != data.get("new_password_confirmation",""):
            return self._json(start,{"error":"La confirmation ne correspond pas."},"400 Bad Request")
        if hmac.compare_digest(current,new):
            return self._json(start,{"error":"Le nouveau mot de passe doit être différent."},"400 Bad Request")
        try: self.security.change_password(session["uid"],current,new,session.get("u"))
        except PermissionError:
            attempts.append(now)
            return self._json(start,{"error":"Le mot de passe actuel est incorrect."},"400 Bad Request")
        self.password_failures.pop(remote,None)
        self.security.audit(session["uid"],"PASSWORD_CHANGED","USER",session["uid"],request_id,remote)
        secure=self.settings.environment=="production"; attrs="; Path=/; HttpOnly; SameSite=Lax"+("; Secure" if secure else "")
        return self._json(start,{"success":True,"reauthentication_required":True},headers=[("Set-Cookie",f"drcloud_session={attrs}; Max-Age=0"),("X-Request-ID",request_id)])

    def _session(self,env):
        if not self.settings: return {"u":"legacy-test","csrf":"test"}
        cookies=dict(x.strip().split("=",1) for x in env.get("HTTP_COOKIE","").split(";") if "=" in x)
        raw=cookies.get("drcloud_session");
        if not raw: return None
        try:
            payload,sig=raw.rsplit(".",1); expected=hmac.new(self.settings.secret_key.encode(),payload.encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig,expected): return None
            data=json.loads(bytes.fromhex(payload)); credential=self.security.credential(data["uid"])
            return data if data["exp"]>time.time() and credential and data.get("sv")==credential.session_version and self.security.valid_session(data["sid"],data["uid"],data["sv"]) else None
        except (ValueError,KeyError,json.JSONDecodeError): return None
    def _encode(self,data):
        payload=json.dumps(data,separators=(",",":")).encode().hex(); return payload+"."+hmac.new(self.settings.secret_key.encode(),payload.encode(),hashlib.sha256).hexdigest()
    @staticmethod
    def _route_permission(path,method):
        """Return the explicit grant required by a route; unknown routes fail closed."""
        pages={"/":"catalogue.read","/crm":"crm.read","/crm/customers":"crm.customer.read","/crm/loyalty":"crm.loyalty.read","/catalogue":"catalogue.read","/inventaire":"stock.read","/stock":"stock.read","/sales":"sales.read","/finance":"finance.read","/settlements":"settlements.read","/settlements/explorer":"settlements.read","/achats":"purchasing.read","/marketing":"marketing.read","/marketing/calendar":"marketing.calendar.read","/marketing/publishing-queue":"marketing.calendar.read","/marketing/review":"marketing.review","/marketing/campaigns":"marketing.campaigns.read","/marketing/social-analytics":"marketing.analytics.read","/marketing/learning":"marketing.learning.read","/administration":"admin.read","/securite":"security.read","/roadmap":"admin.read"}
        if path in pages: return pages[path]
        if path.startswith("/achats/"): return "purchasing.read"
        if path.startswith("/media/"): return "catalogue.read"
        if path.startswith("/api/security/settings"): return "settings.read" if method=="GET" else "settings.write"
        if path.startswith("/api/security/audits"): return "security.read"
        if path.startswith("/api/security/secret-references"): return "security.manage_secrets"
        if path.startswith("/api/security/change-password"): return "security.read"
        if path.startswith("/api/security/users") or path.startswith("/api/security/sessions/"): return "security.manage_users" if method!="GET" else "security.read"
        if path.startswith("/api/security/"): return "security.read"
        if path.startswith("/api/settlements/"):
            if method == "GET": return "settlements.read"
            if path.endswith(("/note","/notes")): return "settlements.notes"
            return "settlements.backfill" if path.endswith(("/backfill","/recompute")) else "settlements.review"
        if path.startswith("/api/crm/customers"): return "crm.customer.read" if method=="GET" else "crm.customer.write"
        if path.startswith("/api/crm"): return "crm.read"
        domains=(("/api/purchasing","purchasing.cost.read" if method=="GET" else "purchasing.cost.validate"),("/api/finance","finance.read" if method=="GET" else "finance.write"),("/api/data-hub/sync-all","admin.write"),("/api/data-hub/sources/","admin.write"),("/api/data-hub/jobs/","bank.sync"),("/api/data-hub","admin.read"),("/api/sales","sales.read" if method=="GET" else "sales.sync"),("/api/marketing","marketing.read" if method=="GET" else "marketing.approve"),("/api/purchase-orders","purchasing.read" if method=="GET" else "purchasing.write"),("/api/goods-receipts","purchasing.read" if method=="GET" else "purchasing.write"),("/api/suppliers","purchasing.read" if method=="GET" else "purchasing.write"),("/api/admin","admin.read" if method=="GET" else "admin.write"),("/api/roadmap","admin.read"),("/api/stock","stock.read"),("/api/inventory","stock.read" if method=="GET" else "stock.validate"),("/api/count","stock.write"),("/api/complete","stock.validate"),("/api/barcodes","catalogue.write"),("/api/products","catalogue.read" if method=="GET" else "catalogue.write"),("/api/catalogue","catalogue.read"),("/api/search","catalogue.read"),("/api/scan","catalogue.read"),("/api/history","stock.read"),("/api/report","stock.read"),("/api/export.csv","stock.read"),("/api/state","stock.read"),("/api/dashboard","catalogue.read"))
        for prefix,permission in domains:
            if path.startswith(prefix): return permission
        return "__default_deny__" if path.startswith("/api/") else "__not_found__"
    def _csrf(self,env,session):
        if not self.settings: return
        token=env.get("HTTP_X_CSRF_TOKEN") or parse_qs(self._raw_body(env).decode(errors="ignore")).get("csrf_token",[""])[0]
        if not hmac.compare_digest(token,session["csrf"]): raise PermissionError("CSRF")
    @staticmethod
    def _raw_body(env):
        if "drcloud.raw_body" not in env: env["drcloud.raw_body"]=env["wsgi.input"].read(min(int(env.get("CONTENT_LENGTH") or 0),MAX_FILE_SIZE+1))
        return env["drcloud.raw_body"]
    def _body(self,env): return json.loads(self._raw_body(env) or b"{}")
    def _html(self,start,name,session,request_id,status="200 OK"):
        content_name="inventory.html" if name == "catalogue.html" else name
        html=(ROOT/content_name).read_text(encoding="utf-8"); safe=self.settings.safe_mode if self.settings else True
        shell_module=PAGES.get("settlements.html") if name=="settlement-explorer.html" else PAGES.get("marketing.html") if name=="marketing-operations.html" else PAGES.get(name)
        if shell_module:
            module = shell_module
            title, active, script = ("Settlement Explorer",module.id,"settlement-explorer.js") if name=="settlement-explorer.html" else ("Opérations marketing",module.id,"marketing-operations.js") if name=="marketing-operations.html" else (module.label,module.id,module.script)
            shell=(ROOT/"app-shell.html").read_text(encoding="utf-8")
            html=shell.replace("{{PAGE_CONTENT}}",html).replace("{{PAGE_TITLE}}",title).replace("{{PAGE_SCRIPT}}",script)
            html=html.replace("{{NAVIGATION}}", render_navigation(active))
        html=html.replace("{{SAFE_BANNER}}",'<div class="safe">Mode sécurisé — écritures externes désactivées</div>' if safe else "").replace("{{CSRF}}",session["csrf"] if session else "")
        return self._send(start,html.encode(),"text/html; charset=utf-8",status,[("X-Request-ID",request_id)])
    def _catalogue(self,query):
        # Rehydration runs in an isolated worker repository.  Catalogue responses
        # must therefore reload committed rows instead of serving the startup copy.
        if hasattr(self.os_repository, "reload"):
            self.os_repository.reload()
        text=query.get("q",[""])[0].casefold(); selected=query.get("filter",["ALL"])[0]
        allowed={"ALL","INCOMPLETE","WITH_EAN","WITHOUT_EAN","CONFLICT","WITH_IMAGE","WITHOUT_IMAGE","UNKNOWN_VARIANT"}
        if selected not in allowed: raise ValueError("Filtre catalogue invalide")
        products=self.os_repository.all()
        ean_groups={}
        for product in products:
            if product.ean: ean_groups.setdefault(product.ean,[]).append(product.drcloud_product_key)
        conflicts={key for keys in ean_groups.values() if len(keys)>1 for key in keys}
        counts=self.service.repo.counts(self.service.session()["id"])
        primaries=self.media.repository.primaries()
        variants=self.media.repository.variants_for([media.media_id for media in primaries.values()])
        diagnostics=self.os_repository.all_diagnostics() if hasattr(self.os_repository,"all_diagnostics") else None
        rows=[]
        for p in products:
            if text and text not in f"{p.base_name} {p.variant_name} {p.display_name} {p.attributes} {p.ean} {p.reference} {p.prestashop_key} {p.shopcaisse_item_id}".casefold(): continue
            primary=primaries.get(p.drcloud_product_key); missing_variant=bool(p.combination_id) and not p.variant_name
            incomplete=missing_variant or not p.reference or not p.ean or not primary
            if (selected=="WITH_EAN" and not p.ean) or (selected=="WITHOUT_EAN" and p.ean) or (selected=="CONFLICT" and p.drcloud_product_key not in conflicts) or (selected=="INCOMPLETE" and not incomplete) or (selected=="WITH_IMAGE" and not primary) or (selected=="WITHOUT_IMAGE" and primary) or (selected=="UNKNOWN_VARIANT" and not missing_variant): continue
            row=asdict(p); row["display_name"]=p.display_name; problems=[]
            if p.drcloud_product_key in conflicts: problems.append("EAN dupliqué")
            product_diagnostics=diagnostics.get(p.drcloud_product_key,[]) if diagnostics is not None else self.os_repository.diagnostics(p.drcloud_product_key)
            problems.extend(x["reason"] for x in product_diagnostics)
            if not p.variant_name and p.combination_id: problems.append("Variante inconnue")
            if not p.reference: problems.append("Référence absente")
            if not p.ean: problems.append("EAN absent")
            if not primary: problems.append("Image absente")
            row["coherence"]="INCOHÉRENT" if any("conflit" in x.casefold() or "diverg" in x.casefold() for x in problems) else "ATTENTION" if problems else "OK"
            row["coherence_issues"]=problems
            row["diagnostics"]=problems
            row["primary_media"]=self._media_json(primary,variants) if primary else None
            row["ean_status"]="CONFLICT" if p.drcloud_product_key in conflicts else "WITH_EAN" if p.ean else "WITHOUT_EAN"; count=counts.get(p.prestashop_key); row["physical_quantity"]=count["physical_quantity"] if count else None; rows.append(row)
            self._with_media(row,p.drcloud_product_key,primary=primary,variants=variants)
        return rows
    def _catalogue_quality(self):
        if hasattr(self.os_repository, "reload"):
            self.os_repository.reload()
        products=self.os_repository.all(); total=len(products); pictured=sum(bool(self.media.primary(p.drcloud_product_key)) for p in products)
        conflicts=sum(bool(self.os_repository.diagnostics(p.drcloud_product_key)) for p in products)
        complete=sum(bool((not p.combination_id or p.variant_name) and p.reference and p.ean
                          and self.media.primary(p.drcloud_product_key)) for p in products)
        return {"total":total,"with_variant":sum(bool(p.variant_name) for p in products),"missing_variant":sum(bool(p.combination_id) and not p.variant_name for p in products),"with_ean":sum(bool(p.ean) for p in products),"without_ean":sum(not p.ean for p in products),"ean_conflict":conflicts,"with_image":pictured,"without_image":total-pictured,"complete":complete}
    def _media_url(self,media,kind,variants=None):
        if not media: return None
        if variants is None: return self.media.url(media,kind)
        variant=variants.get((media.media_id,kind))
        return f"/media/{media.media_id}/{kind.value.lower()}?v={variant.sha256[:16]}" if variant else None
    def _with_media(self,row,key,display=False,primary=None,variants=None):
        media=primary if variants is not None else self.media.primary(key); kind=MediaVariantKind.DISPLAY if display else MediaVariantKind.THUMBNAIL
        row["media_url"]=self._media_url(media,kind,variants); row["media_width"]=(media.width if media else None); row["media_height"]=(media.height if media else None); row["media_status"]="AVAILABLE" if media else "MISSING"; return row
    def _media_json(self,media,variants=None):
        value=asdict(media); value.pop("storage_reference",None); value["url"]=f"/media/{media.media_id}/original?v={media.sha256[:16]}"; value["thumbnail_url"]=self._media_url(media,MediaVariantKind.THUMBNAIL,variants); value["display_url"]=self._media_url(media,MediaVariantKind.DISPLAY,variants); return value
    def _media_api(self,path,method,env,start,session):
        parts=path.removeprefix("/api/products/").split("/"); key=unquote(parts[0])
        if len(parts)==2 and parts[1]=="media" and method=="GET": return self._json(start,{"media":[self._media_json(x) for x in self.media.repository.list(key)]})
        if len(parts)==2 and parts[1]=="media" and method=="POST":
            source=env.get("HTTP_X_MEDIA_SOURCE","MANUAL_UPLOAD"); role=env.get("HTTP_X_MEDIA_ROLE","PRIMARY")
            media=self.media.add(key,self._raw_body(env),filename=unquote(env.get("HTTP_X_FILENAME","upload")),source=source,role=role,actor=session.get("u","authenticated"))
            return self._json(start,{"media":self._media_json(media)},"201 Created")
        if len(parts)==4 and parts[1]=="media" and method=="POST":
            media_id=unquote(parts[2]); action=parts[3]
            if action=="primary": self.media.make_primary(key,media_id,session.get("u","authenticated"))
            elif action=="disable": self.media.disable(key,media_id,session.get("u","authenticated"))
            else: raise KeyError(action)
            return self._json(start,{"media":self._media_json(self.media.repository.get(media_id))})
        raise KeyError(path)
    def _serve_media(self,path,start,request_id,head=False):
        parts=path.split("?")[0].strip("/").split("/")
        if len(parts)!=3 or parts[0]!="media": raise KeyError(path)
        media_id=unquote(parts[1]); kind=MediaVariantKind(parts[2].upper()); media=self.media.repository.get(media_id)
        if not media: raise KeyError(media_id)
        variant=self.media.repository.variant(media_id,kind)
        if kind is MediaVariantKind.ORIGINAL:
            storage_reference,mime_type=media.storage_reference,media.mime_type
        elif variant:
            storage_reference,mime_type=variant.storage_reference,variant.mime_type
        else: raise KeyError(kind)
        content=self.media.storage.read(storage_reference)
        headers=[("X-Request-ID",request_id),("Content-Length",str(len(content))),("Cache-Control","public, max-age=31536000, immutable")]
        return self._send(start,b"" if head else content,mime_type,headers=headers,cache=False)
    @staticmethod
    def _stock_movement(movement):
        labels={"INVENTORY":"Inventaire","LEGACY":"Historique","GOODS_RECEIPT":"Réception fournisseur"}
        return {"id":movement.id,"product_id":movement.drcloud_product_key,"delta":movement.quantity_delta,
          "type":movement.movement_type.value,"origin":labels.get(movement.source_type,movement.source_type.title()),
          "source_reference":movement.source_id,"occurred_at":movement.applied_at or movement.created_at}
    def _purchase_order(self,order):
        value=asdict(order); lines=self.purchase_orders.lines(order.purchase_order_id)
        value.update(lines=[self._with_media(asdict(x),x.product_key) for x in lines],line_count=len(lines),total=self.purchase_orders.total(order.purchase_order_id),cost_complete=bool(lines) and all(x.unit_cost is not None for x in lines))
        return value
    def _goods_receipt(self,receipt):
        value=asdict(receipt); lines=self.goods_receipts.repository.lines(receipt.receipt_id)
        order=self.purchase_orders.get(receipt.purchase_order_id); supplier=self.suppliers.get(order.supplier_id) if order else None
        value.update(line_count=len(lines),units_received=sum(x.received_quantity for x in lines),purchase_order_reference=order.reference if order else receipt.purchase_order_id,supplier_name=supplier.name if supplier else "Fournisseur archivé")
        return value
    def _error(self,start,code,request_id): return self._html(start,"error.html",{"csrf":""},request_id,status={401:"401 Unauthorized",403:"403 Forbidden",404:"404 Not Found",429:"429 Too Many Requests",500:"500 Internal Server Error"}[code])
    def _redirect(self,start,location,request_id,cookie=None,clear=False):
        headers=[("Location",location),("X-Request-ID",request_id)]; secure=self.settings and self.settings.environment=="production"; attrs="; Path=/; HttpOnly; SameSite=Lax"+("; Secure" if secure else "")
        if cookie: headers.append(("Set-Cookie",f"drcloud_session={cookie}{attrs}; Max-Age=28800"))
        if clear: headers.append(("Set-Cookie",f"drcloud_session={attrs}; Max-Age=0"))
        return self._send(start,b"","text/plain","303 See Other",headers)
    @staticmethod
    def _send(start,body,kind,status="200 OK",headers=None,cache=True):
        security=[("Content-Type",kind)]+([("Cache-Control","no-store")] if cache else [])+[("Content-Security-Policy","default-src 'self'; img-src 'self' data:; media-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"),("X-Content-Type-Options","nosniff"),("Referrer-Policy","no-referrer"),("Permissions-Policy","camera=(self), microphone=(), geolocation=(), payment=(), usb=()"),("X-Frame-Options","DENY")]
        start(status,security+(headers or [])); return [body]
    def _json(self,start,value,status="200 OK",headers=None): return self._send(start,json.dumps(value,ensure_ascii=False,default=str).encode(),"application/json; charset=utf-8",status,headers)

def create_app(settings: OSSettings | None=None):
    settings=settings or OSSettings.from_env(); settings.data_dir.mkdir(parents=True,exist_ok=True); settings.data_dir.chmod(0o700)
    catalogue=Path(os.environ.get("INVENTORY_CATALOGUE",settings.data_dir/"catalogue.json")); report=Path(os.environ.get("INVENTORY_MAPPING_REPORT",settings.data_dir/"catalogue-report.json"))
    app=InventoryApp(InventoryService(catalogue,report,InventoryRepository(settings.database)),settings.data_dir/"rapport-inventaire.json",settings=settings)
    if settings.database.exists(): settings.database.chmod(0o600)
    return app

def serve(catalogue:Path,validation:Path,database:Path,host="127.0.0.1",port=8080):
    from waitress import serve as waitress_serve
    service=InventoryService(catalogue,validation,InventoryRepository(database)); waitress_serve(InventoryApp(service),host=host,port=port)
