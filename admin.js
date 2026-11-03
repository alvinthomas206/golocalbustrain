import { initializeApp } from 'https://www.gstatic.com/firebasejs/13.2.0/firebase-app.js';
import {
  getAuth, onAuthStateChanged, signInWithEmailAndPassword, signOut,
  sendPasswordResetEmail,
} from 'https://www.gstatic.com/firebasejs/13.2.0/firebase-auth.js';
import {
  getFirestore, collection, doc, getDoc, getDocs, query, orderBy, limit,
  startAfter, where, writeBatch, setDoc, deleteDoc, serverTimestamp,
  increment, getCountFromServer, onSnapshot,
} from 'https://www.gstatic.com/firebasejs/13.2.0/firebase-firestore.js';
import { firebaseConfig, busAdminEmail } from './firebase-config.js';

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
const $ = (selector) => document.querySelector(selector);
const sourceFiles = [
  'alappuzha.json', 'attingal.json', 'ernakulam.json', 'idukki.json', 'kannur.json',
  'kottayam.json', 'kozhikkode.json', 'malappuram.json', 'muvattupuzha.json',
  'palakkad-1.json', 'palakkad-2.json', 'pathanamthitta.json', 'vadakara.json', 'wayanad.json',
];
let lastBusDoc = null;
let listHasMore = false;
let currentBusId = null;
let currentBusData = null;
let busListLoading = false;
let totalBusCount = null;
let unsubscribeCatalog = null;
let lastCatalogRevision = null;
let catalogWriteInProgress = false;

const message = (element, text, type = '') => {
  element.textContent = text;
  element.className = `admin-message${type ? ` ${type}` : ''}`;
};
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[character]));
const normalizeStop = (value) => {
  let label = String(value || '').trim().replace(/\s+/g, ' ')
    .replace(/\s+(?:(?:old|new)\s+)?bus\s*(?:stand|st[ .]?and|tand)$/i, '')
    .replace(/[ ,.-]+$/g, '').trim();
  const compact = label.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (compact === 'changanacherry') label = 'CHANGANASSERY';
  if (compact === 'kothamangalam') label = 'KOTTAMANGALAM';
  return label;
};
const stopKey = (value) => normalizeStop(value).toLowerCase().replace(/[^a-z0-9]/g, '');
async function bumpCatalogRevision() {
  const reference = doc(db, 'busMetadata', 'state');
  catalogWriteInProgress = true;
  try {
    await setDoc(reference, {
      revision: increment(1),
      updatedAt: serverTimestamp(),
      updatedBy: auth.currentUser.email,
    }, { merge: true });
    const snapshot = await getDoc(reference);
    lastCatalogRevision = Number(snapshot.get('revision') || 0);
  } finally {
    catalogWriteInProgress = false;
  }
}

function watchCatalogChanges() {
  unsubscribeCatalog?.();
  lastCatalogRevision = null;
  unsubscribeCatalog = onSnapshot(doc(db, 'busMetadata', 'state'), (snapshot) => {
    if (!snapshot.exists()) return;
    const revision = Number(snapshot.get('revision') || 0);
    if (lastCatalogRevision === null) {
      lastCatalogRevision = revision;
      return;
    }
    if (revision === lastCatalogRevision) return;
    lastCatalogRevision = revision;
    if (catalogWriteInProgress) return;
    loadBusList(true).catch((error) => message($('#editorMessage'), error.message, 'error'));
  }, (error) => message($('#editorMessage'), error.message, 'error'));
}
const searchTokens = (values) => {
  const tokens = new Set();
  for (const value of values) {
    const normalized = String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    for (let size = 2; size <= Math.min(normalized.length, 24); size += 1) tokens.add(normalized.slice(0, size));
  }
  return [...tokens].slice(0, 1000);
};

