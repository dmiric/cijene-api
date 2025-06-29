from pydantic import BaseModel
from decimal import Decimal
import datetime
from datetime import date
from dataclasses import asdict, fields, is_dataclass
from typing import List, Dict, Any
import google.protobuf.struct_pb2
from proto.marshal.collections import maps, repeated

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
    and other special types to a JSON-serializable dictionary.
    """
    # Handle google.protobuf.struct_pb2.Struct (for tool arguments)
    if isinstance(obj, google.protobuf.struct_pb2.Struct):
        return {k: convert_protobuf_to_dict(v) for k, v in obj.fields.items()}
    # Handle google.protobuf.struct_pb2.ListValue (for lists within tool arguments)
    elif isinstance(obj, google.protobuf.struct_pb2.ListValue):
        return [convert_protobuf_to_dict(v) for v in obj.values]
    # Handle google.protobuf.struct_pb2.Value (wraps primitives, structs, lists)
    elif hasattr(obj, 'kind') and obj.kind in obj.WhichOneof('kind'):
        kind = obj.WhichOneof('kind')
        if kind == 'string_value':
            # Check if the string value is actually a JSON string that needs parsing
            s_val = obj.string_value
            try:
                parsed_json = json.loads(s_val)
                # If it's a valid JSON, recursively convert it
                return convert_protobuf_to_dict(parsed_json)
            except (json.JSONDecodeError, TypeError):
                # Not a JSON string, return as is
                return s_val
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
            # Fallback for any unhandled kind
            return str(obj)

    # Handle proto.marshal.collections types (often wraps the above)
    elif isinstance(obj, maps.MapComposite):
        return {k: convert_protobuf_to_dict(v) for k, v in obj.items()}

    elif isinstance(obj, repeated.RepeatedComposite):
        return [convert_protobuf_to_dict(item) for item in obj]

    # Handle other Python types that need conversion for JSON
    elif isinstance(obj, dict):
        return {k: convert_protobuf_to_dict(v) for k, v in obj.items()}
    
    elif isinstance(obj, list):
        return [convert_protobuf_to_dict(v) for v in obj]

    elif isinstance(obj, (Decimal, datetime.datetime, datetime.date)):
        return str(obj) # Convert to string for JSON compatibility

    # If it's none of the above, return the object as is (e.g., str, int, float, bool, None)
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
