from app.risk.levels import build_long_levels
def test_levels():
 x=build_long_levels(100,101,2,97,1.5);assert x["sl"]<x["entry"]<x["tp1"]<x["tp3"]