function toTimeInput(value) {
  const text = String(value || '').trim().toLowerCase().replace(/\./g, '').replace(/\s+/g, ' ');
  const match12 = text.match(/^(\d{1,2}):(\d{2})\s*(am|pm)$/);
  if (match12) {
    let hour = Number(match12[1]) % 12;
    if (match12[3] === 'pm') hour += 12;
    return `${String(hour).padStart(2, '0')}:${match12[2]}`;
  }
  const match24 = text.match(/^(\d{1,2}):(\d{2})$/);
  if (match24 && Number(match24[1]) < 24) return `${String(Number(match24[1])).padStart(2, '0')}:${match24[2]}`;
  return '';
}

function fromTimeInput(value) {
  if (!value) return '';
  const [rawHour, minute] = value.split(':');
  const hour = Number(rawHour);
  return `${hour % 12 || 12}:${minute} ${hour < 12 ? 'am' : 'pm'}`;
}

function showDashboard(user) {
  $('#loginView').hidden = true;
  $('#adminView').hidden = false;
  $('#signedInEmail').textContent = user.email;
  watchCatalogChanges();
  loadBusList(true).catch((error) => {
    $('#busRecordCount').textContent = 'Connection failed';
    $('#busRecordList').innerHTML = `<div class="admin-message error">${esc(error.message)}</div>`;
  });
}

onAuthStateChanged(auth, async (user) => {
  if (!user) {
    unsubscribeCatalog?.();
    unsubscribeCatalog = null;
    lastCatalogRevision = null;
    $('#loginView').hidden = false;
    $('#adminView').hidden = true;
    return;
  }
  if (user.email?.toLowerCase() !== busAdminEmail || !user.emailVerified) {
    await signOut(auth);
    $('#loginView').hidden = false;
    message($('#loginMessage'), user.email?.toLowerCase() !== busAdminEmail
      ? `Only ${busAdminEmail} can manage bus data.`
      : 'The administrator email must be marked verified in Firebase Authentication before it can manage schedules.', 'error');
    return;
  }
  showDashboard(user);
});

$('#loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#loginButton');
  button.disabled = true;
  message($('#loginMessage'), 'Signing in…');
  try {
    const email = $('#loginEmail').value.trim().toLowerCase();
    if (email !== busAdminEmail) throw new Error(`Sign in with ${busAdminEmail}.`);
    await signInWithEmailAndPassword(auth, email, $('#loginPassword').value);
  } catch (error) {
    message($('#loginMessage'), error.message, 'error');
  } finally {
    button.disabled = false;
  }
});

$('#resetPassword').addEventListener('click', async () => {
  const email = $('#loginEmail').value.trim().toLowerCase();
  if (email !== busAdminEmail) {
    message($('#loginMessage'), `Enter ${busAdminEmail} to request a reset.`, 'error');
    return;
  }
  try {
    await sendPasswordResetEmail(auth, email);
    message($('#loginMessage'), `Password reset email sent to ${email}.`, 'success');
  } catch (error) {
    message($('#loginMessage'), error.message, 'error');
  }
});

$('#signOutButton').addEventListener('click', () => signOut(auth));

