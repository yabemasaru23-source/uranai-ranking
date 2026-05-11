# 🌟 日々を輝かせる今日の一手

**Powered by Quality Management Co., Ltd.**

複数の占いサイトを横断集計し、**12星座 × 4血液型 = 全48通り**の組み合わせから「今日、本当に運勢が良いのは誰か」を判定。さらに、運命を動かす「**今日の一手**」アドバイスをお届けするWebツール。

🌐 **公開URL（予定）**: https://YOUR-USERNAME.github.io/uranai-ranking/

---

## ✨ 特徴

- 🎯 **6つの占い媒体**を横断集計（めざまし占い／マイナビ／TOKYO FM／福井新聞／anna／モデルプレス）
- ♈ **12星座 × 4血液型 = 48通り**を毎日ランキング化
- 💫 各組み合わせに「**今日の一手**」アドバイス付き
- 🌟 詳細モーダルで各サイトの順位内訳・ラッキー要素を表示
- 📱 完全レスポンシブ対応
- 🔍 SEO・OGP・JSON-LD 完備
- 📊 Google Analytics 4 イベント計測対応

---

## 🎨 デザイン

- **キャッチコピー**: 日々を輝かせる今日の一手！
- **ブランド**: Quality Management Co., Ltd.
- **カラー**: ロイヤルブルー × ゴールド（QMロゴ準拠）
- **フォント**: Noto Serif JP（日本語）/ Playfair Display（英字）

---

## 🚀 GitHub Pages 公開手順

### Step 1｜リポジトリ作成
1. [GitHub](https://github.com) にログイン → 右上「＋」→「New repository」
2. Repository name: `uranai-ranking`
3. **Public** を選択 → 「Create repository」

### Step 2｜ファイルをアップロード
1. リポジトリの「Add file」→「Upload files」
2. **このフォルダの全ファイル**をドラッグ＆ドロップ
   - `index.html`
   - `qm-logo.jpg` ← **必須！ロゴ画像**
   - `404.html`
   - `robots.txt`
   - `sitemap.xml`
   - `README.md`
   - `.gitignore`
3. 「Commit changes」をクリック

### Step 3｜GitHub Pagesを有効化
1. 「Settings」→ 左メニュー「Pages」
2. **Source**: `Deploy from a branch`
3. **Branch**: `main` / `/(root)` → **Save**
4. 数分後、`https://ユーザー名.github.io/uranai-ranking/` で公開🎉

---

## 🔧 公開前に差し替える箇所

`index.html` 内の以下を実際の値に置き換えてください：

### ① URL（合計 6箇所）
```
YOUR-USERNAME → あなたのGitHubユーザー名
```

### ② Google Analytics 4（2箇所）
```
GA_MEASUREMENT_ID → 実際の測定ID（G-XXXXXXXXXX）
```

### ③ OGP画像（任意）
- ファイル名: `ogp.jpg`
- 推奨サイズ: 1200 × 630px
- 同じフォルダに配置すると自動で読み込まれます

---

## 📊 GA4 イベント計測

| イベント名 | パラメータ | 計測タイミング |
|---|---|---|
| `select_zodiac` | `zodiac` | 星座を選択 |
| `select_blood` | `blood` | 血液型を選択 |
| `view_rank_detail` | `rank`, `combo` | ランクカード詳細を開いた時 |

---

## 📁 ファイル構成

```
uranai-ranking/
├── index.html       メインHTML
├── qm-logo.jpg      クオリティマネジメント社ロゴ
├── 404.html         エラーページ
├── robots.txt       検索エンジン向け
├── sitemap.xml      サイトマップ
├── README.md        このファイル
└── .gitignore       Git管理除外設定
```

---

## 📝 注意事項

- 占いはエンタメ目的です。重要な意思決定の根拠にしないでください
- 集計元の占いデータは2026年5月11日（月）時点のものです
- 日次更新する場合は、各サイトから最新の星座ランキングを取得し、`SOURCE_RANKINGS` を更新してください
