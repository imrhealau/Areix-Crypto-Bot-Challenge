import areix_io as aio
from areix_io.utils import create_report_folder, SideType
import pandas as pd
import numpy as np
import math

from sklearn.ensemble import RandomForestClassifier
from sklearn import preprocessing

from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score


PRED_DAYS = 2 
PCT_CHANGE = 0.004
'''
Data pre processing step
'''
def bollinger_band(data, n_lookback, n_std):
    hlc3 = (data['high'] + data['low'] + data['close']) / 3
    mean, std = hlc3.rolling(n_lookback).mean(), hlc3.rolling(n_lookback).std()
    upper = mean + n_std*std
    lower = mean - n_std*std
    return upper, lower

def rsi(data, time_window):
    diff = data.diff(1).dropna()        # diff in one field(one day)

    #this preservers dimensions off diff values
    up_chg = 0 * diff
    down_chg = 0 * diff
    
    # up change is equal to the positive difference, otherwise equal to zero
    up_chg[diff > 0] = diff[ diff>0 ]
    
    # down change is equal to negative deifference, otherwise equal to zero
    down_chg[diff < 0] = diff[ diff < 0 ]
    
    # check pandas documentation for ewm
    # https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.ewm.html
    # values are related to exponential decay
    # we set com=time_window-1 so we get decay alpha=1/time_window
    up_chg_avg   = up_chg.ewm(com=time_window-1 , min_periods=time_window).mean()
    down_chg_avg = down_chg.ewm(com=time_window-1 , min_periods=time_window).mean()
    
    rs = abs(up_chg_avg/down_chg_avg)
    rsi = 100 - 100/(1+rs)
    return rsi

def stochastic_rsi(data, k_window, d_window, window):
    # input to function is one column from df
    # containing closing price or whatever value we want to extract K and D from
    
    min_val  = data.rolling(window=window, center=False).min()
    max_val = data.rolling(window=window, center=False).max()
    
    stoch = ( (data - min_val) / (max_val - min_val) ) * 100
    
    K = stoch.rolling(window=k_window, center=False).mean() 
    #K = stoch
    D = K.rolling(window=d_window, center=False).mean() 
    return K, D

def mfi(data, period):
    
    hlc3 = (data['high'] + data['low'] + data['close']) / 3
    money_flow = hlc3 * data['volume']

    # Get all of the positive and negative money flow
    positive_flow = []
    negative_flow = []
    # Loop through typical price calculations
    for i in range(1, len(hlc3)):
        if hlc3[i] > hlc3[i-1]:
            positive_flow.append(money_flow[i-1])
            negative_flow.append(0)
        elif hlc3[i] < hlc3[i-1]:
            negative_flow.append(money_flow[i-1])
            positive_flow.append(0)
        else:
            positive_flow.append(0)
            negative_flow.append(0)
    # Storage for the last 14 days
    positive_mf = []
    negative_mf = []
    for i in range(period-1, len(positive_flow)):
        positive_mf.append(sum(positive_flow[i+1-period:i+1]))
    for i in range(period-1, len(negative_flow)):
        negative_mf.append(sum(negative_flow[i+1-period:i+1]))

    # Calculate the money flow index
    mfi = 100 * (np.array(positive_mf) / (np.array(positive_mf) + np.array(negative_mf)))
    for i in range(14):
        mfi = np.insert(mfi, 0, math.nan)
    
    return mfi

  

# DataFeed
def update_df(df):
    upper, lower = bollinger_band(df, 20, 1.5)
    df['rsi'] = rsi(df.close, 14)
    df['x_k'], df['x_d'] = stochastic_rsi(df['rsi'], 3, 3, 14)
    df['x_mfi'] = mfi(df, 14)

    df['ma10'] = df.close.rolling(10).mean()
    df['ma20'] = df.close.rolling(20).mean()
    df['ma50'] = df.close.rolling(50).mean()
    df['ma100'] = df.close.rolling(100).mean()

    df['x_ma10'] = (df.close - df.ma10) / df.close
    df['x_ma20'] = (df.close - df.ma20) / df.close
    df['x_ma50'] = (df.close - df.ma50) / df.close
    df['x_ma100'] = (df.close - df.ma100) / df.close

    df['x_delta_10'] = (df.ma10 - df.ma20) / df.close
    df['x_delta_20'] = (df.ma20 - df.ma50) / df.close
    df['x_delta_50'] = (df.ma50 - df.ma100) / df.close

    df['x_mom'] = df.close.pct_change(periods=2)
    df['x_bb_upper'] = (upper - df.close) / df.close
    df['x_bb_lower'] = (lower - df.close) / df.close
    df['x_bb_width'] = (upper - lower) / df.close
    return df

def get_X(data):
    return data.filter(like='x').values

def get_y(data):
    # use price change in the future 2 days as training label
    # -1 -- change  > -4%
    # 0 -- change is betweeen -4% & +4%
    # 1 -- change is > +4%
    y = data.close.pct_change(PRED_DAYS).shift(-PRED_DAYS)
    y[y.between(-PCT_CHANGE, PCT_CHANGE)] = 0 
    y[y > 0] = 1
    y[y < 0] = -1
    return y

def get_clean_Xy(df):
    X = get_X(df)
    y = get_y(df).values
    isnan = np.isnan(y)
    X = X[~isnan]
    y = y[~isnan]
    return X, y