async function loadBusList(reset = false) {
  if (busListLoading) return;
  busListLoading = true;
  const button = $('#loadMoreButton');
  button.disabled = true;
  if (reset) {
    lastBusDoc = null;
    listHasMore = false;
    $('#busRecordList').replaceChildren();
  }
  const term = stopKey($('#busAdminSearch').value.trim());
  try {
    if (reset) {
      try {
        const count = await getCountFromServer(collection(db, 'busVehicles'));
        totalBusCount = count.data().count;
      } catch {
        totalBusCount = null;
      }
    }
    const constraints = term
      ? [where('searchTokens', 'array-contains', term), limit(100), ...(lastBusDoc ? [startAfter(lastBusDoc)] : [])]
      : [orderBy('vehicleNumber'), limit(100), ...(lastBusDoc ? [startAfter(lastBusDoc)] : [])];
    const snapshot = await getDocs(query(collection(db, 'busVehicles'), ...constraints));
    if (snapshot.docs.length) lastBusDoc = snapshot.docs.at(-1);
    else if (reset) lastBusDoc = null;
    listHasMore = snapshot.docs.length === 100;
    for (const record of snapshot.docs) {
      const data = record.data();
      const item = document.createElement('button');
      item.type = 'button';
      item.className = `bus-record${currentBusId === record.id ? ' active' : ''}`;
      item.dataset.busId = record.id;
      item.innerHTML = `<strong>${esc(data.busName || data.vehicleNumber || 'Unnamed bus')}</strong><small>${esc(data.vehicleNumber || '')} · ${esc((data.route || []).slice(0, 3).join(' → '))}</small>`;
      $('#busRecordList').append(item);
    }
    const visibleBusCount = $('#busRecordList').querySelectorAll('[data-bus-id]').length;
    $('#busRecordCount').textContent = term
      ? `Showing ${visibleBusCount}${listHasMore ? '+' : ''} matching buses`
      : totalBusCount === null
        ? `Showing ${visibleBusCount}${listHasMore ? '+' : ''} buses`
        : `Showing ${visibleBusCount} of ${totalBusCount.toLocaleString()} buses`;
    $('#loadMoreButton').textContent = term ? 'Load more matches' : 'Load more buses';
    button.hidden = !listHasMore;
    if (term && snapshot.empty) {
      $('#busRecordList').innerHTML = '<div class="admin-message">No matching bus. Try a bus number or stop name.</div>';
    }
  } finally {
    busListLoading = false;
    button.disabled = false;
  }
}

$('#busRecordList').addEventListener('click', async (event) => {
  const item = event.target.closest('[data-bus-id]');
  if (!item) return;
  try {
    const snapshot = await getDoc(doc(db, 'busVehicles', item.dataset.busId));
    if (!snapshot.exists()) throw new Error('This bus record no longer exists. Refresh the list.');
    currentBusId = snapshot.id;
    currentBusData = snapshot.data();
    renderEditor(currentBusData);
    document.querySelectorAll('.bus-record').forEach((entry) => entry.classList.toggle('active', entry.dataset.busId === currentBusId));
  } catch (error) {
    message($('#editorMessage'), error.message, 'error');
  }
});

$('#busAdminSearch').addEventListener('input', () => {
  clearTimeout(window.busSearchDelay);
  window.busSearchDelay = setTimeout(() => loadBusList(true).catch((error) => message($('#editorMessage'), error.message, 'error')), 250);
});
$('#loadMoreButton').addEventListener('click', () => loadBusList().catch((error) => message($('#editorMessage'), error.message, 'error')));
$('#refreshListButton').addEventListener('click', () => loadBusList(true).catch((error) => message($('#editorMessage'), error.message, 'error')));

function tripMarkup(trip = {}, index = 0) {
  const stops = Array.isArray(trip.stops) ? trip.stops : [];
  const rows = stops.map((stop, stopIndex) => `
    <div class="stop-row" data-stop-row>
      <input data-stop-name aria-label="Stop name" placeholder="Station or bus stop" value="${esc(stop.station || stop.stop || '')}" />
      <input data-arrival type="time" aria-label="Arrival time" value="${esc(toTimeInput(stop.arrivalTime || stop.arrival))}" />
      <input data-departure type="time" aria-label="Departure time" value="${esc(toTimeInput(stop.departureTime || stop.departure))}" />
      <div class="stop-controls"><button type="button" data-action="up" aria-label="Move stop up" ${stopIndex === 0 ? 'disabled' : ''}>↑</button><button type="button" data-action="down" aria-label="Move stop down" ${stopIndex === stops.length - 1 ? 'disabled' : ''}>↓</button><button class="remove-stop" type="button" data-action="remove-stop" aria-label="Remove stop">×</button></div>
    </div>`).join('');
  return `<section class="trip-card" data-trip-card>
    <header class="trip-card-head"><strong>TRIP</strong><input class="trip-number-input" data-trip-number aria-label="Trip number" value="${esc(trip.tripNo ?? trip.trip ?? index + 1)}" /><button class="delete-trip" type="button" data-action="remove-trip">Remove trip</button></header>
    <div class="stop-table-head"><span>STOP IN ROUTE ORDER</span><span>ARRIVES</span><span>DEPARTS</span><span></span></div>
    <div class="trip-stop-list">${rows}</div>
    <button class="add-stop-button" type="button" data-action="add-stop">＋ Add stop</button>
  </section>`;
}

