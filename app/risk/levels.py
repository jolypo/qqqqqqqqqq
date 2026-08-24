def build_long_levels(low,high,atr,support,rr_min):
    entry=(low+high)/2
    if entry<=0 or atr<=0:return None
    sl=min(support if support and support<entry else entry-1.5*atr,entry-atr); risk=entry-sl
    if risk<=0:return None
    tp1=entry+risk*max(rr_min,1.5); tp2=entry+risk*max(rr_min+.8,2.3); tp3=entry+risk*max(rr_min+1.8,3.3)
    return {"entry_low":round(low,2),"entry_high":round(high,2),"entry":round(entry,2),"sl":round(sl,2),"tp1":round(tp1,2),"tp2":round(tp2,2),"tp3":round(tp3,2),"rr_tp1":round((tp1-entry)/risk,2)}
