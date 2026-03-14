# FuturAgents 🤖

**AI Destekli Multi-Agent Binance Futures Analiz & Trading Sistemi**

Claude Opus, Sonnet ve Haiku modellerini farklı görevlere atayarak çalışan,
Binance USDⓈ-M Futures piyasasını analiz eden ve sinyal üreten bir platform.

---

## 🏗️ Mimari

```
┌─────────────────────────────────────────────────────────┐
│                   FastAPI Backend                        │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐   │
│  │  Technical  │  │  Sentiment   │  │     Risk      │   │
│  │   Agent     │  │    Agent     │  │    Agent      │   │
│  │  (Haiku⚡)  │  │  (Sonnet🧠)  │  │  (Haiku⚡)   │   │
│  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘   │
│         └────────────────┼──────────────────┘            │
│                          ▼                               │
│              ┌───────────────────────┐                   │
│              │   Orchestrator Agent  │                   │
│              │      (Opus 👑)        │                   │
│              │  EXECUTE/WAIT/ABORT   │                   │
│              └───────────┬───────────┘                   │
│                          ▼                               │
│              ┌───────────────────────┐                   │
│              │  Binance Futures API  │                   │
│              │  Testnet / Mainnet    │                   │
│              └───────────────────────┘                   │
│                                                          │
│  MongoDB (analiz geçmişi) │ Redis (cache + sinyaller)   │
└─────────────────────────────────────────────────────────┘
```

### Agent Rolleri

| Agent | Model | Görev | Maliyet |
|-------|-------|-------|---------|
| **Technical** | Haiku ⚡ | OHLCV analizi, 10+ indikatör | Düşük |
| **Sentiment** | Sonnet 🧠 | Funding, L/S oranı, tasfiyeler | Orta |
| **Risk** | Haiku ⚡ | ATR pozisyon boyutu, portföy riski | Düşük |
| **Orchestrator** | Opus 👑 | Tüm raporları sentezler, karar | Yüksek |

---

## 🚀 Railway'e Deploy

### 1. Repo Hazırlığı