function renderEditor(data = {}) {
  $('#editorEmpty').hidden = true;
  $('#busEditor').hidden = false;
  $('#editorTitle').textContent = data.busName ? `Edit ${data.busName}` : 'New bus';
  $('#busName').value = data.busName || data.vehicleNumber || '';
  $('#vehicleNumber').value = data.vehicleNumber || '';
  $('#busDistrict').value = data.district || '';
  $('#deleteBusButton').hidden = !currentBusId;
  const trips = Array.isArray(data.trips) && data.trips.length ? data.trips : [{ tripNo: 1, stops: [] }];
  $('#tripList').innerHTML = trips.map(tripMarkup).join('');
  updateRouteSummary();
  message($('#editorMessage'), '');
}

function updateRouteSummary() {
  const firstTrip = $('#tripList').querySelector('[data-trip-card]');
  const names = firstTrip ? [...firstTrip.querySelectorAll('[data-stop-name]')].map((input) => input.value.trim()).filter(Boolean) : [];
  $('#routeSummary').textContent = names.length ? names.join(' → ') : 'Add stops to the first trip to define the route.';
}

function addStopRow(tripCard, stop = {}) {
  const holder = document.createElement('div');
  holder.innerHTML = tripMarkup({ tripNo: 1, stops: [stop] }, 0);
  const row = holder.querySelector('[data-stop-row]');
  tripCard.querySelector('.trip-stop-list').append(row);
  refreshStopControls(tripCard);
  updateRouteSummary();
}

function refreshStopControls(tripCard) {
  const rows = [...tripCard.querySelectorAll('[data-stop-row]')];
  rows.forEach((row, index) => {
    row.querySelector('[data-action="up"]').disabled = index === 0;
    row.querySelector('[data-action="down"]').disabled = index === rows.length - 1;
  });
}

$('#tripList').addEventListener('input', updateRouteSummary);
$('#tripList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const tripCard = button.closest('[data-trip-card]');
  const row = button.closest('[data-stop-row]');
  switch (button.dataset.action) {
    case 'add-stop': addStopRow(tripCard); break;
    case 'remove-trip':
      tripCard.remove();
      if (!$('#tripList').children.length) $('#tripList').insertAdjacentHTML('beforeend', tripMarkup({ tripNo: 1, stops: [] }, 0));
      updateRouteSummary();
      break;
    case 'remove-stop': row.remove(); refreshStopControls(tripCard); updateRouteSummary(); break;
    case 'up': if (row.previousElementSibling) row.parentElement.insertBefore(row, row.previousElementSibling); refreshStopControls(tripCard); updateRouteSummary(); break;
    case 'down': if (row.nextElementSibling) row.parentElement.insertBefore(row.nextElementSibling, row); refreshStopControls(tripCard); updateRouteSummary(); break;
  }
});

$('#addTripButton').addEventListener('click', () => {
  const holder = document.createElement('div');
  holder.innerHTML = tripMarkup({ tripNo: $('#tripList').children.length + 1, stops: [] }, $('#tripList').children.length);
  $('#tripList').append(holder.firstElementChild);
});

