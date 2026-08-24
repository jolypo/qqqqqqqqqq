from dataclasses import dataclass
from app.data.providers.base import Quote
@dataclass
class Candidate: quote:Quote; score:float; reasons:list[str]
def fast_score(q,regime):
    score=50.; reasons=[]
    if q.change_percent>1: score+=10; reasons.append("momentum")
    elif q.change_percent<-1: score-=8
    if q.volume>0: score+=5
    if q.bid is not None and q.ask is not None and q.price>0:
        sp=(q.ask-q.bid)/q.price*100
        if sp<=.20: score+=10; reasons.append("spread")
        elif sp>.75: score-=15
    if regime=="BULLISH": score+=5
    if regime=="BEARISH": score-=8
    return Candidate(q,max(0,min(100,score)),reasons)
