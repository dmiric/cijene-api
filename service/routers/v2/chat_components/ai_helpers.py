# service/routers/v2/chat_components/ai_helpers.py

import json
from pydantic import BaseModel
from decimal import Decimal
import datetime
from datetime import date
from dataclasses import asdict, fields, is_dataclass
from typing import List, Dict, Any

import google.protobuf.struct_pb2
from proto.marshal.collections import maps, repeated
from google.genai import types as genai_types # Import the missing type

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

def convert_protobuf_to_dict(obj: Any) -> Any:
    """
    Recursively converts Protobuf objects (including nested Struct, ListValue, Value)
    and also handles the top-level google.genai.types.FunctionCall object.
    """
    # --- THIS IS THE NEW, CRITICAL PART THAT WAS MISSING ---
    if isinstance(obj, genai_types.FunctionCall):
        # This is the main container object. We convert it to a dict,
        # and then recursively call this same function on its 'args' attribute.
        return {
            "name": obj.name,
            "args": convert_protobuf_to_dict(obj.args)
        }
    # --- END OF THE FIX ---

    # Your existing, correct logic for handling the contents of 'args' follows:
    elif isinstance(obj, google.protobuf.struct_pb2.Struct):
        return {k: convert_protobuf_to_dict(v) for k, v in obj.fields.items()}
    elif isinstance(obj, google.protobuf.struct_pb2.ListValue):
        return [convert_protobuf_to_dict(v) for v in obj.values]
    elif hasattr(obj, 'kind') and obj.kind in obj.WhichOneof('kind'):
        kind = obj.WhichOneof('kind')
        if kind == 'string_value':
            return obj.string_value
        elif kind == 'number_value':
            return obj.number_value
        elif kind == 'bool_value':
            return obj.bool_value
        elif kind == 'struct_value':
            return convert_protobuf_to_dict(obj.struct_value)
        elif kind == 'list_value':
            return convert_protobuf_to_dict(obj.list_value)
        elif kind == 'null_value':
            return None
        else:
            return str(obj)
    elif isinstance(obj, maps.MapComposite):
        return {k: convert_protobuf_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, repeated.RepeatedComposite):
        return [convert_protobuf_to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: convert_protobuf_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_protobuf_to_dict(v) for v in obj]
    elif isinstance(obj, (Decimal, datetime.datetime, datetime.date)):
        return str(obj)
    
    return obj

def filter_product_fields(products: List[Dict[str, Any]], desired_fields: List[str]) -> List[Dict[str, Any]]:
    # This function is not related to the bug and is fine as-is.
    filtered_products = []
    for product in products:
        filtered_product = {}
        for field in desired_fields:
            if field in product:
                filtered_product[field] = product[field]
            elif field == "name" and "canonical_name" in product:
                filtered_product[field] = product["canonical_name"]
            elif field == "description" and "text_for_embedding" in product:
                filtered_product[field] = product["text_for_embedding"]
            elif field == "unit_of_measure" and "base_unit_type" in product:
                filtered_product[field] = product["base_unit_type"]
            elif field == "image_url":
                filtered_product[field] = None
            elif field == "product_url":
                filtered_product[field] = None
            elif field == "quantity_value":
                filtered_product[field] = None
        filtered_products.append(filtered_product)
    return filtered_products