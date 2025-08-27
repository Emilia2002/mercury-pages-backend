from bson import ObjectId

def serialize_doc(doc: dict) -> dict:
    """Convert MongoDB document into JSON-serializable dict."""
    if not doc:
        return None
    doc["_id"] = str(doc["_id"])  # ObjectId → str
    return doc

def serialize_list(docs: list) -> list:
    """Convert list of MongoDB documents."""
    return [serialize_doc(doc) for doc in docs]
