import { createServer } from 'node:http';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, extname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const PORT = process.env.PORT || 3000;
const DB_PATH = join(__dirname, 'db.json');
const PUBLIC_DIR = resolve(join(__dirname, 'public'));

const readDB = () => JSON.parse(readFileSync(DB_PATH, 'utf-8'));
const writeDB = (data) => writeFileSync(DB_PATH, JSON.stringify(data, null, 2));

// 人口が少ない地域ほど高ポイント（最小1・最大100）
function calcPoints(population) {
  if (!population || population <= 0) return 10;
  return Math.max(1, Math.min(100, Math.round(100 / Math.pow(population / 1000, 0.4))));
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css',
  '.js':   'application/javascript',
  '.json': 'application/json',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.ico':  'image/x-icon',
};

const CORS = {
  'Access-Control-Allow-Origin':  '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf-8');
}

function send(res, data, status = 200) {
  const body = JSON.stringify(data);
  res.writeHead(status, { 'Content-Type': 'application/json', ...CORS });
  res.end(body);
}

function serveStatic(req, res) {
  const urlPath = req.url === '/' ? '/index.html' : req.url.split('?')[0];
  const filePath = resolve(join(PUBLIC_DIR, urlPath));
  if (!filePath.startsWith(PUBLIC_DIR)) { res.writeHead(403); res.end('Forbidden'); return; }
  if (!existsSync(filePath))            { res.writeHead(404); res.end('Not Found'); return; }
  res.writeHead(200, { 'Content-Type': MIME[extname(filePath)] || 'application/octet-stream' });
  res.end(readFileSync(filePath));
}

createServer(async (req, res) => {
  Object.entries(CORS).forEach(([k, v]) => res.setHeader(k, v));
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  const url  = new URL(req.url, `http://localhost:${PORT}`);
  const path = url.pathname;

  try {
    // GET /spots
    if (req.method === 'GET' && path === '/spots') {
      const spots = readDB().spots.map((s) => ({ ...s, points: calcPoints(s.population) }));
      return send(res, spots);
    }

    // POST /spots - 地域住民によるスポット登録
    if (req.method === 'POST' && path === '/spots') {
      const { name, description, lat, lng, population, userId, culturalCard } = JSON.parse(await readBody(req));
      if (!name || lat === undefined || lng === undefined) {
        return send(res, { error: 'name, lat, lng は必須です' }, 400);
      }
      const db = readDB();
      const newId = db.spots.length ? Math.max(...db.spots.map((s) => s.id)) + 1 : 1;
      const newSpot = {
        id: newId,
        name: String(name).slice(0, 100),
        description: String(description || '').slice(0, 300),
        lat: Number(lat),
        lng: Number(lng),
        population: Number(population) || 0,
        image: null,
        culturalCard: culturalCard || null,
        uploadedBy: userId || null,
        createdAt: new Date().toISOString(),
      };
      db.spots.push(newSpot);
      writeDB(db);
      return send(res, { ...newSpot, points: calcPoints(newSpot.population) });
    }

    // POST /upload - 写真を既存スポットに投稿
    if (req.method === 'POST' && path === '/upload') {
      const { image, spotId, userId } = JSON.parse(await readBody(req));
      if (!image || !userId || !spotId) {
        return send(res, { error: 'image, spotId, userId は必須です' }, 400);
      }
      const db = readDB();
      const target = db.spots.find((s) => s.id === Number(spotId));
      if (!target) return send(res, { error: 'スポットが見つかりません' }, 404);

      Object.assign(target, { image, uploadedBy: userId });
      writeDB(db);

      const points = calcPoints(target.population);
      return send(res, { spot: target, points });
    }

    // POST /points
    if (req.method === 'POST' && path === '/points') {
      const { userId, amount } = JSON.parse(await readBody(req));
      if (!userId) return send(res, { error: 'userId は必須です' }, 400);
      const db = readDB();
      if (!db.users[userId]) db.users[userId] = { points: 0 };
      db.users[userId].points += amount || 10;
      writeDB(db);
      return send(res, { userId, points: db.users[userId].points });
    }

    // GET /points/:userId
    if (req.method === 'GET' && path.startsWith('/points/')) {
      const userId = path.slice('/points/'.length);
      const db = readDB();
      return send(res, { userId, points: db.users[userId]?.points ?? 0 });
    }

    // POST /route
    if (req.method === 'POST' && path === '/route') {
      const { userId, name, spotIds } = JSON.parse(await readBody(req));
      if (!userId || !Array.isArray(spotIds)) return send(res, { error: '不正なリクエスト' }, 400);
      const db = readDB();
      const route = {
        id: Date.now(), userId,
        name: name || `ルート ${db.routes.length + 1}`,
        spotIds, createdAt: new Date().toISOString(),
      };
      db.routes.push(route);
      writeDB(db);
      return send(res, route);
    }

    // GET /route
    if (req.method === 'GET' && path === '/route') {
      const userId = url.searchParams.get('userId');
      const db = readDB();
      return send(res, userId ? db.routes.filter((r) => r.userId === userId) : db.routes);
    }

    serveStatic(req, res);

  } catch (err) {
    console.error('Error:', err.message);
    send(res, { error: 'サーバーエラー', detail: err.message }, 500);
  }

}).listen(PORT, () => console.log(`文化アーカイブラリー → http://localhost:${PORT}`));
