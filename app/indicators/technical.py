import numpy as np,pandas as pd
def add_indicators(df):
    x=df.copy()
    for c in ["open","high","low","close","volume"]: x[c]=pd.to_numeric(x[c],errors="coerce")
    x["ema9"]=x.close.ewm(span=9,adjust=False).mean(); x["ema20"]=x.close.ewm(span=20,adjust=False).mean(); x["ema50"]=x.close.ewm(span=50,adjust=False).mean(); x["ema200"]=x.close.ewm(span=200,adjust=False).mean()
    d=x.close.diff(); g=d.clip(lower=0).rolling(14).mean(); l=(-d.clip(upper=0)).rolling(14).mean(); x["rsi"]=100-(100/(1+g/l.replace(0,np.nan)))
    e12=x.close.ewm(span=12,adjust=False).mean(); e26=x.close.ewm(span=26,adjust=False).mean(); x["macd"]=e12-e26; x["macd_signal"]=x.macd.ewm(span=9,adjust=False).mean()
    pc=x.close.shift(1); tr=pd.concat([x.high-x.low,(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1); x["atr14"]=tr.rolling(14).mean()
    x["volume_avg20"]=x.volume.rolling(20).mean(); x["relative_volume"]=x.volume/x.volume_avg20.replace(0,np.nan); x["support20"]=x.low.rolling(20).min(); x["resistance20"]=x.high.rolling(20).max(); x["vwap20"]=(x.close*x.volume).rolling(20).sum()/x.volume.rolling(20).sum()
    return x
def latest_features(df):
    x=add_indicators(df).dropna()
    if x.empty:return {}
    r=x.iloc[-1]; keys=["close","ema9","ema20","ema50","ema200","rsi","macd","macd_signal","atr14","volume","volume_avg20","relative_volume","support20","resistance20","vwap20"]
    return {k:float(r[k]) for k in keys}
