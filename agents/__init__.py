"""Microsoft Agent Framework (MAF) logical agents package."""

from agents.carrier_document_extraction_agent import CarrierDocumentExtractionAgent
from agents.cw1_create_agent import CW1CreateAgent
from agents.cw1_update_agent import CW1UpdateAgent
from agents.hitl_agent import HITLAgent
from agents.order_extraction_agent import OrderExtractionAgent
from agents.supervisor_agent import SupervisorAgent

__all__ = [
    "SupervisorAgent",
    "OrderExtractionAgent",
    "CarrierDocumentExtractionAgent",
    "CW1CreateAgent",
    "CW1UpdateAgent",
    "HITLAgent",
]
