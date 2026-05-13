const state = {
  userId: null,
  points: 0,
  spots: [],
  routeSpotIds: [],
  map: null,
  markers: {},
  selectedFile: null,
};

document.addEventListener('DOMContentLoaded', async () => {
  setupRightTabs();
  setupMobileTabs();
  setupUserLogin();
  setupUpload();
  setupRegister();
  setupRoute();
  setupModal();
  await loadSpots();
  initMap();
});

// ==============================
// 右パネルタブ
// ==============================
function setupRightTabs() {
  document.querySelectorAll('.rtab').forEach((btn) => {
    btn.addEventListener('click', () => switchRightTab(btn.dataset.rtab));
  });
}

function switchRightTab(tab) {
  document.querySelectorAll('.rtab').forEach((b) => b.classList.toggle('active', b.dataset.rtab === tab));
  document.querySelectorAll('.rpanel').forEach((p) => p.classList.toggle('active', p.id === `rpanel-${tab}`));
  if (tab === 'album') renderAlbum();
}

// ==============================
// モバイルタブ
// ==============================
function setupMobileTabs() {
  document.querySelectorAll('.mtab').forEach((btn) => {
    btn.addEventListener('click', () => setMobileTab(btn.dataset.mtab));
  });
}

function setMobileTab(tab) {
  document.querySelectorAll('.mtab').forEach((b) => b.classList.toggle('active', b.dataset.mtab === tab));
  document.getElementById('panel-spots').classList.remove('mobile-show');
  document.getElementById('panel-map').classList.remove('mobile-show');
  document.getElementById('panel-right').classList.remove('mobile-show');

  switch (tab) {
    case 'map':
      document.getElementById('panel-map').classList.add('mobile-show');
      if (state.map) setTimeout(() => state.map.invalidateSize(), 50);
      break;
    case 'spots':
      document.getElementById('panel-spots').classList.add('mobile-show');
      break;
    case 'upload':
    case 'album':
    case 'register':
    case 'route':
      document.getElementById('panel-right').classList.add('mobile-show');
      switchRightTab(tab);
      break;
  }
}

// ==============================
// ユーザー認証
// ==============================
function setupUserLogin() {
  document.getElementById('loginBtn').addEventListener('click', async () => {
    const id = document.getElementById('userIdInput').value.trim();
    if (!id) return alert('ユーザーIDを入力してください');
    state.userId = id;
    document.getElementById('pointsDisplay').classList.remove('hidden');
    await refreshPoints();
    await loadRoutes();
    document.getElementById('loginBtn').textContent = 'ログイン中';
    document.getElementById('loginBtn').disabled = true;
  });
}

async function refreshPoints() {
  if (!state.userId) return;
  const data = await (await fetch(`/points/${state.userId}`)).json();
  state.points = data.points;
  document.getElementById('pointsValue').textContent = state.points;
}

async function addPoints(amount = 10) {
  if (!state.userId) return;
  const data = await (await fetch('/points', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ userId: state.userId, amount }),
  })).json();
  state.points = data.points;
  document.getElementById('pointsValue').textContent = state.points;
  const el = document.getElementById('pointsDisplay');
  el.classList.add('points-bump');
  setTimeout(() => el.classList.remove('points-bump'), 400);
}

// ==============================
// スポット
// ==============================
async function loadSpots() {
  try {
    state.spots = await (await fetch('/spots')).json();
    renderSpotList();
    populateSpotSelect();
  } catch {
    document.getElementById('spotList').innerHTML = '<p class="empty-text">読み込みに失敗しました</p>';
  }
}

function calcPointsFromPopulation(population) {
  if (!population || population <= 0) return 10;
  return Math.max(1, Math.min(100, Math.round(100 / Math.pow(population / 1000, 0.4))));
}

