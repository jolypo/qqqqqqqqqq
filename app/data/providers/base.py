from abc import ABC,abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
class Quote:
    symbol:str; name:str; name_en:str; price:float; change_percent:float=0; volume:float=0; value:float=0
    bid:float|None=None; ask:float|None=None; updated_at:datetime|None=None; is_delayed:bool=True; raw:dict[str,Any]|None=None

class DataProvider(ABC):
    @abstractmethod
    async def companies(self,market="TASI"):...
    @abstractmethod
    async def quote(self,symbol):...
    @abstractmethod
    async def market_summary(self):...
    @abstractmethod
    async def historical(self,symbol,days=250):...