function collectTrips() {
  return [...$('#tripList').querySelectorAll('[data-trip-card]')].map((card, index) => {
    const stops = [...card.querySelectorAll('[data-stop-row]')].map((row) => {
      const station = normalizeStop(row.querySelector('[data-stop-name]').value);
      const arrivalTime = fromTimeInput(row.querySelector('[data-arrival]').value);
      const departureTime = fromTimeInput(row.querySelector('[data-departure]').value);
      if (!station) return null;
      if (!arrivalTime && !departureTime) throw new Error(`Add an arrival or departure time for ${station}.`);
      return { station, arrivalTime: arrivalTime || departureTime, departureTime: departureTime || arrivalTime };
    }).filter(Boolean);
    if (stops.length < 2) throw new Error(`Trip ${index + 1} needs at least two stops.`);
    return { tripNo: card.querySelector('[data-trip-number]').value.trim() || String(index + 1), stops };
  });
}

function buildSearchTokens(busName, vehicleNumber, district, route, trips) {
  const terms = [busName, vehicleNumber, district, ...route];
  for (const trip of trips) for (const stop of trip.stops) terms.push(stop.station);
  return searchTokens(terms);
}

$('#busEditor').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#saveBusButton');
  button.disabled = true;
  message($('#editorMessage'), 'Saving bus and trips…');
  try {
    const busName = $('#busName').value.trim();
    const vehicleNumber = $('#vehicleNumber').value.trim().toUpperCase();
    const district = $('#busDistrict').value.trim();
    if (!busName || !vehicleNumber) throw new Error('Add both a bus name and vehicle number.');
    const trips = collectTrips();
    const route = trips[0].stops.map((stop) => stop.station);
    const stopKeys = [...new Set(trips.flatMap((trip) => trip.stops.map((stop) => stopKey(stop.station))))];
    const data = {
      busName, vehicleNumber, district, route, stopKeys, trips,
      searchTokens: buildSearchTokens(busName, vehicleNumber, district, route, trips),
      sourceFile: currentBusData?.sourceFile || 'admin',
      updatedAt: serverTimestamp(),
      createdAt: currentBusData?.createdAt || serverTimestamp(),
      updatedBy: auth.currentUser.email,
    };
    const reference = currentBusId ? doc(db, 'busVehicles', currentBusId) : doc(collection(db, 'busVehicles'));
    await setDoc(reference, data);
    await bumpCatalogRevision();
    currentBusId = reference.id;
    currentBusData = data;
    $('#editorTitle').textContent = `Edit ${busName}`;
    $('#deleteBusButton').hidden = false;
    message($('#editorMessage'), 'Bus, route, and trips saved.', 'success');
    await loadBusList(true);
  } catch (error) {
    message($('#editorMessage'), error.message, 'error');
  } finally {
    button.disabled = false;
  }
});

$('#newBusButton').addEventListener('click', () => {
  currentBusId = null;
  currentBusData = null;
  document.querySelectorAll('.bus-record').forEach((item) => item.classList.remove('active'));
  renderEditor({ busName: '', vehicleNumber: '', trips: [{ tripNo: 1, stops: [] }] });
});

$('#deleteBusButton').addEventListener('click', async () => {
  if (!currentBusId || !confirm('Delete this bus and every trip stored in its record?')) return;
  try {
    await deleteDoc(doc(db, 'busVehicles', currentBusId));
    await bumpCatalogRevision();
    currentBusId = null;
    currentBusData = null;
    $('#busEditor').hidden = true;
    $('#editorEmpty').hidden = false;
    await loadBusList(true);
  } catch (error) {
    message($('#editorMessage'), error.message, 'error');
  }
});

