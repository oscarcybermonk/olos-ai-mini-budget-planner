const state = {
  month: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  type: 'expense', recurringType: 'bill', transactions: [], categories: [],
  facilities: [], calendarItems: [],
};
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const iso = day => `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
const money = cents => new Intl.NumberFormat('en-AU', {style: 'currency', currency: 'AUD'}).format(cents / 100);
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
const defaults = {expense: 'Other', income: 'Other Income', bill: 'Utilities', savings: 'Savings'};
const typeLabels = {expense: 'expense', income: 'income', bill: 'bill', savings: 'savings'};
const entryKinds = {expense: 'an expense', income: 'income', bill: 'a bill', savings: 'a savings transfer'};

class ApiError extends Error {
  constructor(message, errors = []) { super(message); this.errors = errors; }
}

async function api(path, options = {}) {
  const headers = {'Content-Type': 'application/json', ...(options.headers || {})};
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    let body = {};
    try { body = await response.json(); } catch {}
    throw new ApiError(body.detail || 'Something went wrong. Your data was not changed.', body.errors || []);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  const element = $('#toast'); element.textContent = message; element.classList.add('show');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => element.classList.remove('show'), 3000);
}

function monthKey() { return iso(state.month).slice(0, 7); }
function parseMinor(value, fieldName = 'Amount') {
  const text = String(value).trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw new ApiError(`${fieldName}: enter a valid amount with up to two decimal places`);
  const cents = Math.round(Number(text) * 100);
  if (cents <= 0) throw new ApiError(`${fieldName}: enter an amount greater than zero`);
  return cents;
}

function clearFormErrors() {
  $$('.field-error').forEach(element => { element.textContent = ''; });
  $$('#transaction-form [aria-invalid="true"]').forEach(element => element.removeAttribute('aria-invalid'));
}

function showFormErrors(errors) {
  const ids = {amount_minor: 'amount', transaction_date: 'transaction-date', description: 'description', category: 'category', credit_facility_id: 'expense-facility', payment_method: 'payment-method'};
  errors.forEach(({field, message}) => {
    const input = $(`#${ids[field] || field}`);
    if (!input) return;
    input.setAttribute('aria-invalid', 'true');
    const error = $(`#${(ids[field] || field)}-error`) || input.closest('label')?.querySelector('.field-error');
    if (error) error.textContent = message;
  });
  const first = $('#transaction-form [aria-invalid="true"]'); if (first) first.focus();
}

function validateTransactionForm() {
  const errors = [];
  try { parseMinor($('#amount').value); } catch (error) { errors.push({field: 'amount_minor', message: error.message.replace('Amount: ', '')}); }
  if (!$('#description').value.trim()) errors.push({field: 'description', message: 'Enter a description'});
  if (!$('#transaction-date').value || Number.isNaN(Date.parse(`${$('#transaction-date').value}T00:00:00`))) errors.push({field: 'transaction_date', message: 'Choose a valid date'});
  if (!$('#category').value.trim()) errors.push({field: 'category', message: 'Choose or enter a category'});
  const method = $('#payment-method').value;
  if (state.type === 'expense' && ['credit', 'pay_later'].includes(method) && !$('#expense-facility').value) errors.push({field: 'credit_facility_id', message: 'Choose the account used'});
  return errors;
}

function renderCategories() {
  const names = state.categories.filter(category => category.transaction_type === state.type || (state.type === 'expense' && category.transaction_type === 'bill')).map(category => category.name);
  $('#category-list').innerHTML = [...new Set(names)].map(name => `<option value="${escapeHtml(name)}">`).join('');
}

function matchingFacilities(method) { return state.facilities.filter(facility => facility.facility_type === method); }
function renderExpenseFacilities() {
  const method = $('#payment-method').value;
  const needsFacility = state.type === 'expense' && ['credit', 'pay_later'].includes(method);
  $('#facility-select-label').hidden = !needsFacility;
  if (!needsFacility) { $('#expense-facility').innerHTML = ''; return; }
  const facilities = matchingFacilities(method);
  $('#expense-facility').innerHTML = `<option value="">Choose account</option>${facilities.map(facility => `<option value="${facility.id}">${escapeHtml(facility.name)} · ${money(facility.available_credit_minor)} available</option>`).join('')}`;
}

