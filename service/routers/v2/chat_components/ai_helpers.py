from pydantic import BaseModel
from decimal import Decimal
import datetime
from datetime import date
from dataclasses import asdict, fields, is_dataclass
from typing import List, Dict, Any

from service.utils.timing import timing_decorator

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

def filter_product_fields(products: List[Dict[str, Any]], desired_fields: List[str]) -> List[Dict[str, Any]]:
    """
    Filters a list of product dictionaries to include only the desired fields.
    """
    filtered_products = []
    for product in products:
        filtered_product = {}
        for field in desired_fields:
            if field in product:
                filtered_product[field] = product[field]
            # Handle special mappings for AI fields if necessary
            elif field == "name" and "canonical_name" in product:
                filtered_product[field] = product["canonical_name"]
            elif field == "description" and "text_for_embedding" in product:
                filtered_product[field] = product["text_for_embedding"]
            elif field == "unit_of_measure" and "base_unit_type" in product:
                filtered_product[field] = product["base_unit_type"]
            # Add placeholders for fields not in DB but expected by AI if needed
            elif field == "image_url":
                filtered_product[field] = None # Or a default image URL
            elif field == "product_url":
                filtered_product[field] = None # Or a default product URL
            elif field == "quantity_value":
                filtered_product[field] = None # Or a default quantity value
        filtered_products.append(filtered_product)
    return filtered_products