function toVehicleRecord(busRecord, sourceFile, index) {
  const vehicleNumber = String(busRecord['Vehicle Number'] || busRecord.vehicle_number || `Unnumbered bus ${index + 1}`).trim().toUpperCase();
  const trips = (busRecord.schedule || []).map((trip, tripIndex) => ({
    tripNo: String(trip.trip ?? tripIndex + 1),
    stops: (trip.stations || []).map((stop) => ({
      station: normalizeStop(stop.station),
      arrivalTime: String(stop.arrivalTime || stop.departureTime || ''),
      departureTime: String(stop.departureTime || stop.arrivalTime || ''),
    })).filter((stop) => stop.station && (stop.arrivalTime || stop.departureTime)),
  })).filter((trip) => trip.stops.length >= 2);
  const route = (busRecord.route || []).map(normalizeStop).filter(Boolean);
  const effectiveRoute = route.length ? route : (trips[0]?.stops.map((stop) => stop.station) || []);
  const busName = String(busRecord['Bus Name'] || busRecord.busName || vehicleNumber).trim();
  const district = sourceFile.replace(/\.json$/i, '').replace(/-\d+$/, '').replace(/-/g, ' ');
  return {
    busName, vehicleNumber, district, route: effectiveRoute,
    stopKeys: [...new Set(trips.flatMap((trip) => trip.stops.map((stop) => stopKey(stop.station))))],
    trips,
    searchTokens: buildSearchTokens(busName, vehicleNumber, district, effectiveRoute, trips),
    sourceFile,
    updatedAt: serverTimestamp(),
    createdAt: serverTimestamp(),
    updatedBy: auth.currentUser.email,
  };
}

$('#importDataButton').addEventListener('click', async () => {
  if (!auth.currentUser || auth.currentUser.email?.toLowerCase() !== busAdminEmail) return;
  if (!confirm('Import the bundled Kerala timetable into Firebase? Existing bus records will be skipped, so admin edits will not be overwritten.')) return;
  const button = $('#importDataButton');
  const status = $('#importStatus');
  const bar = $('#importProgress');
  button.disabled = true;
  status.hidden = false;
  $('#importStatusText').textContent = 'Checking existing Firebase bus records…';
  bar.style.width = '0%';
  try {
    const existing = await getDocs(collection(db, 'busVehicles'));
    const existingIds = new Set(existing.docs.map((record) => record.id));
    let totalSource = 0;
    let imported = 0;
    let skipped = 0;
    let batch = writeBatch(db);
    let batchSize = 0;
    for (let fileIndex = 0; fileIndex < sourceFiles.length; fileIndex += 1) {
      const filename = sourceFiles[fileIndex];
      $('#importStatusText').textContent = `Reading ${filename}…`;
      const response = await fetch(`/bus-data/${filename}`);
      if (!response.ok) throw new Error(`Could not read ${filename} from this deployment.`);
      const payload = await response.json();
      for (let recordIndex = 0; recordIndex < (payload.busSchedules || []).length; recordIndex += 1) {
        const busRecord = payload.busSchedules[recordIndex];
        const vehicleId = `${filename.replace(/\.json$/i, '')}-${String(busRecord['Vehicle Number'] || busRecord.vehicle_number || `bus-${recordIndex}`).toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${recordIndex}`.slice(0, 1400);
        totalSource += 1;
        if (existingIds.has(vehicleId)) { skipped += 1; continue; }
        const value = toVehicleRecord(busRecord, filename, recordIndex);
        batch.set(doc(db, 'busVehicles', vehicleId), value);
        existingIds.add(vehicleId);
        imported += 1;
        batchSize += 1;
        if (batchSize >= 300) {
          await batch.commit();
          batch = writeBatch(db);
          batchSize = 0;
          $('#importStatusText').textContent = `Imported ${imported.toLocaleString()} of ${totalSource.toLocaleString()} records…`;
        }
      }
      bar.style.width = `${Math.round(((fileIndex + 1) / sourceFiles.length) * 100)}%`;
    }
    if (batchSize) await batch.commit();
    await bumpCatalogRevision();
    $('#importStatusText').textContent = `Done: imported ${imported.toLocaleString()} buses with their trips; skipped ${skipped.toLocaleString()} existing records.`;
    message($('#editorMessage'), `${imported.toLocaleString()} bus records imported into Firestore.`, 'success');
    await loadBusList(true);
  } catch (error) {
    $('#importStatusText').textContent = 'Import stopped. You can retry; records already imported will be skipped.';
    message($('#editorMessage'), error.message, 'error');
  } finally {
    button.disabled = false;
  }
});
