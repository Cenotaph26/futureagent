"""
FuturAgents — Risk Management Agent
Portfolio riski, pozisyon büyüklüğü ve stop seviyesi hesaplar.
Güncel açık pozisyonları değerlendirerek max risk limiti korur.
Haiku modeli (hızlı hesap) kullanır.
"""
import logging
from datetime import datetime

from app.core.config import settings
from app.services.binance.client import get_binance_client
from app.services.llm.service import get_llm_service
from app.db.database import get_db

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen bir kripto futures portföy risk yöneticisisin.
Görevin: sermayeyi korumak, risk/ödül oranını optimize etmek.

Kelly Kriteri, ATR bazlı pozisyon boyutu ve portföy korelasyonunu dikkate alarak
kesin büyüklük ve stop seviyeleri belirle.

Yanıtı JSON formatında ver."""


class RiskManagementAgent:
    """
    - Pozisyon büyüklüğü hesapla (Kelly + ATR bazlı)
    - Portfolio riskini değerlendir
    - Max drawdown limitlerini kontrol et
    - Stop-loss / take-profit seviyeleri öner
    """

    def __init__(self):
        self.binance = get_binance_client()
        self.llm = get_llm_service()

    async def evaluate(
        self,
        symbol: str,
        signal: str,  # "LONG" | "SHORT"
        entry_price: float,
        technical_atr: float,
        technical_confidence: int,
        user_id: str = None,
    ) -> dict:
        logger.info(f"[RiskAgent] {symbol} {signal} pozisyon değerlendirme")

        # 1. Hesap bakiyesi
        balance_data = await self._get_usdt_balance()
        available_usdt = balance_data["available"]
        total_usdt = balance_data["total"]

        # 2. Mevcut açık pozisyonlar
        open_positions = await self.binance.get_positions()
        current_exposure = self._calc_exposure(open_positions, total_usdt)

        # 3. Pozisyon büyüklüğü hesapla
        sizing = self._calculate_position_size(
            available_usdt=available_usdt,
            entry_price=entry_price,
            atr=technical_atr,
            confidence=technical_confidence,
            current_exposure_pct=current_exposure["exposure_pct"],
        )

        # 4. Stop ve TP seviyeleri
        levels = self._calculate_levels(
            signal=signal,
            entry_price=entry_price,
            atr=technical_atr,
        )

        # 5. LLM risk değerlendirmesi
        risk_assessment = await self._llm_assess(
            symbol=symbol,
            signal=signal,
            sizing=sizing,
            levels=levels,
            balance=balance_data,
            exposure=current_exposure,
            confidence=technical_confidence,
        )

        return {
            "agent": "risk_management",
            "symbol": symbol,
            "signal": signal,
            "entry_price": entry_price,
            "balance": balance_data,
            "exposure": current_exposure,
            "sizing": sizing,
            "levels": levels,
            "llm_assessment": risk_assessment,
            "approved": risk_assessment.get("approved", False),
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def _get_usdt_balance(self) -> dict:
        try:
            balances = await self.binance.get_balance()
            usdt = next((b for b in balances if b["asset"] == "USDT"), None)
            if usdt:
                return {
                    "total": float(usdt.get("balance", 0)),
                    "available": float(usdt.get("availableBalance", 0)),
                    "unrealized_pnl": float(usdt.get("crossUnPnl", 0)),
                }
        except Exception as e:
            logger.warning(f"Bakiye alınamadı: {e}")
        # Testnet varsayılan bakiye
        return {"total": 10000.0, "available": 10000.0, "unrealized_pnl": 0.0}

    def _calc_exposure(self, positions: list[dict], total_usdt: float) -> dict:
        if not positions or total_usdt == 0:
            return {"total_notional": 0, "exposure_pct": 0, "position_count": 0}

        total_notional = sum(
            abs(float(p.get("positionAmt", 0))) * float(p.get("markPrice", 0))
            for p in positions
        )
        return {
            "total_notional": round(total_notional, 2),
            "exposure_pct": round(total_notional / total_usdt * 100, 1),
            "position_count": len(positions),
        }

    def _calculate_position_size(
        self,
        available_usdt: float,
        entry_price: float,
        atr: float,
        confidence: int,
        current_exposure_pct: float,
    ) -> dict:
        """
        ATR bazlı pozisyon boyutlandırma:
        - Risk = bakiyenin %2'si (ayarlanabilir)
        - Stop mesafesi = 1.5 × ATR
        - Confidence'a göre ölçekle (50-100 arası)
        """
        # Kalan riske izin verilen oran
        max_exposure = 80.0  # %80 max toplam exposure
        remaining_exposure = max(0, max_exposure - current_exposure_pct)
        
        # Base risk: bakiyenin %2'si, confidence'a göre ölçekli
        confidence_scalar = max(0.3, confidence / 100)
        risk_usdt = available_usdt * settings.DEFAULT_RISK_PER_TRADE * confidence_scalar

        # Stop mesafesi (1.5 ATR)
        stop_distance = 1.5 * atr
        stop_distance_pct = stop_distance / entry_price

        # Pozisyon büyüklüğü (USDT)
        position_usdt = min(
            risk_usdt / stop_distance_pct,
            settings.MAX_POSITION_SIZE_USDT,
            available_usdt * (remaining_exposure / 100),
        )

        # Kaldıraç ile miktarı hesapla
        leverage = settings.DEFAULT_LEVERAGE
        quantity = (position_usdt * leverage) / entry_price

        return {
            "risk_usdt": round(risk_usdt, 2),
            "position_usdt": round(position_usdt, 2),
            "quantity": round(quantity, 4),
            "leverage": leverage,
            "stop_distance_pct": round(stop_distance_pct * 100, 2),
            "risk_reward_target": 2.0,  # min 1:2
        }

    def _calculate_levels(self, signal: str, entry_price: float, atr: float) -> dict:
        """ATR bazlı stop-loss ve take-profit seviyeleri"""
        sl_mult = 1.5
        tp1_mult = 2.0
        tp2_mult = 4.0

        if signal == "LONG":
            return {
                "stop_loss": round(entry_price - atr * sl_mult, 4),
                "take_profit_1": round(entry_price + atr * tp1_mult, 4),
                "take_profit_2": round(entry_price + atr * tp2_mult, 4),
                "risk_pct": round(atr * sl_mult / entry_price * 100, 2),
                "reward_pct_tp1": round(atr * tp1_mult / entry_price * 100, 2),
            }
        else:  # SHORT
            return {
                "stop_loss": round(entry_price + atr * sl_mult, 4),
                "take_profit_1": round(entry_price - atr * tp1_mult, 4),
                "take_profit_2": round(entry_price - atr * tp2_mult, 4),
                "risk_pct": round(atr * sl_mult / entry_price * 100, 2),
                "reward_pct_tp1": round(atr * tp1_mult / entry_price * 100, 2),
            }

    async def _llm_assess(
        self,
        symbol: str,
        signal: str,
        sizing: dict,
        levels: dict,
        balance: dict,
        exposure: dict,
        confidence: int,
    ) -> dict:
        user_prompt = f"""