// Switching type deliberately preserves amount, description, date and note. A default/blank
// category is replaced, while a custom category is kept; the live label makes the new type explicit.
function setType(type, {announce = true} = {}) {
  const previous = state.type;
  const category = $('#category').value.trim();
  state.type = type;
  $$('.type-tab').forEach(button => { const active = button.dataset.type === type; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
  if (!category || category === defaults[previous]) $('#category').value = defaults[type];
  $('#entry-kind').textContent = `Recording ${entryKinds[type]}`;
  $('#save-label').textContent = `Save ${typeLabels[type]}`;
  $('#payment-fields').hidden = type !== 'expense';
  renderCategories(); renderExpenseFacilities(); clearFormErrors();
  if (announce && previous !== type && ($('#amount').value || $('#description').value)) toast(`Entry kept — now recording ${entryKinds[type]}`);
}

function renderSnapshot(summary) {
  const metrics = [
    ['Available after planned bills', summary.expected_remaining, `${money(summary.expected_income)} still expected`, 'primary'],
    ['Income received', summary.actual_income, `${money(summary.expected_income)} expected`, ''],
    ['Spent this month', summary.actual_expenses + summary.actual_bills, `${money(summary.bills_remaining)} bills · ${money(summary.actual_credit_payments)} repayments`, ''],
    ['Savings', summary.actual_savings, `${money(summary.planned_savings_remaining)} still planned`, ''],
  ];
  $('#snapshot').innerHTML = metrics.map(([label, value, detail, kind]) => `<article class="metric ${kind}"><div class="metric-label">${label}</div><div class="metric-value">${money(value)}</div><div class="metric-detail">${detail}</div></article>`).join('');
}

function activityRow(transaction, example = false) {
  const repayment = transaction.transaction_role === 'credit_payment';
  const sign = transaction.transaction_type === 'income' ? '+' : '−';
  const icon = repayment ? '↘' : ({income: '+', expense: '−', bill: '−', savings: '↗'}[transaction.transaction_type]);
  const method = transaction.payment_method && transaction.transaction_type === 'expense' ? ` · ${transaction.payment_method.replace('_', ' ')}` : '';
  const controls = repayment
    ? `<button class="action-delete" data-delete="${transaction.id}" aria-label="Delete ${escapeHtml(transaction.description)}">Delete</button>`
    : `<button class="action-edit" data-edit="${transaction.id}" aria-label="Edit ${escapeHtml(transaction.description)}">Edit</button><button class="action-delete" data-delete="${transaction.id}" aria-label="Delete ${escapeHtml(transaction.description)}">Delete</button>`;
  return `<article class="activity-item${example ? ' example-row' : ''}"><span class="item-icon ${repayment ? 'repayment' : transaction.transaction_type}" aria-hidden="true">${icon}</span><div><div class="item-title">${escapeHtml(transaction.description)}</div><div class="item-meta">${escapeHtml(transaction.category)}${method} · ${example ? 'Example only' : new Date(`${transaction.transaction_date}T00:00`).toLocaleDateString('en-AU',{day:'numeric',month:'short'})}</div></div><div><div class="item-amount">${sign}${money(transaction.amount_minor)}</div><div class="item-actions">${example ? controls.replaceAll(`data-edit="0"`, 'data-example-action="edit"').replaceAll(`data-delete="0"`, 'data-example-action="delete"') : controls}</div></div></article>`;
}

function renderActivity() {
  const element = $('#activity-list');
  if (!state.transactions.length) {
    const sample = {id:0,transaction_type:'expense',transaction_role:'ordinary',amount_minor:2450,description:'Example lunch',category:'Dining',transaction_date:iso(new Date()),payment_method:'debit'};
    element.innerHTML = `<div class="first-run-note"><strong>Your activity will appear here.</strong><span>This example is not saved.</span></div>${activityRow(sample, true)}`;
    return;
  }
  element.innerHTML = state.transactions.map(transaction => activityRow(transaction)).join('');
}

function renderUpcoming(items) {
  const element = $('#upcoming-list'); const open = items.filter(item => ['upcoming','due'].includes(item.state));
  if (!open.length) { element.innerHTML = '<p class="empty-state">Nothing planned for the next 30 days.<br>Add a recurring item when you’re ready.</p>'; return; }
  const meanings = {income: 'Expected income', bill: 'Planned bill', savings: 'Planned savings'};
  element.innerHTML = open.slice(0, 12).map(item => `<article class="upcoming-item"><span class="item-icon ${item.transaction_type}" role="img" aria-label="${meanings[item.transaction_type]}" title="${meanings[item.transaction_type]}">${{income:'+',bill:'−',savings:'↗'}[item.transaction_type]}</span><div><div class="item-title">${escapeHtml(item.description)}</div><div class="item-meta">${new Date(`${item.due_date}T00:00`).toLocaleDateString('en-AU',{weekday:'short',day:'numeric',month:'short'})}${item.automated_externally?' · automated':''}</div></div><div><div class="item-amount">${item.transaction_type==='income'?'+':'−'}${money(item.amount_minor)}</div><div class="item-actions"><button class="action-record" data-record="${item.rule_id}|${item.due_date}" aria-label="Record ${escapeHtml(item.description)} as an actual transaction" title="Convert this planned item into an actual transaction">Record as paid</button><button class="action-edit" data-skip="${item.rule_id}|${item.due_date}">Skip</button></div></div></article>`).join('');
}

function renderFacilities() {
  $('#facility-count').textContent = state.facilities.length ? `(${state.facilities.length})` : '(optional)';
  const element = $('#facility-list');
  if (!state.facilities.length) { element.innerHTML = '<p class="empty-state compact">No accounts added. Add one only if you want to track a credit or pay-later balance.</p>'; return; }
  element.innerHTML = state.facilities.map(facility => `<article class="facility-item"><div><div class="item-title">${escapeHtml(facility.name)}</div><div class="item-meta">${facility.facility_type === 'credit' ? 'Credit' : 'Pay Later'} · Limit ${money(facility.credit_limit_minor)}</div></div><div class="facility-balance"><small>Owed</small><strong>${money(facility.amount_owed_minor)}</strong><span>${money(facility.available_credit_minor)} available</span></div><button class="small-button payment-action" data-payment="${facility.id}" ${facility.amount_owed_minor ? '' : 'disabled'}>Payment</button></article>`).join('');
}

async function refresh() {
  try {
    const [summary, transactions, upcoming, categories, facilities] = await Promise.all([
      api(`/api/summary/${monthKey()}`), api(`/api/transactions?limit=20&month=${monthKey()}`), api('/api/upcoming?days=30'), api('/api/categories'), api('/api/credit-facilities'),
    ]);
    Object.assign(state, {transactions, categories, facilities});
    renderSnapshot(summary); renderActivity(); renderUpcoming(upcoming); renderCategories(); renderFacilities(); renderExpenseFacilities();
    $('#current-month').textContent = state.month.toLocaleDateString('en-AU',{month:'long',year:'numeric'});
  } catch (error) { toast(error.message); }
}

function resetForm() {
  $('#transaction-form').reset(); $('#transaction-id').value = ''; $('#transaction-date').value = iso(new Date());
  $('#category').value = defaults[state.type]; $('#payment-method').value = 'debit'; $('#cancel-edit').hidden = true; $('#draft-note').hidden = true;
  $('#save-label').textContent = `Save ${state.type}`; clearFormErrors(); renderExpenseFacilities();
}

function fillEdit(item) {
  setType(item.transaction_type, {announce:false}); $('#transaction-id').value = item.id; $('#amount').value = (item.amount_minor / 100).toFixed(2);
  $('#description').value = item.description; $('#category').value = item.category; $('#transaction-date').value = item.transaction_date; $('#note').value = item.note || '';
  $('#payment-method').value = item.payment_method || 'debit'; renderExpenseFacilities(); if (item.credit_facility_id) $('#expense-facility').value = String(item.credit_facility_id);
  $('#cancel-edit').hidden = false; $('#save-label').textContent = 'Update transaction'; $('#transaction-form').scrollIntoView({behavior:'smooth',block:'center'}); $('#amount').focus();
}

$('#transaction-form').addEventListener('submit', async event => {
  event.preventDefault(); clearFormErrors(); const errors = validateTransactionForm();
  if (errors.length) { showFormErrors(errors); toast(errors[0].message); return; }
  try {
    const id = $('#transaction-id').value; const method = state.type === 'expense' ? $('#payment-method').value : null;
    const payload = {transaction_type:state.type,amount_minor:parseMinor($('#amount').value),currency:'AUD',description:$('#description').value.trim(),category:$('#category').value.trim(),transaction_date:$('#transaction-date').value,note:$('#note').value.trim()||null,payment_method:method,credit_facility_id:method && ['credit','pay_later'].includes(method) ? Number($('#expense-facility').value) : null};
    await api(id ? `/api/transactions/${id}` : '/api/transactions', {method:id?'PUT':'POST',headers:id?{}:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(payload)});
    toast(id ? 'Transaction updated' : `${state.type === 'income' ? 'Income' : state.type[0].toUpperCase()+state.type.slice(1)} saved`); resetForm(); await refresh();
  } catch (error) { showFormErrors(error.errors || []); toast(error.message); }
});

$$('.type-tab').forEach(button => button.addEventListener('click', () => setType(button.dataset.type)));
$('#payment-method').addEventListener('change', renderExpenseFacilities); $('#cancel-edit').addEventListener('click', resetForm);
$('#activity-list').addEventListener('click', async event => {
  if (event.target.dataset.exampleAction) { toast(`${event.target.textContent} controls appear here once you save a real transaction`); return; }
  const edit = event.target.dataset.edit, remove = event.target.dataset.delete;
  if (edit) fillEdit(state.transactions.find(item => item.id === Number(edit)));
  if (remove && confirm('Delete this transaction? Any linked credit balance will be adjusted.')) {
    try { await api(`/api/transactions/${remove}`,{method:'DELETE'}); toast('Transaction deleted'); await refresh(); } catch (error) { toast(error.message); }
  }
});
$('#upcoming-list').addEventListener('click', async event => {
  const record = event.target.dataset.record, skip = event.target.dataset.skip;
  try {
    if (record) { const [id,due]=record.split('|'); await api(`/api/occurrences/${id}/${due}/record`,{method:'POST',body:'{}'}); toast('Planned item recorded as an actual transaction'); }
    if (skip) { const [id,due]=skip.split('|'); if (!confirm('Skip this occurrence?')) return; await api(`/api/occurrences/${id}/${due}/skip`,{method:'POST'}); toast('Occurrence skipped'); }
    if (record || skip) await refresh();
  } catch (error) { toast(error.message); }
});

function changeMonth(delta) { state.month = new Date(state.month.getFullYear(), state.month.getMonth() + delta, 1); refresh(); }
$('#previous-month').addEventListener('click', () => changeMonth(-1)); $('#next-month').addEventListener('click', () => changeMonth(1));

const calendarDialog = $('#calendar-dialog');
async function openCalendar() {
  try { const data = await api(`/api/calendar/${monthKey()}`); state.calendarItems = data.items; renderCalendar(); calendarDialog.showModal(); } catch (error) { toast(error.message); }
}
function renderCalendar() {
  const year = state.month.getFullYear(), month = state.month.getMonth(), days = new Date(year, month + 1, 0).getDate();
  const offset = (new Date(year, month, 1).getDay() + 6) % 7; const grouped = Object.groupBy ? Object.groupBy(state.calendarItems, item => item.due_date) : state.calendarItems.reduce((all,item)=>((all[item.due_date]??=[]).push(item),all),{});
  $('#calendar-title').textContent = state.month.toLocaleDateString('en-AU',{month:'long',year:'numeric'});
  let cells = '<span class="calendar-spacer" aria-hidden="true"></span>'.repeat(offset);
  for (let day=1; day<=days; day++) { const key=`${monthKey()}-${String(day).padStart(2,'0')}`, items=grouped[key]||[]; const labels=items.map(item=>({income:'Expected income',bill:'Planned bill',savings:'Planned savings'}[item.transaction_type])).join(', '); const markers=items.map(item=>`<i class="marker ${item.transaction_type}" title="${{income:'Expected income',bill:'Planned bill',savings:'Planned savings'}[item.transaction_type]}"></i>`).join(''); cells += `<button class="calendar-day" data-calendar-date="${key}" aria-label="${day}${labels?`: ${labels}`:''}"><span>${day}</span><span class="day-markers" aria-hidden="true">${markers}</span></button>`; }
  $('#calendar-grid').innerHTML = cells; $('#calendar-day-detail').textContent = 'Choose a marked day to see its plans.';
}
$('#current-month').addEventListener('click', openCalendar); $('.calendar-dialog .dialog-close').addEventListener('click', () => calendarDialog.close());
calendarDialog.addEventListener('click', event => { if (event.target === calendarDialog) calendarDialog.close(); });
$('#calendar-grid').addEventListener('click', event => { const button=event.target.closest('[data-calendar-date]'); if(!button)return; const items=state.calendarItems.filter(item=>item.due_date===button.dataset.calendarDate); $('#calendar-day-detail').innerHTML=items.length?`<strong>${new Date(`${button.dataset.calendarDate}T00:00`).toLocaleDateString('en-AU',{weekday:'long',day:'numeric',month:'long'})}</strong>${items.map(item=>`<span>${escapeHtml(item.description)} · ${money(item.amount_minor)}</span>`).join('')}`:'No planned recurring items on this day.'; });

const recurringDialog=$('#recurring-dialog'); $('#add-recurring').addEventListener('click',()=>{recurringDialog.showModal();$('#next-due').value=iso(new Date())});
$$('.recurring-tab').forEach(button=>button.addEventListener('click',()=>{state.recurringType=button.dataset.type;$$('.recurring-tab').forEach(b=>b.classList.toggle('active',b===button));$('#recurring-category').value=defaults[state.recurringType]}));
$('#recurring-form').addEventListener('submit',async event=>{event.preventDefault();try{const due=$('#next-due').value;await api('/api/recurring',{method:'POST',body:JSON.stringify({transaction_type:state.recurringType,amount_minor:parseMinor($('#recurring-amount').value),currency:'AUD',description:$('#recurring-description').value.trim(),category:$('#recurring-category').value.trim(),frequency:$('#frequency').value,start_date:due,next_due_date:due,end_date:null,active:true,automated_externally:$('#automated').checked,note:null})});recurringDialog.close();event.target.reset();toast('Recurring item added');await refresh()}catch(error){toast(error.message)}});

const facilityDialog=$('#facility-dialog'); $('#add-facility').addEventListener('click',()=>facilityDialog.showModal());
$('#facility-form').addEventListener('submit',async event=>{event.preventDefault();try{const owedText=$('#facility-owed').value.trim()||'0';const owedMinor=Number(owedText)===0?0:parseMinor(owedText,'Amount owed');await api('/api/credit-facilities',{method:'POST',body:JSON.stringify({name:$('#facility-name').value.trim(),facility_type:$('#facility-type').value,credit_limit_minor:parseMinor($('#facility-limit').value,'Credit limit'),amount_owed_minor:owedMinor,currency:'AUD',note:$('#facility-note').value.trim()||null})});facilityDialog.close();event.target.reset();$('#facility-owed').value='0.00';toast('Credit account added');await refresh()}catch(error){toast(error.message)}});
const paymentDialog=$('#payment-dialog');
$('#facility-list').addEventListener('click',event=>{const id=Number(event.target.dataset.payment);if(!id)return;const facility=state.facilities.find(item=>item.id===id);$('#payment-facility-id').value=id;$('#payment-title').textContent=`${facility.name} payment`;$('#payment-balance').textContent=`Currently owed ${money(facility.amount_owed_minor)} · ${money(facility.available_credit_minor)} available`;$('#payment-amount').value='';$('#payment-date').value=iso(new Date());paymentDialog.showModal()});
$('#payment-form').addEventListener('submit',async event=>{event.preventDefault();try{const id=$('#payment-facility-id').value;await api(`/api/credit-facilities/${id}/payments`,{method:'POST',body:JSON.stringify({amount_minor:parseMinor($('#payment-amount').value,'Payment'),transaction_date:$('#payment-date').value,note:$('#payment-note').value.trim()||null})});paymentDialog.close();event.target.reset();toast('Payment recorded');await refresh()}catch(error){toast(error.message)}});

function voiceInput(){const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Recognition){toast('Voice recognition is unavailable. Use your phone keyboard’s dictation microphone instead.');$('#description').focus();return}const recognition=new Recognition();recognition.lang='en-AU';recognition.interimResults=false;$('#voice-button').textContent='Listening…';recognition.onresult=async event=>{try{const parsed=await api('/api/parse',{method:'POST',body:JSON.stringify({text:event.results[0][0].transcript})});setType(parsed.transaction_type,{announce:false});if(parsed.amount_minor)$('#amount').value=(parsed.amount_minor/100).toFixed(2);$('#description').value=parsed.description||'';$('#category').value=parsed.category;$('#draft-note').hidden=false;$('#amount').focus();toast('Voice draft ready — please check it')}catch(error){toast(error.message)}};recognition.onerror=()=>toast('Microphone input was not available. Nothing was saved.');recognition.onend=()=>{$('#voice-button').innerHTML='<span aria-hidden="true">●</span> Speak'};recognition.start()}
$('#voice-button').addEventListener('click',voiceInput);$('#theme-toggle').addEventListener('click',()=>{const current=document.documentElement.dataset.theme;document.documentElement.dataset.theme=current==='dark'?'light':'dark';localStorage.setItem('theme',document.documentElement.dataset.theme)});if(localStorage.getItem('theme'))document.documentElement.dataset.theme=localStorage.getItem('theme');
$('#restore-file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;if(!confirm('Restore this backup? Current transactions, recurring items and credit accounts will be replaced.')){event.target.value='';return}try{const body=await file.text();await api('/api/restore?confirm=true',{method:'POST',body});toast('Backup restored');await refresh()}catch(error){toast(error.message)}finally{event.target.value=''}});

resetForm(); setType('expense',{announce:false}); refresh(); if('serviceWorker' in navigator)window.addEventListener('load',()=>navigator.serviceWorker.register('/sw.js').catch(()=>{}));
