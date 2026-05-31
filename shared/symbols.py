import logging

logger = logging.getLogger(__name__)

def normalize_symbol(symbol: str) -> str:
    """
    Converts a standard symbol like 'XLM/USDT' or 'XLM-USDT' to a format 
    like 'XLMUSDT', which is often used by CCXT internally or WebSockets.
    """
    if not symbol:
        return ""
    return symbol.replace('/', '').replace('-', '').upper()

def denormalize_symbol(symbol: str) -> str:
    """
    Attempts to convert 'XLMUSDT' back to 'XLM/USDT'.
    Assumes standard quote currencies (USDT, USDC, BTC, ETH, BUSD).
    """
    if not symbol:
        return ""
    
    symbol_upper = symbol.upper()
    if '/' in symbol_upper:
        return symbol_upper
        
    quotes = ["USDT", "USDC", "BUSD", "BTC", "ETH"]
    for q in quotes:
        if symbol_upper.endswith(q):
            base = symbol_upper[:-len(q)]
            return f"{base}/{q}"
            
    # Fallback if no known quote found
    return symbol_upper

def is_symbol_supported(symbol: str, exchange_markets: dict) -> bool:
    """
    Safely checks if a symbol exists in the exchange's markets dictionary.
    Checks both normalized and denormalized forms.
    """
    if not exchange_markets:
        return False
        
    norm = normalize_symbol(symbol)
    denorm = denormalize_symbol(symbol)
    
    if symbol in exchange_markets:
        return True
    if denorm in exchange_markets:
        return True
    if norm in exchange_markets:
        return True
        
    return False
