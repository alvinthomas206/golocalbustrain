const busStationNameByKey = new Map();
let busStationNames = [];
let busStationLoadPromise = null;
let busTimetableStationsReady = false;

const busEl = (selector) => document.querySelector(selector);
const busNormalize = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
const busEscape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const busDuration = (minutes) => `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;

function setGoLocalView(view) {
  busEl('#launchPage').hidden = view !== 'home';
  busEl('#trainApp').hidden = view !== 'train';
  busEl('#busApp').hidden = view !== 'bus';
  window.scrollTo({ top: 0, behavior: 'auto' });
  if (view === 'bus') loadBusStations().catch(() => {});
  if (view === 'train') {
    if (typeof loadStationDirectory === 'function' && !document.querySelector('#stationCount').textContent.includes('stations')) loadStationDirectory();
    if (!window.goLocalTrainOpened) {
      window.goLocalTrainOpened = true;
      searchTrains();
    }
  }
}

document.querySelectorAll('[data-open-app]').forEach((button) => {
  button.addEventListener('click', () => setGoLocalView(button.dataset.openApp));
});

function renderBusSuggestions(input) {
  const panel = busEl(`#${input.id === 'busFromInput' ? 'busFromSuggestions' : 'busToSuggestions'}`);
  if (!busStationNames.length) { panel.hidden = true; return; }
  const query = busNormalize(input.value);
  const matches = (query
    ? busStationNames.filter((name) => busNormalize(name).includes(query))
    : busStationNames
  ).slice(0, 18);
  panel.replaceChildren();
  for (const name of matches) {
    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'bus-station-suggestion';
    option.dataset.input = input.id;
    option.dataset.name = name;
    option.textContent = name;
    panel.append(option);
  }
  panel.hidden = matches.length === 0;
}

async function loadBusStations() {
  if (busStationLoadPromise) return busStationLoadPromise;
  if (busTimetableStationsReady) return busStationNames;
  if (!busStationNames.length) loadKeralaStationFallback();
  busEl('#busSourceStatus').textContent = 'Showing Kerala stations while bus stops load…';
  busStationLoadPromise = (async () => {
    try {
      const response = await fetch('/api/buses?action=stations', { signal: AbortSignal.timeout(50000) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not load bus stops.');
      busStationNames = payload.stations || [];
      for (const name of busStationNames) busStationNameByKey.set(busNormalize(name), name);
      busTimetableStationsReady = true;
      busEl('#busSourceStatus').textContent = `${Number(payload.count || busStationNames.length).toLocaleString('en-IN')} stops · community timetable`;
      return busStationNames;
    } catch (error) {
      busEl('#busSourceStatus').textContent = busStationNames.length
        ? 'Kerala railway stations shown; private bus stop list could not load'
        : 'Bus timetable could not be loaded';
      busEl('#busResultsSubtitle').textContent = error.name === 'TimeoutError'
        ? 'Loading the bus timetable took too long. Please try again.'
        : error.message;
      throw error;
    } finally {
      busStationLoadPromise = null;
    }
  })();
  return busStationLoadPromise;
}

async function loadKeralaStationFallback() {
  try {
    const response = await fetch('/stations.json');
    if (!response.ok) return;
    const records = await response.json();
    if (busTimetableStationsReady) return;
    const names = records.filter((station) => station && station.state?.toLowerCase() === 'kerala' && station.name)
      .map((station) => station.name.trim());
    for (const name of names) busStationNameByKey.set(busNormalize(name), name);
    busStationNames = [...new Set([...busStationNames, ...names])].sort((a, b) => a.localeCompare(b));
    busEl('#busSourceStatus').textContent = 'Kerala railway stations shown while private bus stops load…';
    for (const id of ['busFromInput', 'busToInput']) {
      const input = busEl(`#${id}`);
      if (document.activeElement === input) renderBusSuggestions(input);
    }
  } catch { /* The bus timetable request will show its own error if this fallback is unavailable. */ }
}

for (const id of ['busFromInput', 'busToInput']) {
  const input = busEl(`#${id}`);
  input.addEventListener('focus', () => { loadBusStations().then(() => renderBusSuggestions(input)).catch(() => {}); });
  input.addEventListener('input', () => renderBusSuggestions(input));
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      const panel = busEl(`#${id === 'busFromInput' ? 'busFromSuggestions' : 'busToSuggestions'}`);
      const first = panel.querySelector('.bus-station-suggestion');
      if (first && !panel.hidden) { event.preventDefault(); first.click(); }
      else { event.preventDefault(); searchBusJourneys(); }
    }
    if (event.key === 'Escape') busEl(`#${id === 'busFromInput' ? 'busFromSuggestions' : 'busToSuggestions'}`).hidden = true;
  });
}

document.addEventListener('click', (event) => {
  const option = event.target.closest('.bus-station-suggestion');
  if (option) {
    const input = busEl(`#${option.dataset.input}`);
    input.value = option.dataset.name;
    busEl(`#${option.dataset.input === 'busFromInput' ? 'busFromHint' : 'busToHint'}`).textContent = 'Kerala private bus stop';
    busEl(`#${option.dataset.input === 'busFromInput' ? 'busFromSuggestions' : 'busToSuggestions'}`).hidden = true;
    return;
  }
  if (!event.target.closest('.bus-field')) {
    busEl('#busFromSuggestions').hidden = true;
    busEl('#busToSuggestions').hidden = true;
  }
});

