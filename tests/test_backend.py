from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db
from backend.domain import advance_day, daily_simple_interest_minor, money_to_minor, parse_transaction_text, projected_dates
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
        payload = {"transaction_type":"bill","amount_minor":8000,"currency":"AUD","description":"Phone","category":"Phone/Internet","frequency":"monthly","interval_count":1,"start_date":"2026-08-15","next_due_date":"2026-08-15","end_date":None,"active":True,"automated_externally":True,"note":None}
        payload.update(overrides); return payload

    def facility(self, **overrides):
        payload = {"name":"Main card","facility_type":"credit","credit_limit_minor":800000,"amount_owed_minor":0,"currency":"AUD","note":None}
        payload.update(overrides); return payload

    def fixed_loan(self, **overrides):
        payload = {"name":"Car loan","facility_type":"fixed_loan","credit_limit_minor":None,"amount_owed_minor":500000,"annual_rate_basis_points":825,"balance_as_of_date":"2026-08-01","linked_recurring_rule_id":None,"currency":"AUD","note":None}
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
        self.assertEqual(response.json()['errors'][0]['message'],'Enter an amount greater than zero')

    def test_quick_add_all_four_transaction_types(self):
        cases=[('expense','Other',-1),('income','Salary',1),('bill','Utilities',-1),('savings','Savings',-1)]
        for index,(kind,category,_sign) in enumerate(cases):
            response=self.client.post('/api/transactions',json=self.transaction(transaction_type=kind,description=f'Quick {kind}',category=category,payment_method='debit' if kind=='expense' else None),headers={'Idempotency-Key':f'quick-{index}'})
            self.assertEqual(response.status_code,201,response.text)
        summary=self.client.get('/api/summary/2026-08').json()
        self.assertEqual((summary['actual_expenses'],summary['actual_income'],summary['actual_bills'],summary['actual_savings']),(2250,2250,2250,2250))
        self.assertEqual(len(self.client.get('/api/transactions?month=2026-08').json()),4)

    def test_recurrence_frequencies_and_month_end(self):
        self.assertEqual(advance_day(date(2026,1,1),'weekly'),date(2026,1,8))
        self.assertEqual(advance_day(date(2026,1,1),'fortnightly'),date(2026,1,15))
        self.assertEqual(advance_day(date(2026,1,31),'monthly'),date(2026,2,28))
        self.assertEqual(advance_day(date(2024,2,29),'yearly'),date(2025,2,28))
        days=list(projected_dates(date(2026,1,30),'monthly',date(2026,2,1),date(2026,4,30)))
        self.assertEqual(days,[date(2026,2,28),date(2026,3,30),date(2026,4,30)])
        self.assertEqual(advance_day(date(2026,1,31),'monthly',2),date(2026,3,31))
        for interval, expected in ((2,[date(2026,1,31),date(2026,3,31),date(2026,5,31)]),(3,[date(2026,1,31),date(2026,4,30)]),(4,[date(2026,1,31),date(2026,5,31)])):
            with self.subTest(interval=interval):
                actual=list(projected_dates(date(2026,1,31),'monthly',date(2026,1,1),date(2026,5,31),interval_count=interval))
                self.assertEqual(actual,expected)

    def test_weekly_and_fortnightly_projection_api(self):
        self.client.post('/api/recurring',json=self.recurring(frequency='weekly',start_date='2026-08-01',next_due_date='2026-08-01'))
        self.client.post('/api/recurring',json=self.recurring(description='Fortnight',frequency='fortnightly',start_date='2026-08-01',next_due_date='2026-08-01'))
        items=self.client.get('/api/upcoming?from_date=2026-08-01&days=28').json()
        self.assertEqual(sum(i['description']=='Phone' for i in items),5); self.assertEqual(sum(i['description']=='Fortnight' for i in items),3)

    def test_custom_recurring_interval_projects_and_validates(self):
        created=self.client.post('/api/recurring',json=self.recurring(description='Quarterly subscription',interval_count=3,start_date='2026-01-31',next_due_date='2026-01-31'))
        self.assertEqual(created.status_code,201,created.text); self.assertEqual(created.json()['interval_count'],3)
        items=self.client.get('/api/upcoming?from_date=2026-01-01&days=150').json()
        self.assertEqual([item['due_date'] for item in items if item['description']=='Quarterly subscription'],['2026-01-31','2026-04-30'])
        self.assertEqual(self.client.post('/api/recurring',json=self.recurring(interval_count=0)).status_code,422)
        self.assertEqual(self.client.post('/api/recurring',json=self.recurring(interval_count=121)).status_code,422)

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

    def test_calendar_projection_data(self):
        self.client.post('/api/recurring',json=self.recurring(transaction_type='income',description='Salary',category='Salary'))
        self.client.post('/api/recurring',json=self.recurring(transaction_type='savings',description='Save',category='Savings',next_due_date='2026-08-20',start_date='2026-08-20'))
        response=self.client.get('/api/calendar/2026-08'); self.assertEqual(response.status_code,200)
        self.assertEqual({item['transaction_type'] for item in response.json()['items']},{'income','savings'})

    def test_credit_facility_creation_and_available_credit(self):
        created=self.client.post('/api/credit-facilities',json=self.facility(amount_owed_minor=600000))
        self.assertEqual(created.status_code,201); self.assertEqual(created.json()['available_credit_minor'],200000)
        invalid=self.client.post('/api/credit-facilities',json=self.facility(name='Too high',amount_owed_minor=900000))
        self.assertEqual(invalid.status_code,422)

    def test_cash_debit_credit_and_pay_later_expenses(self):
        card=self.client.post('/api/credit-facilities',json=self.facility()).json()
        pay_later=self.client.post('/api/credit-facilities',json=self.facility(name='Flexible payments',facility_type='pay_later',credit_limit_minor=100000)).json()
        self.client.post('/api/transactions',json=self.transaction(description='Cash',payment_method='cash'))
        self.client.post('/api/transactions',json=self.transaction(description='Debit',payment_method='debit'))
        credit=self.client.post('/api/transactions',json=self.transaction(description='Credit groceries',amount_minor=12000,payment_method='credit',credit_facility_id=card['id']))
        later=self.client.post('/api/transactions',json=self.transaction(description='Pay later shoes',amount_minor=9000,payment_method='pay_later',credit_facility_id=pay_later['id']))
        self.assertEqual((credit.status_code,later.status_code),(201,201))
        facilities={item['id']:item for item in self.client.get('/api/credit-facilities').json()}
        self.assertEqual(facilities[card['id']]['amount_owed_minor'],12000); self.assertEqual(facilities[pay_later['id']]['available_credit_minor'],91000)
        summary=self.client.get('/api/summary/2026-08').json()
        self.assertEqual(summary['actual_expenses'],25500); self.assertEqual(summary['actual_cash_expenses'],4500); self.assertEqual(summary['actual_credit_funded_expenses'],21000); self.assertEqual(summary['expected_remaining'],-4500)

    def test_partial_full_and_multiple_credit_payments_do_not_double_count(self):
        card=self.client.post('/api/credit-facilities',json=self.facility(amount_owed_minor=60000)).json()
        first=self.client.post(f"/api/credit-facilities/{card['id']}/payments",json={'amount_minor':25000,'transaction_date':'2026-08-11','note':None}); self.assertEqual(first.status_code,201)
        second=self.client.post(f"/api/credit-facilities/{card['id']}/payments",json={'amount_minor':15000,'transaction_date':'2026-08-12','note':None}); self.assertEqual(second.status_code,201)
        facility=self.client.get('/api/credit-facilities').json()[0]; self.assertEqual((facility['amount_owed_minor'],facility['available_credit_minor']),(20000,780000))
        final=self.client.post(f"/api/credit-facilities/{card['id']}/payments",json={'amount_minor':20000,'transaction_date':'2026-08-13','note':None}); self.assertEqual(final.status_code,201)
        summary=self.client.get('/api/summary/2026-08').json(); self.assertEqual(summary['actual_bills'],0); self.assertEqual(summary['actual_credit_payments'],60000); self.assertEqual(summary['actual_expenses'],0); self.assertEqual(summary['expected_remaining'],-60000)
        overpay=self.client.post(f"/api/credit-facilities/{card['id']}/payments",json={'amount_minor':1,'transaction_date':'2026-08-14','note':None}); self.assertEqual(overpay.status_code,422)

    def test_credit_expense_edit_and_delete_adjust_balance(self):
        card=self.client.post('/api/credit-facilities',json=self.facility()).json()
        created=self.client.post('/api/transactions',json=self.transaction(amount_minor=12000,payment_method='credit',credit_facility_id=card['id'])).json()
        changed=self.client.put(f"/api/transactions/{created['id']}",json=self.transaction(amount_minor=15000,payment_method='credit',credit_facility_id=card['id']))
        self.assertEqual(changed.status_code,200); self.assertEqual(self.client.get('/api/credit-facilities').json()[0]['amount_owed_minor'],15000)
        self.assertEqual(self.client.delete(f"/api/transactions/{created['id']}").status_code,204); self.assertEqual(self.client.get('/api/credit-facilities').json()[0]['amount_owed_minor'],0)
        self.assertEqual(self.client.delete(f"/api/credit-facilities/{card['id']}").status_code,204)

    def test_fixed_loan_interest_payment_and_no_double_counting(self):
        self.assertEqual(daily_simple_interest_minor(500000,825,30),3390)
        loan=self.client.post('/api/credit-facilities',json=self.fixed_loan()).json()
        rule=self.client.post('/api/recurring',json=self.recurring(amount_minor=73000,description='Car loan payment',category='Loan repayment',linked_fixed_loan_id=loan['id'])).json()
        payment=self.client.post(f"/api/occurrences/{rule['id']}/2026-08-15/record",json={'transaction_date':'2026-08-31'})
        self.assertEqual(payment.status_code,201,payment.text); self.assertEqual(payment.json()['transaction_role'],'loan_payment')
        updated=self.client.get('/api/credit-facilities').json()[0]
        self.assertEqual(updated['amount_owed_minor'],430390); self.assertIsNone(updated['available_credit_minor'])
        summary=self.client.get('/api/summary/2026-08').json()
        self.assertEqual(summary['actual_loan_payments'],73000); self.assertEqual(summary['actual_bills'],0); self.assertEqual(summary['expected_remaining'],-73000)
        events=self.client.get('/api/export.json').json()['loan_balance_events']; self.assertEqual(events[-1]['interest_minor'],3390)
        self.assertEqual(self.client.delete(f"/api/transactions/{payment.json()['id']}").status_code,422)

    def test_fixed_loan_saves_unlinked_then_links_from_either_editor(self):
        loan=self.client.post('/api/credit-facilities',json=self.fixed_loan(name='Unlinked loan'))
        self.assertEqual(loan.status_code,201,loan.text); self.assertIsNone(loan.json()['linked_recurring_rule_id'])
        rule=self.client.post('/api/recurring',json=self.recurring(description='Loan payment'))
        self.assertEqual(rule.status_code,201,rule.text)
        linked_loan=self.fixed_loan(name='Unlinked loan',linked_recurring_rule_id=rule.json()['id'])
        edited=self.client.put(f"/api/credit-facilities/{loan.json()['id']}",json=linked_loan)
        self.assertEqual(edited.status_code,200,edited.text); self.assertEqual(edited.json()['linked_recurring_rule_id'],rule.json()['id'])

        second=self.client.post('/api/credit-facilities',json=self.fixed_loan(name='Link from recurring')).json()
        second_rule=self.client.post('/api/recurring',json=self.recurring(description='Second loan payment',linked_fixed_loan_id=second['id']))
        self.assertEqual(second_rule.status_code,201,second_rule.text)
        facilities={item['id']:item for item in self.client.get('/api/credit-facilities').json()}
        self.assertEqual(facilities[second['id']]['linked_recurring_rule_id'],second_rule.json()['id'])

    def test_fixed_loan_reconciliation_rate_edit_and_facility_delete_preserve_history(self):
        loan=self.client.post('/api/credit-facilities',json=self.fixed_loan()).json()
        reconciled=self.client.post(f"/api/credit-facilities/{loan['id']}/reconcile",json={'amount_owed_minor':434722,'balance_as_of_date':'2026-08-05','note':'Lender balance'})
        self.assertEqual(reconciled.status_code,200); self.assertEqual(reconciled.json()['amount_owed_minor'],434722)
        changed=self.fixed_loan(name='Updated car loan',amount_owed_minor=1,annual_rate_basis_points=900,balance_as_of_date='2026-08-15')
        updated=self.client.put(f"/api/credit-facilities/{loan['id']}",json=changed)
        expected=434722+daily_simple_interest_minor(434722,825,10)
        self.assertEqual(updated.status_code,200,updated.text); self.assertEqual(updated.json()['amount_owed_minor'],expected); self.assertEqual(updated.json()['annual_rate_basis_points'],900)
        payment=self.client.post(f"/api/credit-facilities/{loan['id']}/payments",json={'amount_minor':30000,'transaction_date':'2026-08-20','note':None}); self.assertEqual(payment.status_code,201)
        self.assertEqual(self.client.delete(f"/api/credit-facilities/{loan['id']}").status_code,204)
        self.assertEqual(self.client.get('/api/credit-facilities').json(),[])
        self.assertEqual(len(self.client.get('/api/transactions').json()),1)

    def test_credit_apr_is_display_only_and_account_edit_delete_preserves_transaction(self):
        card=self.client.post('/api/credit-facilities',json=self.facility(annual_rate_basis_points=1999)).json()
        expense=self.client.post('/api/transactions',json=self.transaction(amount_minor=12000,payment_method='credit',credit_facility_id=card['id'])).json()
        updated=self.client.put(f"/api/credit-facilities/{card['id']}",json=self.facility(name='Renamed card',amount_owed_minor=12000,annual_rate_basis_points=2499))
        self.assertEqual(updated.json()['amount_owed_minor'],12000); self.assertEqual(updated.json()['annual_rate_basis_points'],2499)
        self.assertEqual(self.client.delete(f"/api/credit-facilities/{card['id']}").status_code,204)
        self.assertEqual(self.client.get(f"/api/transactions").json()[0]['id'],expense['id'])
        self.assertEqual(self.client.delete(f"/api/transactions/{expense['id']}").status_code,204)

    def test_recurring_edit_delete_changes_future_only_and_preserves_history(self):
        rule=self.client.post('/api/recurring',json=self.recurring()).json()
        recorded=self.client.post(f"/api/occurrences/{rule['id']}/2026-08-15/record",json={}).json()
        changed=self.recurring(amount_minor=9000,description='Updated phone',interval_count=4,next_due_date='2026-09-15',start_date='2026-09-15')
        updated=self.client.put(f"/api/recurring/{rule['id']}",json=changed).json(); self.assertEqual(updated['amount_minor'],9000); self.assertEqual(updated['interval_count'],4)
        self.assertEqual(self.client.delete(f"/api/recurring/{rule['id']}").status_code,204)
        self.assertEqual(self.client.get('/api/transactions').json()[0]['id'],recorded['id'])
        self.assertEqual(self.client.get('/api/calendar/2026-09').json()['items'],[])

    def test_reset_requires_typed_confirmation_and_restores_defaults(self):
        self.client.post('/api/transactions',json=self.transaction()); self.client.post('/api/recurring',json=self.recurring()); self.client.post('/api/credit-facilities',json=self.fixed_loan())
        self.assertEqual(self.client.post('/api/reset',json={'confirmation':'reset'}).status_code,422)
        self.assertEqual(self.client.post('/api/reset',json={'confirmation':'RESET'}).status_code,200)
        self.assertEqual(self.client.get('/api/transactions').json(),[]); self.assertEqual(self.client.get('/api/recurring').json(),[]); self.assertEqual(self.client.get('/api/credit-facilities').json(),[])
        self.assertTrue(self.client.get('/api/categories').json())

    def test_payment_method_validation_requires_matching_facility(self):
        card=self.client.post('/api/credit-facilities',json=self.facility()).json()
        missing=self.client.post('/api/transactions',json=self.transaction(payment_method='credit',credit_facility_id=None)); self.assertEqual(missing.status_code,422)
        mismatch=self.client.post('/api/transactions',json=self.transaction(payment_method='pay_later',credit_facility_id=card['id'])); self.assertEqual(mismatch.status_code,422)

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

    def test_backup_export_restore_includes_credit_data_and_old_backups_still_work(self):
        facility=self.client.post('/api/credit-facilities',json=self.facility(amount_owed_minor=10000)).json()
        self.client.post('/api/transactions',json=self.transaction(amount_minor=2000,payment_method='credit',credit_facility_id=facility['id']))
        backup=self.client.get('/api/export.json').json(); self.assertEqual(len(backup['credit_facilities']),1); self.assertEqual(backup['transactions'][0]['payment_method'],'credit')
        restored=self.client.post('/api/restore?confirm=true',json=backup); self.assertEqual(restored.status_code,200); self.assertEqual(restored.json()['credit_facilities'],1)
        old_backup={"version":1,"transactions":[],"recurring_rules":[],"occurrences":[],"categories":[]}
        self.assertEqual(self.client.post('/api/restore?confirm=true',json=old_backup).status_code,200)

    def test_backup_restore_preserves_fixed_loan_rate_link_and_balance_events(self):
        rule=self.client.post('/api/recurring',json=self.recurring(interval_count=3)).json()
        loan=self.client.post('/api/credit-facilities',json=self.fixed_loan(linked_recurring_rule_id=rule['id'])).json()
        self.client.post(f"/api/credit-facilities/{loan['id']}/reconcile",json={'amount_owed_minor':490000,'balance_as_of_date':'2026-08-02','note':'Statement'})
        backup=self.client.get('/api/export.json').json()
        self.assertEqual(backup['credit_facilities'][0]['annual_rate_basis_points'],825); self.assertEqual(backup['recurring_rules'][0]['interval_count'],3); self.assertEqual(len(backup['loan_balance_events']),2)
        self.assertEqual(self.client.post('/api/restore?confirm=true',json=backup).status_code,200)
        restored=self.client.get('/api/credit-facilities').json()[0]
        self.assertEqual(restored['linked_recurring_rule_id'],rule['id']); self.assertEqual(restored['amount_owed_minor'],490000)
        self.assertEqual(self.client.get('/api/recurring').json()[0]['interval_count'],3)

    def test_schema_migration_preserves_existing_transactions(self):
        legacy=Path(self.temp.name)/'legacy.sqlite3'; connection=sqlite3.connect(legacy)
        connection.executescript("""CREATE TABLE transactions(id INTEGER PRIMARY KEY,transaction_type TEXT NOT NULL,amount_minor INTEGER NOT NULL,currency TEXT NOT NULL,description TEXT NOT NULL,category TEXT NOT NULL,transaction_date TEXT NOT NULL,note TEXT,recurring_occurrence_id INTEGER,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);INSERT INTO transactions(transaction_type,amount_minor,currency,description,category,transaction_date) VALUES('expense',1234,'AUD','Existing row','Other','2026-08-01');"""); connection.commit(); connection.close()
        original=db.DB_PATH; db.DB_PATH=legacy
        try:
            db.init_db()
            with db.connect() as migrated:
                row=migrated.execute('SELECT * FROM transactions').fetchone(); columns={column['name'] for column in migrated.execute('PRAGMA table_info(transactions)')}
            self.assertEqual(row['description'],'Existing row'); self.assertTrue({'payment_method','credit_facility_id','transaction_role'}.issubset(columns))
        finally: db.DB_PATH=original

    def test_schema_migration_adds_default_interval_without_changing_rules(self):
        legacy=Path(self.temp.name)/'legacy-recurring.sqlite3'; connection=sqlite3.connect(legacy)
        connection.executescript("""CREATE TABLE recurring_rules(id INTEGER PRIMARY KEY,transaction_type TEXT NOT NULL,amount_minor INTEGER NOT NULL,currency TEXT NOT NULL,description TEXT NOT NULL,category TEXT NOT NULL,frequency TEXT NOT NULL,start_date TEXT NOT NULL,next_due_date TEXT NOT NULL,end_date TEXT,active INTEGER NOT NULL DEFAULT 1,automated_externally INTEGER NOT NULL DEFAULT 0,note TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);INSERT INTO recurring_rules(transaction_type,amount_minor,currency,description,category,frequency,start_date,next_due_date) VALUES('bill',1299,'AUD','Existing subscription','Subscriptions','monthly','2026-08-31','2026-08-31');"""); connection.commit(); connection.close()
        original=db.DB_PATH; db.DB_PATH=legacy
        try:
            db.init_db()
            with db.connect() as migrated:
                row=migrated.execute('SELECT * FROM recurring_rules').fetchone(); columns={column['name'] for column in migrated.execute('PRAGMA table_info(recurring_rules)')}
            self.assertEqual(row['description'],'Existing subscription'); self.assertEqual(row['interval_count'],1); self.assertIn('interval_count',columns)
        finally: db.DB_PATH=original


if __name__ == '__main__': unittest.main()
