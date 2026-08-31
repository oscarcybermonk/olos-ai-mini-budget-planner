import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../frontend/assets/webmcp.js',import.meta.url),'utf8');

function response(body,status=200){return{ok:status>=200&&status<300,status,async json(){return body}}}

async function harness(responses=[]){
  const registered=[],requests=[],events=[];
  const status={classList:{add(value){status.class=value}},innerHTML:''};
  const context={
    AbortController,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail}},
    Date,Math,Object,Promise,RegExp,URLSearchParams,console,crypto:{randomUUID:()=> 'test-key'},
    fetch:async(path,options={})=>{requests.push({path,options});const next=responses.shift();if(!next)throw new Error(`Unexpected request: ${path}`);return next},
    document:{querySelector:()=>status,modelContext:{registerTool:async tool=>{registered.push(tool)}}},
    window:{dispatchEvent:event=>events.push(event)},
  };
  context.globalThis=context;
  vm.runInNewContext(source,context,{filename:'webmcp.js'});
  await context.window.OlosWebMCP.ready;
  return{...context.window.OlosWebMCP,registered,requests,events,status};
}

test('registers a compact meaningful tool surface with strict schemas and annotations',async()=>{
  const h=await harness();
  assert.equal(h.registered.length,11);
  assert.deepEqual(h.registered.map(t=>t.name),[
    'get_budget_summary','list_upcoming_items','list_recent_activity','list_credit_and_loans','get_calendar_month',
    'add_transaction','create_recurring_item','record_recurring_item','record_credit_payment','update_transaction','delete_transaction',
  ]);
  for(const tool of h.registered){
    assert.equal(tool.inputSchema.type,'object');
    assert.equal(tool.inputSchema.additionalProperties,false);
    assert.equal(typeof tool.annotations.readOnlyHint,'boolean');
    assert.equal(tool.annotations.untrustedContentHint,true);
  }
  assert.equal(h.registered.find(t=>t.name==='get_budget_summary').annotations.readOnlyHint,true);
  assert.equal(h.registered.find(t=>t.name==='delete_transaction').inputSchema.properties.confirmation.const,'DELETE');
  assert.match(h.status.innerHTML,/11 structured tools/);
});

test('summary keeps cash, revolving credit, and fixed loans separate',async()=>{
  const h=await harness([
    response({month:'2026-09',actual_income:300000,expected_remaining:210000}),
    response([{id:1,name:'Everyday card',facility_type:'credit',currency:'AUD',credit_limit_minor:500000,amount_owed_minor:100000,available_credit_minor:400000,annual_rate_basis_points:1999},{id:2,name:'Car loan',facility_type:'fixed_loan',currency:'AUD',amount_owed_minor:900000,annual_rate_basis_points:825,balance_as_of_date:'2026-09-01',linked_recurring_rule_id:4}]),
  ]);
  const output=await h.registered[0].execute({month:'2026-09'});
  assert.equal(output.data.expected_remaining,210000);
  assert.equal(output.data.revolving_credit[0].available_minor,400000);
  assert.equal(output.data.fixed_loans[0].estimated_owing_minor,900000);
});

test('add transaction serializes exact cents and announces a same-page refresh',async()=>{
  const created={id:7,transaction_type:'expense',amount_minor:8740,description:'Groceries'};
  const h=await harness([response(created,201)]),tool=h.registered.find(t=>t.name==='add_transaction');
  const output=await tool.execute({transaction_type:'expense',amount:'87.40',description:'Groceries',category:'Groceries',transaction_date:'2026-09-01',payment_method:'debit'});
  assert.equal(output.data.transaction.id,7);
  assert.equal(JSON.parse(h.requests[0].options.body).amount_minor,8740);
  assert.equal(h.requests[0].options.headers['Idempotency-Key'],'test-key');
  assert.equal(h.events[0].type,'olos:data-changed');
  await assert.rejects(()=>tool.execute({transaction_type:'expense',amount:'12.345',description:'Bad',category:'Other',transaction_date:'2026-09-01'}),/two decimal places/);
});

test('update merges a patch and delete requires an exact deliberate confirmation',async()=>{
  const current={id:9,transaction_type:'expense',amount_minor:1200,currency:'AUD',description:'Fuel',category:'Fuel',transaction_date:'2026-09-01',note:null,payment_method:'debit',credit_facility_id:null};
  const updated={...current,amount_minor:1350};
  const h=await harness([response(current),response(updated),response(updated),response(null,204)]);
  const update=h.registered.find(t=>t.name==='update_transaction');
  const changed=await update.execute({transaction_id:9,amount:'13.50'});
  assert.equal(changed.data.transaction.amount_minor,1350);
  assert.equal(JSON.parse(h.requests[1].options.body).description,'Fuel');
  const remove=h.registered.find(t=>t.name==='delete_transaction');
  await assert.rejects(()=>remove.execute({transaction_id:9,confirmation:'yes'}),/exactly equal to DELETE/);
  const deleted=await remove.execute({transaction_id:9,confirmation:'DELETE'});
  assert.equal(deleted.data.deleted.id,9);
  assert.equal(h.requests.at(-1).options.method,'DELETE');
});

test('recurring record and credit payment call existing deterministic API paths',async()=>{
  const h=await harness([
    response({id:4,transaction_role:'loan_payment'},201),
    response([{id:3,name:'Card',facility_type:'credit'}]),
    response({id:5,transaction_role:'credit_payment'},201),
  ]);
  const recurring=h.registered.find(t=>t.name==='record_recurring_item');
  const recorded=await recurring.execute({recurring_rule_id:2,due_date:'2026-09-15'});
  assert.equal(recorded.data.transaction.transaction_role,'loan_payment');
  assert.equal(h.requests[0].path,'/api/occurrences/2/2026-09-15/record');
  const payment=h.registered.find(t=>t.name==='record_credit_payment');
  await payment.execute({credit_facility_id:3,amount:'25.00',transaction_date:'2026-09-16'});
  assert.equal(JSON.parse(h.requests[2].options.body).amount_minor,2500);
});