GitHub'a push et:
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/KULLANICI/futuragents.git
git push -u origin main
```

### 2. Railway Projesi Oluştur

1. [railway.app](https://railway.app) → **New Project**
2. **Deploy from GitHub repo** → repoyu seç
3. Railway, `Dockerfile` ve `railway.toml`'u otomatik algılar

### 3. MongoDB ve Redis Ekle

1. Projede **+ New** → **Database** → **Add MongoDB**
2. Projede **+ New** → **Database** → **Add Redis**

MongoDB eklendikten sonra Railway otomatik olarak `MONGO_URL` değişkeni oluşturur.
Redis için `REDIS_URL` oluşturur. Bunları aşağıdaki değişken adlarına kopyala.

### 4. Environment Variables

Railway Dashboard → Servisin üzerine tıkla → **Variables** sekmesi → **+ New Variable**

#### 🔴 ZORUNLU

```
ANTHROPIC_API_KEY       = sk-ant-api03-...
BINANCE_API_KEY         = testnet_api_key
BINANCE_API_SECRET      = testnet_secret
BINANCE_TESTNET         = true
MONGODB_URL             = (Railway MongoDB'den kopyala)
MONGODB_DATABASE        = futuragents
REDIS_URL               = (Railway Redis'ten kopyala)
JWT_SECRET              = en_az_32_karakter_rastgele_string
```

#### 🟡 OPSİYONEL

```
ANTHROPIC_MODEL         = claude-opus-4-5
ANTHROPIC_SONNET_MODEL  = claude-sonnet-4-6
ANTHROPIC_FAST_MODEL    = claude-haiku-4-5-20251001
DEFAULT_LEVERAGE        = 3
MAX_POSITION_SIZE_USDT  = 100.0
DEFAULT_RISK_PER_TRADE  = 0.02
FINNHUB_API_KEY         = (ücretsiz: finnhub.io)
LOG_LEVEL               = INFO
CORS_ORIGINS            = *
```

### 5. Deploy

Variables kaydedilince Railway otomatik deploy başlatır.  
**Deployments** sekmesinden build loglarını izle.

Deploy tamamlanınca:
- `https://your-app.railway.app/docs` → Swagger UI
- `https://your-app.railway.app/api/health` → Sağlık kontrolü

---

## 🔑 API Anahtarları Nasıl Alınır

### Anthropic Claude API

1. [console.anthropic.com](https://console.anthropic.com/settings/keys) → **API Keys**
2. **+ Create Key** → isim ver → kopyala
3. `sk-ant-api03-...` formatındadır
4. İlk kullanım için $5 kredi gelir

**Maliyet tahmini** (10 sembol, saatlik tarama):
- Haiku: ~$0.001 / analiz
- Sonnet: ~$0.01 / analiz
- Opus: ~$0.05 / analiz
- Toplam: ~$0.06 / sembol / gün → 10 sembol = ~$0.60/gün

### Binance Futures Testnet

1. [testnet.binancefuture.com](https://testnet.binancefuture.com)
2. **Log In with GitHub**
3. **Generate HMAC_SHA256 Key** → bir label yaz → **Generate**
4. `API Key` ve `Secret Key`yi kopyala

> ⚠️ Testnet'te gerçek para yok, işlemler simüle edilir.
> Mainnet'e geçmek için `BINANCE_TESTNET=false` yap ve gerçek Binance API key kullan.

### Finnhub (Opsiyonel)

1. [finnhub.io/dashboard](https://finnhub.io/dashboard) → ücretsiz kayıt
2. Dashboard'dan API key kopyala
3. Haber analizi için kullanılır, sistemin çalışması için zorunlu değil

---

## 📡 API Endpoints

### Analiz

```
POST /api/analysis/run          → Tam multi-agent analiz (SSE stream)
POST /api/analysis/technical    → Sadece teknik analiz
POST /api/analysis/sentiment    → Sadece duyarlılık analizi
GET  /api/analysis/history      → Geçmiş analizler
GET  /api/analysis/symbols      → Desteklenen semboller
```

### Pozisyonlar

```
GET    /api/positions            → Açık pozisyonlar
POST   /api/positions/open       → Manuel pozisyon aç
DELETE /api/positions/{sym}/close→ Pozisyonu kapat
GET    /api/positions/orders/open→ Açık emirler
```

### Market

```
GET /api/market/price/{symbol}   → Anlık fiyat
GET /api/market/klines/{symbol}  → Mum verileri
GET /api/market/funding/{symbol} → Funding rate
GET /api/market/ticker/{symbol}  → 24s istatistik
GET /api/market/account/balance  → Hesap bakiyesi
```

### Sinyaller

```
GET /api/signals                 → Son sinyaller
GET /api/signals/stats           → İstatistikler
```

---

## 🎯 SSE Streaming Kullanımı

Frontend'den analizi canlı takip et:

```javascript
const source = new EventSource('/api/analysis/run', {
  method: 'POST',  // Fetch + ReadableStream kullan
});

// veya fetch ile:
const response = await fetch('/api/analysis/run', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({symbol: 'BTCUSDT', interval: '1h'})
});

const reader = response.body.getReader();
while (true) {
  const {done, value} = await reader.read();
  if (done) break;
  const text = new TextDecoder().decode(value);
  // event: status | technical | sentiment | decision | complete | error
  console.log(text);
}
```

---

## ⚠️ Risk Uyarısı

Bu platform **eğitim ve araştırma amaçlıdır**.

- Testnet modunda çalıştır, gerçek para riske atma
- AI sinyalleri her zaman doğru değildir
- Kaldıraçlı işlemler sermayeni tamamen kaybettirebilir
- Mainnet'e geçmeden önce testnet'te en az 2 hafta test et

---

## 📁 Proje Yapısı

```
futuragents/
├── app/
│   ├── main.py                    # FastAPI app + lifespan
│   ├── core/
│   │   └── config.py              # Tüm ayarlar (env vars)
│   ├── db/
│   │   └── database.py            # MongoDB + Redis bağlantı
│   ├── api/routes/
│   │   ├── health.py              # /api/health
│   │   ├── auth.py                # JWT auth
│   │   ├── analysis.py            # Multi-agent analiz + SSE
│   │   ├── positions.py           # Binance pozisyon yönetimi
│   │   ├── market.py              # Market verisi
│   │   └── signals.py             # Sinyal geçmişi
│   ├── services/
│   │   ├── agents/
│   │   │   ├── orchestrator.py    # Opus — baş agent
│   │   │   ├── technical_agent.py # Haiku — teknik analiz
│   │   │   ├── sentiment_agent.py # Sonnet — duyarlılık
│   │   │   └── risk_agent.py      # Haiku — risk yönetimi
│   │   ├── binance/
│   │   │   └── client.py          # Binance Futures REST client
│   │   └── llm/
│   │       └── service.py         # Anthropic Claude wrapper
│   └── tasks/
│       └── scheduler.py           # Saatlik otomatik tarama
├── Dockerfile                     # Railway optimized
├── railway.toml                   # Railway config
├── pyproject.toml                 # Python dependencies
└── .env.example                   # Örnek env vars
```
