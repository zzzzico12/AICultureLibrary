# AI文化アーカイブラリー

住民が投稿した文化スポットの写真・説明を地図上に集約し、地域の文化・歴史を次世代に伝えるウェブアプリです。人口が少ない地域ほど高ポイントが獲得でき、過疎地域の文化継承を促進します。

JSAI 2026 ハッカソン出展作品。

---

## 機能

**ウェブアプリ (`server.js`)**
- インタラクティブ地図（Leaflet + OpenStreetMap）
- スポット登録：名前・説明・位置情報（GPS対応）・文化カード
- 写真投稿：スポットへの写真アップロード
- ポイントシステム：人口逆比例スコア（過疎地ほど高ポイント）
- 文化カードアルバム：各スポットの歴史・物語をカード形式で保存
- ルート作成・保存
- モバイル対応レイアウト（底部タブバー）

**4D タイムマシン (`main.py`)**
- 同一建物を複数アングルから撮影した写真群からの3D復元
- ORB特徴点抽出 → Essential Matrix → DLT三角測量（SfM）
- 時刻スライダーで3Dモデルのライティングをリアルタイム変化
- インタラクティブ3D点群ビューワ（matplotlib）

---

## 技術スタック

| レイヤー | 採用技術 |
|---|---|
| サーバー | Node.js 20+ (`node:http`) — npm依存ゼロ |
| 地図 | Leaflet.js + OpenStreetMap |
| データ | JSON ファイル (`db.json`) |
| 3D復元 | Python + OpenCV (SfM) + NumPy + matplotlib |

---

## セットアップ

### ウェブアプリ

```bash
# Node.js 20 以上が必要
cp .env.example .env
npm start         # 本番
npm run dev       # 開発（ファイル変更で自動再起動）
```

ブラウザで http://localhost:3000 を開く。

### 4D タイムマシン（Python デモ）

```bash
pip install opencv-python numpy matplotlib scipy
python3 main.py
```

時刻スライダーを動かすと3Dモデルのライティングが変化します。

---

## API

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/spots` | スポット一覧（ポイント付き） |
| `POST` | `/spots` | スポット登録 |
| `POST` | `/upload` | 写真を既存スポットに投稿 |
| `GET` | `/points/:userId` | ユーザーポイント取得 |
| `POST` | `/points` | ポイント付与 |
| `GET` | `/route?userId=` | ルート一覧 |
| `POST` | `/route` | ルート保存 |

---

## ポイント計算式

```
points = max(1, min(100, round(100 / (population / 1000)^0.4)))
```

人口5万人の町 → 約18pt、人口3000人の村 → 約52pt。

---

## ライセンス

MIT
