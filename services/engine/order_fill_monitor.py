import asyncio
import logging
import ccxt.async_support as ccxt
from aiogram import Bot

from shared.lite_db import (
    get_pullback_items_by_status, 
    update_pullback_status, 
    save_trade,
    get_trade_by_signal_id
)

logger = logging.getLogger("OrderFillMonitor")

class OrderFillMonitor:
    def __init__(self, exchange: ccxt.Exchange, config):
        self.exchange = exchange
        self.config = config
        self.running = False

    async def start(self):
        self.running = True
        logger.info("Starting Order Fill Monitor for Live Limit Orders...")
        await self.fill_detection_loop()

    def stop(self):
        self.running = False

    async def fill_detection_loop(self):
        """
        Background loop running every 15-20 seconds to poll WAITING orders
        and execute fill logic when triggered by the exchange.
        """
        while self.running:
            try:
                waiting_orders = await get_pullback_items_by_status("WAITING")
                
                for item in waiting_orders:
                    exchange_order_id = item.get("exchange_order_id")
                    
                    if not exchange_order_id:
                        # Skip simulated/paper limit orders
                        continue
                        
                    try:
                        # Fetch real order status from the exchange
                        order = await self.exchange.fetch_order(exchange_order_id, item['symbol'])
                        
                        if order["status"] == "closed":
                            logger.info(f"[FILL DETECTION] Limit order filled for {item['symbol']} at {order.get('average', item['original_entry'])}")
                            await self.on_limit_filled(item, order)
                            
                        elif order["status"] == "partially_filled":
                            filled_amount = order.get("filled", 0)
                            if filled_amount > 0:
                                logger.warning(f"[FILL DETECTION] Limit order PARTIALLY FILLED for {item['symbol']}. Filled amount: {filled_amount}")
                                
                                import structlog
                                struct_logger = structlog.get_logger("telemetry")
                                
                                try:
                                    await self.exchange.cancel_order(exchange_order_id, item['symbol'])
                                    logger.info(f"Cancelled remaining partial order for {item['symbol']}")
                                except Exception as c_err:
                                    logger.error(f"Failed to cancel remaining partial order {exchange_order_id}: {c_err}")
                                
                                # Convert the partially filled order to an active trade
                                await self.on_limit_filled(item, order, is_partial=True)
                                
                                struct_logger.info(
                                    "PARTIAL_FILL_PROTECTED",
                                    symbol=item['symbol'],
                                    filled_amount=filled_amount,
                                    exchange_order_id=exchange_order_id,
                                    reason="Cancelled remaining and placed protective SL/TP"
                                )
                            
                        elif order["status"] in ["canceled", "rejected"]:
                            logger.info(f"[FILL DETECTION] Order {exchange_order_id} cancelled/rejected externally for {item['symbol']}")
                            await update_pullback_status(item['id'], "CANCELLED_EXTERNAL")
                            
                    except ccxt.OrderNotFound:
                        logger.warning(f"[FILL DETECTION] Order {exchange_order_id} not found on exchange for {item['symbol']}")
                    except Exception as e:
                        logger.error(f"[FILL DETECTION] Error fetching order for {item['symbol']}: {e}")
                        
            except Exception as e:
                logger.error(f"Error in background fill detection loop: {e}")
                
            await asyncio.sleep(20)  # Check every 20 seconds
            
    async def on_limit_filled(self, item: dict, order: dict, is_partial: bool = False):
        """
        Triggered when the limit entry is successfully executed on the exchange.
        """
        symbol = item['symbol']
        entry_price = order.get("average", item['original_entry'])
        amount = order.get("filled") if is_partial else order.get("amount", 0)
        
        if amount <= 0:
            logger.warning(f"Order for {symbol} filled with 0 amount. Aborting SL/TP placement.")
            return

        signal_id = f"live_pb_{item['id']}"
        existing_trade = await get_trade_by_signal_id(signal_id)
        if existing_trade:
            import structlog
            struct_logger = structlog.get_logger("telemetry")
            struct_logger.info("DUPLICATE_FILL_IGNORED", symbol=symbol, signal_id=signal_id, reason="Trade already exists")
            logger.info(f"Trade for {signal_id} already exists. Ignoring duplicate fill.")
            return

        # 1. Create record in trades DB
        await save_trade(
            signal_id=signal_id,
            symbol=symbol,
            direction=item['direction'],
            entry_price=entry_price,
            stop_loss=item['stop_loss'],
            take_profit_1=item['take_profit_1'],
            position_usd=entry_price * amount,
            reasoning="PARTIAL_LIMIT_FILL" if is_partial else "LIMIT_FILL",
            strategy="PULLBACK_LIVE",
            source="LIMIT"
        )
        
        import structlog
        struct_logger = structlog.get_logger("telemetry")
        sl_order_id = None
        tp_order_id = None
        
        # 2. Place SL as stop-market order
        try:
            sl_order = await self.exchange.create_order(
                symbol, "stop", "sell", 
                amount, None,
                params={'stopPrice': item['stop_loss']}
            )
            sl_order_id = sl_order.get('id')
            logger.info(f"Placed Stop Market SL for {symbol} at {item['stop_loss']}")
        except Exception as e:
            logger.error(f"Failed to place SL for {symbol}: {e}")
            struct_logger.error("SL_PLACE_FAILED", symbol=symbol, error=str(e))
            
        # 3. Place TP1 as limit order
        try:
            tp_amount = amount * 0.40
            tp_order = await self.exchange.create_order(
                symbol, "limit", "sell",
                tp_amount, item['take_profit_1']
            )
            tp_order_id = tp_order.get('id')
            logger.info(f"Placed Limit TP1 for {symbol} at {item['take_profit_1']}")
        except Exception as e:
            logger.error(f"Failed to place TP1 for {symbol}: {e}")
            struct_logger.error("TP_PLACE_FAILED", symbol=symbol, error=str(e))
            
        # 4. Update status in database
        await update_pullback_status(item['id'], "FILLED")
        
        struct_logger.info(
            "LIMIT_FILLED",
            exchange=self.exchange.id,
            exchange_order_id=order.get('id'),
            local_pullback_id=item['id'],
            filled_amount=amount,
            avg_fill_price=entry_price,
            remaining_amount=order.get("remaining", 0),
            fill_status="partially_filled" if is_partial else "closed",
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
            source="LIMIT"
        )
        
        # 5. Send Telegram alert
        await self.send_fill_alert(item, entry_price, is_partial)
        
    async def send_fill_alert(self, item: dict, entry_price: float, is_partial: bool = False):
        try:
            token = self.config.alerts.telegram_bot_token.get_secret_value()
            chat_id = self.config.alerts.telegram_chat_id
            
            if not token or not chat_id:
                return
                
            bot = Bot(token=token)
            status_text = "LIMIT FILLED" if not is_partial else "PARTIAL LIMIT FILLED"
            msg = (
                f"✅ <b>{status_text} | {item['symbol']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📥 <b>Entry Price:</b> ${entry_price:.4f}\n"
                f"🛑 <b>Stop Loss Placed:</b> ${item['stop_loss']:.4f}\n"
                f"🏁 <b>TP1 Placed:</b> ${item['take_profit_1']:.4f}\n\n"
                f"<i>Сделка открыта, защитные ордера выставлены на бирже.</i>"
            )
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            await bot.session.close()
        except Exception as e:
            logger.error(f"Failed to send LIMIT FILLED TG notification: {e}")