class MLStrategy(aio.Strategy):
    num_pre_train = 3500

    def initialize(self):
        '''
        Model training step
        '''
        self.info('initialize')
        self.code = 'ENJ/USDT'

        # unwrap csv data for trading pair
        df = self.ctx.feed[self.code]

        self.ctx.feed[self.code] = update_df(df)

        self.y = get_y(df[self.num_pre_train-1:])

        # Status for each candle
        self.y_true = self.y.values

        self.clf = RandomForestClassifier()
    
        tmp = df.dropna().astype(float)
        X, y = get_clean_Xy(tmp[:self.num_pre_train])
        self.clf.fit(X, y)

        self.y_pred = []
    
    def before_trade(self, order):
        return True

    def on_order_ok(self, order):
        self.info(f"{order['side'].name} order {order['id']} ({order['order_type'].name}) executed #{order['quantity']} {order['code']} @ ${order['price']:2f}; Commission: ${order['commission']}; Available Cash: ${self.ctx.available_cash}; Position: #{self.ctx.get_quantity(order['code'])}; Gross P&L : ${order['pnl']}; Net P&L : ${order['pnl_net']}")
        if not order['is_open']:
            self.info(f"Trade closed, pnl: {order['pnl']}========")


    def on_market_start(self):
        # self.info('on_market_start')
        pass

    def on_market_close(self):
        # self.info('on_market_close')
        pass

    def on_order_timeout(self, order):
        self.info(f'on_order_timeout. Order: {order}')
        pass

    def finish(self):
        self.info('finish')

    def on_bar(self, tick):
        '''
        Model scoring and decisioning step
        '''
        bar_data = self.ctx.bar_data[self.code]
        hist_data = self.ctx.hist_data[self.code]

        if len(hist_data) < self.num_pre_train:
            return 
        
        open, high, low, close = bar_data.open, bar_data.high, bar_data.low, bar_data.close
        X = get_X(bar_data)
        forecast = self.clf.predict([X])[0]
        self.y_pred.append(forecast)

        self.ctx.cplot(forecast,'Forcast')
        self.ctx.cplot(self.y[tick],'Groundtruth')
        # self.info(f"focasing result: {forecast}")

        upper, lower = close * (1 + np.r_[1, -1]*PCT_CHANGE)
        curr_price = self.ctx.feed[self.code].at[tick, 'close']


        if forecast == 1 and not self.ctx.get_position(self.code):
            o1 = self.order_amount(code=self.code,amount=self.context.available_cash,side=SideType.BUY, asset_type='Crypto')
            self.info(f"BUY order {o1['id']} created #{o1['quantity']} @ {close:2f}")
            # osl = self.sell(code=self.code,quantity=o1['quantity'], price=lower, stop_price=lower, asset_type='Crypto')
            # self.info(f"STOPLOSS order {osl['id']} created #{osl['quantity']} @ {lower:2f}")
            
        elif forecast == -1 and self.ctx.get_position(self.code):
            o2 = self.order_amount(code=self.code,amount=self.context.trade_records[-1]['cost'],side=SideType.SELL, price=upper, asset_type='Crypto',ioc=True)
            self.info(f"SELL order {o2['id']} created #{o2['quantity']} @ {close:2f}")


# Run your strategy:

if __name__ == '__main__':
    aio.set_token('eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJleHAiOjE2NDMyMDUxMTQsImlhdCI6MTYxMjEwMTA1NCwic3ViIjoiZjQzZyAzNWc1dzRoNXc0aCB3NDVoNXc0aHc0NWhqNXdqamh3NTRnIHc0NSBnNXc0ICJ9.CLavu4bIpl64So0F0nYl6g3NfmXqopLfS_UC-9wOgrA') # Only need to run once

    base = create_report_folder()

    start_date = '2021-03-01'
    end_date = '2021-04-15'

    sdf = aio.CryptoDataFeed(
        symbols=['ENJ/USDT', 'BTC/USDT'], 
        start_date=start_date, 
        end_date=end_date,  
        interval='5m', 
        order_ascending=True, 
        store_path=base
    )
    feed, idx = sdf.fetch_data()
    benchmark = feed.pop('BTC/USDT')

    mytest = aio.BackTest(
        feed, 
        MLStrategy, 
        commission_rate=0.001, 
        min_commission=0, 
        trade_at='close', 
        benchmark=benchmark, 
        cash=55800, 
        tradedays=idx, 
        store_path=base
    )
    mytest.start()

    # Strategy: Random Forest Classifier

    prefix = ''
    stats = mytest.ctx.statistic.stats(pprint=True, annualization=252, risk_free=0.0442)
    '''
    Model evaluation step
    '''
    stats['model_name'] = 'Random Forest Classifier'
    stats['algorithm'] = ['RFC', 'Simple Moving Average', 'Bollinger Band', 'StochRSI', 'MFI']
    stats['model_measures'] = ['f1-score','accuracy']
    ytrue = mytest.ctx.strategy.y_true[:-PRED_DAYS]
    ypred = mytest.ctx.strategy.y_pred[:-PRED_DAYS]

    stats['f1-score'] = f1_score(ytrue, ypred,average='weighted')
    stats['accuracy'] = accuracy_score(ytrue, ypred)
    print(stats)

    mytest.contest_output()