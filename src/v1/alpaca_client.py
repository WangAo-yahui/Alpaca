from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

from config import get_alpaca_credentials

def create_stock_data_client() -> StockHistoricalDataClient:
    """
    创建 Alpaca 股票行情客户端。

    用于获取股票和 ETF 的历史 K 线等市场数据。
    """
    api_key, secret_key,_ = get_alpaca_credentials()
    return StockHistoricalDataClient(
        api_key=api_key,
        secret_key=secret_key,
    )
def create_trading_client() -> TradingClient:
    """
    创建 Alpaca 交易账户客户端。

    当前版本只用它读取：
    - 账户信息
    - 当前持仓
    - 未完成订单

    暂时不使用它提交或取消订单。
    """
    api_key,secret_key, paper = get_alpaca_credentials()
    return TradingClient(
        api_key=api_key,
        secret_key=secret_key,
        paper=paper
    )