document.querySelectorAll('[data-bus-clear]').forEach((button) => {
  button.addEventListener('click', () => {
    const input = busEl(`#${button.dataset.busClear}`);
    input.value = '';
    input.focus();
    renderBusSuggestions(input);
  });
});

busEl('#busSwapButton').addEventListener('click', () => {
  const from = busEl('#busFromInput');
  const to = busEl('#busToInput');
  [from.value, to.value] = [to.value, from.value];
});

busEl('#busUnlimited').addEventListener('change', (event) => {
  busEl('#busMaxTransfers').disabled = event.target.checked;
});

function renderBusPlans(payload) {
  const plans = payload.plans || [];
  busEl('#busResultsTitle').textContent = `${payload.from} to ${payload.to}`;
  busEl('#busResultsSubtitle').textContent = plans.length
    ? `${plans.length} scheduled option${plans.length === 1 ? '' : 's'} · ${payload.maxTransfers === 'unlimited' ? 'no transfer limit' : `up to ${payload.maxTransfers} changes`}`
    : 'No bus journey found in the available timetable. Try another leave-after time or transfer limit.';
  if (!plans.length) {
    busEl('#busResultsList').innerHTML = '<div class="bus-empty-state"><strong>No bus route found</strong><p>Try an earlier departure or allow more changes. This dataset covers Kerala private buses only.</p></div>';
    return;
  }
  busEl('#busResultsList').innerHTML = plans.map((plan, planIndex) => `
    <article class="bus-plan-card">
      <div class="bus-plan-head"><div><span class="bus-plan-kicker">${plan.transferCount === 0 ? 'DIRECT BUS' : `${plan.transferCount} CHANGE${plan.transferCount === 1 ? '' : 'S'}`}</span><h3>${busEscape(payload.from)} <span>→</span> ${busEscape(payload.to)}</h3></div><div class="bus-total-time">${busDuration(plan.totalMinutes)}<small>scheduled journey</small></div></div>
      <div class="bus-leg-list">${plan.legs.map((leg, index) => `<div class="bus-leg"><span class="bus-leg-number">${String(index + 1).padStart(2, '0')}</span><div class="bus-leg-vehicle"><strong>${busEscape(leg.vehicle)}</strong><small>Private bus · timetable</small></div><div class="bus-leg-time"><b>${busEscape(leg.depart)}${leg.departDayOffset ? ' (+1 day)' : ''}</b><span>${busEscape(leg.from)}</span></div><div class="bus-leg-line"></div><div class="bus-leg-time"><b>${busEscape(leg.arrive)}${leg.arriveDayOffset ? ' (+1 day)' : ''}</b><span>${busEscape(leg.to)}</span></div></div>${plan.transfers[index] ? `<div class="bus-transfer-note">↳ ${busDuration(plan.transfers[index].waitMinutes)} connection at ${busEscape(plan.transfers[index].station)}</div>` : ''}`).join('')}</div>
      <div class="bus-plan-foot">${plan.legs.length} bus leg${plan.legs.length === 1 ? '' : 's'} · leave ${busEscape(plan.departAt)} · arrive ${busEscape(plan.arriveAt)}${plan.arrivalDayOffset ? ' (+1 day)' : ''} · scheduled, not live</div>
    </article>`).join('');
}

async function searchBusJourneys() {
  const fromText = busEl('#busFromInput').value.trim();
  const toText = busEl('#busToInput').value.trim();
  const from = busStationNameByKey.get(busNormalize(fromText));
  const to = busStationNameByKey.get(busNormalize(toText));
  if (!from || !to) {
    busEl('#busResultsSubtitle').textContent = 'Choose both bus stops from the suggestions before searching.';
    if (!busStationNames.length) loadBusStations().catch(() => {});
    return;
  }
  if (busNormalize(from) === busNormalize(to)) {
    busEl('#busResultsSubtitle').textContent = 'Choose two different bus stops.';
    return;
  }
  const limit = busEl('#busUnlimited').checked ? 'unlimited' : String(Math.max(0, Math.min(25, Number(busEl('#busMaxTransfers').value) || 0)));
  const button = busEl('#busSearchButton');
  button.disabled = true;
  button.innerHTML = '<span>Searching…</span><span class="button-arrow">···</span>';
  busEl('#busResultsTitle').textContent = `${from} to ${to}`;
  busEl('#busResultsSubtitle').textContent = 'Checking scheduled buses and possible transfers…';
  busEl('#busResultsList').innerHTML = '<div class="loading-row"><span></span> Finding bus connections…</div>';
  try {
    const params = new URLSearchParams({ from, to, leaveAfter: busEl('#busLeaveAfter').value || '06:00', maxTransfers: limit });
    const response = await fetch(`/api/buses?${params}`, { signal: AbortSignal.timeout(55000) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Bus search failed.');
    renderBusPlans(payload);
  } catch (error) {
    busEl('#busResultsSubtitle').textContent = error.name === 'TimeoutError'
      ? 'The bus timetable search took too long. Please try again.'
      : error.message;
    busEl('#busResultsList').innerHTML = `<div class="api-error"><strong>Could not load bus journeys</strong><p>${busEscape(error.message)}</p></div>`;
  } finally {
    button.disabled = false;
    button.innerHTML = '<span>Find bus journeys</span><span class="button-arrow">→</span>';
  }
}

busEl('#busSearchButton').addEventListener('click', searchBusJourneys);
