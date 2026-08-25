def normalize_universe(companies):
    out=[]; seen=set()
    for c in companies:
        s=str(c.get("symbol","")).strip(); typ=str(c.get("security_type","")).lower()
        if not s or s in seen: continue
        if typ and not any(x in typ for x in ("equity","stock","share")): continue
        seen.add(s); out.append({"symbol":s,"name":c.get("name","") or c.get("name_ar",""),"name_en":c.get("name_en","") or "","sector":c.get("sector","") or c.get("sector_name","")})
    return out
