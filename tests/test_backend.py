from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db
from backend.domain import advance_day, money_to_minor, parse_transaction_text, projected_dates
from backend.main import app


class BudgetApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.temp.name) / "test.sqlite3"
        db.DATA_DIR = Path(self.temp.name)
        db.init_db()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.temp.cleanup()

    def transaction(self, **overrides):
        payload = {"transaction_type":"expense","amount_minor":2250,"currency":"AUD","description":"Lunch","category":"Dining","transaction_date":"2026-08-10","note":None}
        payload.update(overrides); return payload

    def recurring(self, **overrides):
        payload = {"transaction_type":"bill","amount_minor":8000,"currency":"AUD","description":"Phone","category":"Phone/Internet","frequency":"monthly","start_date":"2026-08-15","next_due_date":"2026-08-15","end_date":None,"active":True,"automated_externally":True,"note":None}
        payload.update(overrides); return payload

    def test_transaction_create_edit_delete_and_idempotency(self):
        first=self.client.post('/api/transactions',json=self.transaction(),headers={'Idempotency-Key':'one'}); self.assertEqual(first.status_code,201)
        duplicate=self.client.post('/api/transactions',json=self.transaction(amount_minor=9999),headers={'Idempotency-Key':'one'}); self.assertEqual(duplicate.json()['amount_minor'],2250)
        item=first.json(); changed=self.client.put(f"/api/transactions/{item['id']}",json=self.transaction(amount_minor=2301)); self.assertEqual(changed.json()['amount_minor'],2301)
        self.assertEqual(self.client.delete(f"/api/transactions/{item['id']}").status_code,204)
        self.assertEqual(self.client.get('/api/transactions').json(),[])

    def test_money_precision(self):
        self.assertEqual(money_to_minor('83.47'),8347); self.assertEqual(money_to_minor('0.005'),1)
        with self.assertRaises(ValueError): money_to_minor('nan')

    def test_api_validation_rejects_bad_inputs(self):
        response=self.client.post('/api/transactions',json=self.transaction(amount_minor=-1,description=' '))
        self.assertEqual(response.status_code,422); self.assertIn('errors',response.json())

    def test_recurrence_frequencies_and_month_end(self):
        self.assertEqual(advance_day(date(2026,1,1),'weekly'),date(2026,1,8))
        self.assertEqual(advance_day(date(2026,1,1),'fortnightly'),date(2026,1,15))
        self.assertEqual(advance_day(date(2026,1,31),'monthly'),date(2026,2,28))
        self.assertEqual(advance_day(date(2024,2,29),'yearly'),date(2025,2,28))
        days=list(projected_dates(date(2026,1,30),'monthly',date(2026,2,1),date(2026,4,30)))
        self.assertEqual(days,[date(2026,2,28),date(2026,3,30),date(2026,4,30)])

    def test_weekly_and_fortnightly_projection_api(self):
        self.client.post('/api/recurring',json=self.recurring(frequency='weekly',start_date='2026-08-01',next_due_date='2026-08-01'))
        self.client.post('/api/recurring',json=self.recurring(description='Fortnight',frequency='fortnightly',start_date='2026-08-01',next_due_date='2026-08-01'))
        items=self.client.get('/api/upcoming?from_date=2026-08-01&days=28').json()
        self.assertEqual(sum(i['description']=='Phone' for i in items),5); self.assertEqual(sum(i['description']=='Fortnight' for i in items),3)

    def test_yearly_leap_projection(self):
        self.client.post('/api/recurring',json=self.recurring(frequency='yearly',start_date='2024-02-29',next_due_date='2024-02-29'))
        items=self.client.get('/api/upcoming?from_date=2025-02-01&days=30').json()
        self.assertEqual(items[0]['due_date'],'2025-02-28')

    def test_planned_vs_actual_has_no_double_counting(self):
        rule=self.client.post('/api/recurring',json=self.recurring(amount_minor=8000)).json()
        before=self.client.get('/api/summary/2026-08').json(); self.assertEqual(before['bills_remaining'],8000)
        recorded=self.client.post(f"/api/occurrences/{rule['id']}/2026-08-15/record",json={}); self.assertEqual(recorded.status_code,201)
        again=self.client.post(f"/api/occurrences/{rule['id']}/2026-08-15/record",json={}); self.assertEqual(again.json()['id'],recorded.json()['id'])
        after=self.client.get('/api/summary/2026-08').json(); self.assertEqual(after['actual_bills'],8000); self.assertEqual(after['bills_remaining'],0); self.assertEqual(after['expected_remaining'],-8000)
        self.assertEqual(self.client.delete(f"/api/transactions/{recorded.json()['id']}").status_code,204)
        self.assertEqual(self.client.get('/api/summary/2026-08').json()['bills_remaining'],8000)

    def test_projected_cashflow_formula(self):
        self.client.post('/api/transactions',json=self.transaction(transaction_type='income',amount_minor=300000,description='Pay',category='Salary'))
        self.client.post('/api/transactions',json=self.transaction(amount_minor=50000))
        self.client.post('/api/transactions',json=self.transaction(transaction_type='savings',amount_minor=20000,description='Saved',category='Savings'))
        self.client.post('/api/recurring',json=self.recurring(transaction_type='income',amount_minor=100000,description='Next pay',category='Salary'))
        self.client.post('/api/recurring',json=self.recurring(amount_minor=30000))
        self.client.post('/api/recurring',json=self.recurring(transaction_type='savings',amount_minor=10000,description='Save',category='Savings'))
        summary=self.client.get('/api/summary/2026-08').json(); self.assertEqual(summary['expected_remaining'],290000)

    def test_skip_removes_planned_occurrence(self):
        rule=self.client.post('/api/recurring',json=self.recurring()).json()
        self.client.post(f"/api/occurrences/{rule['id']}/2026-08-15/skip")
        self.assertEqual(self.client.get('/api/summary/2026-08').json()['bills_remaining'],0)

    def test_parser_representative_phrases(self):
        cases=[('I spent $22.50 on lunch','expense',2250,'Dining'),('I bought groceries for $87.20','expense',8720,'Groceries'),('I paid electricity $123.45','bill',12345,'Utilities'),('I got paid $2400','income',240000,'Other Income'),('I put $150 into savings','savings',15000,'Savings'),('I purchased fuel and it cost eighty-three dollars forty-seven','expense',8347,'Fuel')]
        for phrase,kind,amount,category in cases:
            with self.subTest(phrase=phrase):
                parsed=parse_transaction_text(phrase); self.assertEqual((parsed['transaction_type'],parsed['amount_minor'],parsed['category']),(kind,amount,category))

    def test_parser_never_invents_amount(self):
        parsed=parse_transaction_text('I bought something nice'); self.assertIsNone(parsed['amount_minor']); self.assertTrue(parsed['needs_review'])
        self.assertEqual(self.client.post('/api/parse',json={'text':''}).status_code,422)

    def test_export_and_duplicate_restore_protection(self):
        self.client.post('/api/transactions',json=self.transaction())
        csv_response=self.client.get('/api/export.csv'); self.assertIn('amount_minor',csv_response.text); self.assertIn('2250',csv_response.text)
        backup=self.client.get('/api/export.json').json(); self.assertEqual(backup['version'],1); self.assertEqual(len(backup['transactions']),1)
        restored=self.client.post('/api/restore?confirm=true',json=backup); self.assertEqual(restored.status_code,200)
        duplicate=self.client.post('/api/restore?confirm=true',json=backup); self.assertEqual(duplicate.status_code,409)


if __name__ == '__main__': unittest.main()
