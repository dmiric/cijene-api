from pydantic import BaseModel
from decimal import Decimal
import datetime
from datetime import date
from dataclasses import asdict, fields, is_dataclass

def pydantic_to_dict(obj):
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode='json')
    elif isinstance(obj, list):
        return [pydantic_to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: pydantic_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, date):
        return obj.isoformat()
    elif is_dataclass(obj):
        return pydantic_to_dict(asdict(obj))
    return obj