function renderSpotList() {
  const container = document.getElementById('spotList');
  container.innerHTML = '';
  if (state.spots.length === 0) {
    container.innerHTML = '<p class="empty-text">スポットがありません</p>';
    return;
  }
  state.spots.forEach((spot) => {
    const card = document.createElement('div');
    card.className = 'spot-card fade-in';
    card.dataset.id = spot.id;
    const hasCard = !!spot.culturalCard;
    const pts = spot.points || calcPointsFromPopulation(spot.population);
    card.innerHTML = `
      ${spot.image ? `<img class="spot-card-thumb" src="${spot.image}" alt="${escHtml(spot.name)}" />` : ''}
      <div class="spot-card-header">
        <div class="spot-card-name">${escHtml(spot.name)}</div>
        <span class="spot-pts-badge">+${pts}pt</span>
      </div>
      <div class="spot-card-desc">${escHtml(spot.description)}</div>
      <div class="spot-card-actions">
        <button class="btn-tiny" onclick="focusSpot(${spot.id})">地図</button>
        ${hasCard ? `<button class="btn-tiny" onclick="openAlbumCard(${spot.id})">カード</button>` : ''}
        <button class="btn-tiny" onclick="addToRoute(${spot.id})">追加</button>
        ${hasCard ? `<span class="has-card-badge">文化カード</span>` : ''}
      </div>
    `;
    card.addEventListener('click', (e) => { if (e.target.tagName === 'BUTTON') return; focusSpot(spot.id); });
    container.appendChild(card);
  });
}

function populateSpotSelect() {
  const sel = document.getElementById('spotSelect');
  sel.innerHTML = '<option value="">スポットを選択（必須）</option>';
  state.spots.forEach((s) => {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.name;
    sel.appendChild(opt);
  });
}

// ==============================
// マップ（Leaflet）
// ==============================
function initMap() {
  state.map = L.map('map').setView([35.6812, 139.7531], 12);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  }).addTo(state.map);
  addMarkersToMap();
}

function addMarkersToMap() {
  if (!state.map) return;
  state.spots.forEach((spot) => {
    const marker = L.marker([spot.lat, spot.lng])
      .addTo(state.map)
      .bindPopup(`
        <div class="popup-name">${escHtml(spot.name)}</div>
        <div class="popup-desc">${escHtml(spot.description)}</div>
        ${spot.culturalCard ? '<div class="popup-badge">文化カードあり</div>' : ''}
      `);
    marker.on('click', () => highlightSpotCard(spot.id));
    state.markers[spot.id] = marker;
  });
}

function focusSpot(id) {
  const spot = state.spots.find((s) => s.id === id);
  if (!spot) return;
  highlightSpotCard(id);
  if (window.innerWidth <= 768) setMobileTab('map');
  if (state.map) {
    state.map.flyTo([spot.lat, spot.lng], 15);
    state.markers[id]?.openPopup();
  }
}

function highlightSpotCard(id) {
  document.querySelectorAll('.spot-card').forEach((c) => c.classList.remove('active'));
  const card = document.querySelector(`.spot-card[data-id="${id}"]`);
  if (card) { card.classList.add('active'); card.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
}

// ==============================
// アップロード
// ==============================
function setupUpload() {
  const uploadArea = document.getElementById('uploadArea');
  const imageInput = document.getElementById('imageInput');
  uploadArea.addEventListener('click', () => imageInput.click());
  uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('drag-over'); });
  uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('drag-over'));
  uploadArea.addEventListener('drop', (e) => {
    e.preventDefault(); uploadArea.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) handleFileSelect(e.dataTransfer.files[0]);
  });
  imageInput.addEventListener('change', (e) => { if (e.target.files[0]) handleFileSelect(e.target.files[0]); });
  document.getElementById('uploadBtn').addEventListener('click', handleUpload);
}