Sembol: {symbol} | Sinyal: {signal} | Güven: {confidence}/100

Hesap Durumu:
- Toplam USDT: {balance['total']:.2f}
- Kullanılabilir: {balance['available']:.2f}
- Gerçekleşmemiş PnL: {balance['unrealized_pnl']:.2f}

Mevcut Exposure:
- Toplam Notional: {exposure['total_notional']:.2f} USDT
- Exposure Oranı: {exposure['exposure_pct']:.1f}%
- Açık Pozisyon Sayısı: {exposure['position_count']}

Önerilen İşlem:
- Risk Miktarı: {sizing['risk_usdt']:.2f} USDT
- Pozisyon Büyüklüğü: {sizing['position_usdt']:.2f} USDT
- Kaldıraç: {sizing['leverage']}x
- Miktar: {sizing['quantity']}
- Stop Distance: {sizing['stop_distance_pct']:.2f}%

Seviyeler:
- Stop-Loss: {levels['stop_loss']}
- TP1: {levels['take_profit_1']}
- TP2: {levels['take_profit_2']}
- Risk/Ödül: 1:{levels['reward_pct_tp1'] / levels['risk_pct']:.1f}

Bu işlemi onaylıyor musun?
{{
  "approved": true | false,
  "risk_score": 0-100,
  "reasoning": "kısa gerekçe",
  "adjustments": {{"quantity": sayı_veya_null, "leverage": sayı_veya_null}},
  "warnings": ["uyarı listesi"]
}}
"""
        try:
            return await self.llm.complete_json(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                model_tier="fast",
            )
        except Exception as e:
            logger.error(f"Risk LLM hatası: {e}")
            return {"approved": False, "risk_score": 100, "error": str(e)}
