from decimal import Decimal
from dr_cloud_sync.purchase_cost import PurchaseCostLedger


def ledger(tmp_path):
    return PurchaseCostLedger(tmp_path/'cost.sqlite')


def add_lot(x, product, line, qty, cost, date):
    return x.record_receipt_cost(product_key=product,supplier_id='sup:1',quantity=qty,
        received_at=date,unit_cost_ht=cost,receipt_line_id=line,status='CONFIRMED')


def test_fifo_exact_and_partial_coverage(tmp_path):
    x=ledger(tmp_path);add_lot(x,'drc:p','grl:1',10,3,'2026-01-01T00:00:00+00:00');add_lot(x,'drc:p','grl:2',10,4,'2026-01-02T00:00:00+00:00')
    result=x.allocate_sale('sale:1','drc:p',12)
    assert result['total_cost']=='38.00'; assert result['coverage_percent']=='100.00'
    partial=x.allocate_sale('sale:2','drc:p',10)
    assert partial['covered_quantity']=='8.00'; assert partial['uncovered_quantity']=='2.00'; assert partial['coverage_percent']=='80.00'


def test_no_confirmed_cost_is_invented_and_idempotency(tmp_path):
    x=ledger(tmp_path)
    event=x.record_receipt_cost(product_key='drc:p',supplier_id='sup:1',quantity=5,received_at='2026-01-01T00:00:00+00:00',unit_cost_ht=None,receipt_line_id='grl:1',status='INCOMPLETE')
    replay=x.record_receipt_cost(product_key='drc:p',supplier_id='sup:1',quantity=5,received_at='2026-01-01T00:00:00+00:00',unit_cost_ht=None,receipt_line_id='grl:1',status='INCOMPLETE')
    assert event['cost_event_id']==replay['cost_event_id']; assert x.stock_value()['value_ht']=='0.00'
    result=x.allocate_sale('sale:old','drc:p',3); assert result['total_cost']=='0.00'; assert result['coverage_percent']=='0.00'


def test_invoice_preview_apply_controls_mapping_and_duplicate(tmp_path):
    x=ledger(tmp_path);x.map_product('sup:1','REF','drc:p')
    csv='supplier_id,invoice_number,invoice_date,description,supplier_reference,quantity,unit_price_ht,tax_amount,total_ht,total_tax,total_ttc,currency\nsup:1,F-1,2026-01-01,Produit,REF,2,10,4,20,4,24,EUR\n'
    preview=x.preview_csv(csv); assert preview['mutated'] is False and preview['matched']==1
    applied=x.apply_csv(csv,preview['preview_id']); assert applied['created']==1
    invoice=x.invoice(applied['invoice_ids'][0]); assert invoice['status']=='DRAFT'
    assert x.control_invoice(invoice['invoice_id'])['status']=='MATCHED'
    assert x.validate_invoice(invoice['invoice_id'])['status']=='VALIDATED'
    assert x.preview_csv(csv)['duplicates']==1


def test_invoice_divergence_not_corrected(tmp_path):
    x=ledger(tmp_path);x.map_product('sup:1','REF','drc:p')
    inv=x.create_invoice({'supplier_id':'sup:1','invoice_number':'bad','invoice_date':'2026-01-01','total_ht':'20','total_tax':'4','total_ttc':'25'},[{'description':'p','supplier_reference':'REF','quantity':2,'unit_price_ht':10,'total_ht':20,'tax_amount':4,'total_ttc':24}])
    assert x.control_invoice(inv['invoice_id'])['status']=='TOTAL_DIFFERENCE'
    try:x.validate_invoice(inv['invoice_id'])
    except ValueError:pass
    else:assert False


def test_stock_value_never_values_uncovered_quantity(tmp_path):
    x=ledger(tmp_path);add_lot(x,'drc:p','grl:1',5,3,'2026-01-01T00:00:00+00:00')
    value=x.stock_value({'drc:p':8}); assert value['value_ht']=='15.00'; assert value['uncovered_quantity']=='3.00'; assert value['products'][0]['coverage_percent']=='62.50'