function handleFileSelect(file) {
  state.selectedFile = file;
  const reader = new FileReader();
  reader.onload = (e) => {
    document.getElementById('uploadPlaceholder').classList.add('hidden');
    const preview = document.getElementById('imagePreview');
    preview.src = e.target.result;
    preview.classList.remove('hidden');
    document.getElementById('uploadBtn').disabled = false;
  };
  reader.readAsDataURL(file);
}

async function handleUpload() {
  if (!state.userId) { alert('先にログインしてください'); return; }
  if (!state.selectedFile) return;

  const spotId = document.getElementById('spotSelect').value;
  if (!spotId) { alert('スポットを選択してください'); return; }

  const btn = document.getElementById('uploadBtn');
  const status = document.getElementById('uploadStatus');

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>投稿中...';
  status.className = 'upload-status loading';
  status.classList.remove('hidden');
  status.textContent = '写真を投稿しています...';

  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      const res = await fetch('/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image: e.target.result,
          spotId,
          userId: state.userId,
        }),
      });
      if (!res.ok) { const err = await res.json(); throw new Error(err.error); }
      const data = await res.json();

      status.className = 'upload-status success';
      status.textContent = `写真を投稿しました。+${data.points}pt`;

      await addPoints(data.points);
      await loadSpots();
      Object.values(state.markers).forEach((m) => m.remove());
      state.markers = {};
      addMarkersToMap();

      if (data.spot?.culturalCard) showLatestCard(data.spot.culturalCard, e.target.result);
      if (data.spot) focusSpot(data.spot.id);
    } catch (err) {
      status.className = 'upload-status error';
      status.textContent = `エラー: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.innerHTML = '写真を投稿する';
    }
  };
  reader.readAsDataURL(state.selectedFile);
}

function showLatestCard(card, imageDataUrl) {
  const el = document.getElementById('latestCard');
  el.classList.remove('hidden');
  el.innerHTML = buildCardHTML(card, imageDataUrl);
}

// ==============================
// スポット登録
// ==============================
function setupRegister() {
  document.getElementById('sidebarRegisterBtn').addEventListener('click', () => {
    if (window.innerWidth <= 768) setMobileTab('register');
    else switchRightTab('register');
  });

  document.getElementById('regPop').addEventListener('change', updateRegPtsPreview);
  updateRegPtsPreview();

  document.getElementById('gpsBtn').addEventListener('click', () => {
    if (!navigator.geolocation) { alert('この端末はGPSに対応していません'); return; }
    const btn = document.getElementById('gpsBtn');
    btn.textContent = '取得中...';
    btn.disabled = true;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        document.getElementById('regLat').value = pos.coords.latitude.toFixed(6);
        document.getElementById('regLng').value = pos.coords.longitude.toFixed(6);
        btn.textContent = '現在地を取得する（GPS）';
        btn.disabled = false;
      },
      () => {
        alert('位置情報の取得に失敗しました。手動で入力してください。');
        btn.textContent = '現在地を取得する（GPS）';
        btn.disabled = false;
      }
    );
  });

  document.getElementById('regSubmitBtn').addEventListener('click', submitSpot);
}

function updateRegPtsPreview() {
  const pop = Number(document.getElementById('regPop').value);
  document.getElementById('regPtsPreview').textContent = `+${calcPointsFromPopulation(pop)}pt`;
}

async function submitSpot() {
  if (!state.userId) { alert('ログインしてください'); return; }
  const name = document.getElementById('regName').value.trim();
  const lat  = Number(document.getElementById('regLat').value);
  const lng  = Number(document.getElementById('regLng').value);

  if (!name)        { alert('スポット名を入力してください'); return; }
  if (!lat || !lng) { alert('位置情報を取得または入力してください'); return; }

  const btn    = document.getElementById('regSubmitBtn');
  const status = document.getElementById('regStatus');
  btn.disabled = true; btn.textContent = '登録中...';
  status.classList.add('hidden');

  const cardTitle = document.getElementById('cardTitle').value.trim();
  const culturalCard = cardTitle ? {
    title: cardTitle,
    history: document.getElementById('cardHistory').value.trim(),
    story: document.getElementById('cardStory').value.trim(),
    culturalConnection: document.getElementById('cardCulturalConnection').value.trim(),
    question: document.getElementById('cardQuestion').value.trim(),
  } : null;

  try {
    const res = await fetch('/spots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        description: document.getElementById('regDesc').value.trim(),
        lat, lng,
        population: Number(document.getElementById('regPop').value),
        userId: state.userId,
        culturalCard,
      }),
    });
    if (!res.ok) { const err = await res.json(); throw new Error(err.error); }
    const spot = await res.json();

    status.className = 'upload-status success';
    status.classList.remove('hidden');
    status.textContent = `「${spot.name}」を登録しました。写真を投稿すると +${spot.points}pt 獲得できます。`;

    ['regName','regDesc','regLat','regLng','cardTitle','cardHistory','cardStory','cardCulturalConnection','cardQuestion'].forEach((id) => { document.getElementById(id).value = ''; });

    await loadSpots();
    Object.values(state.markers).forEach((m) => m.remove());
    state.markers = {};
    addMarkersToMap();
    focusSpot(spot.id);
  } catch (err) {
    status.className = 'upload-status error';
    status.classList.remove('hidden');
    status.textContent = `エラー: ${err.message}`;
  } finally {
    btn.disabled = false; btn.textContent = 'スポットを登録する';
  }
}

// ==============================
// アルバム
// ==============================
function renderAlbum() {
  const grid = document.getElementById('albumGrid');
  const withCards = state.spots.filter((s) => s.culturalCard);

  if (withCards.length === 0) {
    grid.innerHTML = '<p class="empty-text">まだ文化カードがありません<br>写真をアップして生成しましょう</p>';
    return;
  }

  grid.innerHTML = '';
  withCards.forEach((spot) => {
    const card = document.createElement('div');
    card.className = 'album-card fade-in';
    card.innerHTML = `
      <div class="album-thumb">
        ${spot.image
          ? `<img src="${spot.image}" alt="${escHtml(spot.name)}" />`
          : `<div class="album-no-img"><span>NO PHOTO</span></div>`}
      </div>
      <div class="album-info">
        <div class="album-card-title">${escHtml(spot.culturalCard.title || spot.name)}</div>
        <div class="album-spot-name">${escHtml(spot.name)}</div>
        ${spot.culturalCard.history
          ? `<div class="album-preview">${escHtml(spot.culturalCard.history.slice(0, 55))}...</div>`
          : ''}
      </div>
    `;
    card.addEventListener('click', () => openAlbumCard(spot.id));
    grid.appendChild(card);
  });
}

function openAlbumCard(spotId) {
  const spot = state.spots.find((s) => s.id === spotId);
  if (!spot?.culturalCard) return;

  const modalImg = document.getElementById('modalImg');
  if (spot.image) { modalImg.src = spot.image; modalImg.classList.remove('hidden'); }
  else { modalImg.classList.add('hidden'); }

  document.getElementById('modalCard').innerHTML = buildCardHTML(spot.culturalCard, null);
  document.getElementById('cardModal').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function buildCardHTML(card, imageDataUrl) {
  return `
    <div class="cultural-card fade-in">
      ${imageDataUrl ? `<img src="${imageDataUrl}" class="card-photo" alt="写真" />` : ''}
      <div class="card-title">${escHtml(card.title || '文化カード')}</div>
      ${cardField('歴史的背景', card.history)}
      ${cardField('物語', card.story)}
      ${cardField('文化とのつながり', card.culturalConnection)}
      ${card.question ? `<div class="card-question">${escHtml(card.question)}</div>` : ''}
    </div>
  `;
}

// ==============================
// モーダル
// ==============================
function setupModal() {
  document.getElementById('modalClose').addEventListener('click', closeModal);
  document.getElementById('modalBackdrop').addEventListener('click', closeModal);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
}

function closeModal() {
  document.getElementById('cardModal').classList.add('hidden');
  document.body.style.overflow = '';
}

// ==============================
// ルート管理
// ==============================
function setupRoute() {
  document.getElementById('saveRouteBtn').addEventListener('click', saveRoute);
}

function addToRoute(spotId) {
  if (state.routeSpotIds.includes(spotId)) return;
  state.routeSpotIds.push(spotId);
  renderRouteBuilder();
}

function removeFromRoute(spotId) {
  state.routeSpotIds = state.routeSpotIds.filter((id) => id !== spotId);
  renderRouteBuilder();
}

function renderRouteBuilder() {
  const container = document.getElementById('routeSpots');
  if (state.routeSpotIds.length === 0) {
    container.innerHTML = '<p class="empty-text">スポットが追加されていません</p>';
    return;
  }
  container.innerHTML = '';
  state.routeSpotIds.forEach((id, idx) => {
    const spot = state.spots.find((s) => s.id === id);
    if (!spot) return;
    const item = document.createElement('div');
    item.className = 'route-spot-item';
    item.innerHTML = `
      <span class="route-spot-num">${idx + 1}</span>
      <span class="route-spot-name">${escHtml(spot.name)}</span>
      <button class="route-spot-remove" onclick="removeFromRoute(${id})" aria-label="削除">&#215;</button>
    `;
    container.appendChild(item);
  });
}

async function saveRoute() {
  if (!state.userId) { alert('ログインが必要です'); return; }
  if (state.routeSpotIds.length === 0) { alert('スポットを追加してください'); return; }
  const name = document.getElementById('routeNameInput').value.trim() || `ルート ${new Date().toLocaleDateString('ja')}`;
  try {
    const res = await fetch('/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userId: state.userId, name, spotIds: state.routeSpotIds }),
    });
    if (!res.ok) throw new Error();
    state.routeSpotIds = [];
    renderRouteBuilder();
    document.getElementById('routeNameInput').value = '';
    await loadRoutes();
  } catch { alert('ルートの保存に失敗しました'); }
}

async function loadRoutes() {
  if (!state.userId) return;
  try {
    renderSavedRoutes(await (await fetch(`/route?userId=${state.userId}`)).json());
  } catch (_) {}
}

function renderSavedRoutes(routes) {
  const container = document.getElementById('savedRouteList');
  if (routes.length === 0) {
    container.innerHTML = '<p class="empty-text">保存されたルートはありません</p>';
    return;
  }
  container.innerHTML = '';
  routes.slice().reverse().forEach((route) => {
    const spotNames = route.spotIds
      .map((id) => { const s = state.spots.find((sp) => sp.id === id); return s ? s.name : `スポット${id}`; })
      .join(' → ');
    const item = document.createElement('div');
    item.className = 'saved-route-item fade-in';
    item.innerHTML = `
      <div class="saved-route-name">${escHtml(route.name)}</div>
      <div class="saved-route-meta">${escHtml(spotNames)}</div>
    `;
    item.addEventListener('click', () => showRouteOnMap(route));
    container.appendChild(item);
  });
}

function showRouteOnMap(route) {
  if (!state.map) return;
  const spots = route.spotIds.map((id) => state.spots.find((s) => s.id === id)).filter(Boolean);
  if (spots.length === 0) return;
  if (window.innerWidth <= 768) setMobileTab('map');
  state.map.fitBounds(spots.map((s) => [s.lat, s.lng]), { padding: [60, 60], maxZoom: 14 });
  spots.forEach((s) => state.markers[s.id]?.openPopup());
}

// ==============================
// ユーティリティ
// ==============================
function cardField(label, text) {
  if (!text) return '';
  return `
    <div class="card-field">
      <div class="card-field-label">${label}</div>
      <div class="card-field-text">${escHtml(text)}</div>
    </div>
  `;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
