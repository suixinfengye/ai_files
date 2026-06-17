# -*- coding: utf-8 -*-
"""
Step-2 converter: From Step-1 JSON/JSONL to final CSV blocks.

Updates in this version:
- Support both JSON array and JSONL (one JSON object per line) inputs.
- Guarantee unique headers per test case: no duplicate column names.
- Write MarketSession Status into the fixed 'Status' column (no extra 'Status').

Author: M365 Copilot
"""

import csv
import json
import logging
import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple, Any, Set, Optional, Iterable
from csv0_bdd_convert import csv_bdd
from pathlib import Path

DEFAULT_CONTRACT_CODE = "ZSDF"
TEMPORARY_SCHEDULE_BY_SESSION = {
    # [startTime,tomStartTime,]
    'OpeningAuctionCall': ['T+1-T+3600-T21:45:00-T22:30:00','T21:45:00'], #PreOpen
    'Regular': ['T00:30:00-T+1-T22:00:00-T22:30:00','T21:45:00'], #Open
    'PostTrade': ['T00:30:00-T00:35:00-T+1-T22:30:00','T21:45:00'],
    'Close': ['T00:30:00-T00:35:00-T00:40:00-T+1','T00:40:00'],
    # 'SOD': ['T+1-T+2-T+3602-T22:30:00','T21:45:00'],
}

# --------------------------
# Global output drop columns
# --------------------------
DROPPED_FIELDS = {"SecurityExchange", "ProductComplex", "MsgType", "Text"}

# --------------------------
# Defaults / configuration
# --------------------------
DEFAULTS = {
    "DEFAULT_FIX_USER": "fix_tricgw101_1",
    "DEFAULT_BIN_USER": "bin_tripsg101_1",
    "DEFAULT_RMG_USER": "fixrm_epunarmg51_1",
    "DEFAULT_PROTOCOL": "FIX",
    "DEFAULT_SIDE": "Buy",  # semantic default
    "DEFAULT_ORDERQTY": "10",
    "DEFAULT_LIMIT_PRICE": "Func(LFC())",
    "DEFAULT_ORDTYPE": "Limit",
    "DEFAULT_OUTRIGHT": f"{DEFAULT_CONTRACT_CODE}_M5",
    "DEFAULT_CARRY": f"{DEFAULT_CONTRACT_CODE}_M4_M5",
    "DEFAULT_ACCOUNTTYPE": "3",
    "DEFAULT_NOPARTYIDS": "[{'PartyRole': '300', 'PartyIDSource': 'P', 'PartyID': '15'}, {'PartyRole': '11', 'PartyIDSource': 'D', 'PartyID': 'HFE'}, {'PartyRole': '81', 'PartyIDSource': 'D', 'PartyID': '1C'}, {'PartyRole': '301', 'PartyIDSource': 'P', 'PartyID': '8'}]",
    "DEFAULT_AMEND_NOPARTYIDS": "[{'PartyRole': '300', 'PartyIDSource': 'P', 'PartyID': '15'}]"
}

# FIX tag -> internal FieldName (comprehensive)
TAG_TO_FIELD: Dict[str, str] = {
    # Standard header/trailer
    "8": "BeginString",
    "9": "BodyLength",
    "35": "MsgType",
    "1128": "ApplVerID",
    "49": "SenderCompID",
    "56": "TargetCompID",
    "34": "MsgSeqNum",
    "43": "PossDupFlag",
    "97": "PossResend",
    "52": "SendingTime",
    "122": "OrigSendingTime",
    "10": "CheckSum",
    # Session/Admin/Other
    "98": "EncryptMethod",
    "108": "HeartBtInt",
    "789": "NextExpectedMsgSeqNum",
    "1400": "EncryptedPasswordMethod",
    "1402": "EncryptedPassword",
    "1404": "EncryptedNewPassword",
    "1137": "DefaultApplVerID",
    "1409": "SessionStatus",
    "112": "TestReqID",
    "7": "BeginSeqNo",
    "16": "EndSeqNo",
    "123": "GapFillFlag",
    "36": "NewSeqNo",
    "45": "RefSeqNum",
    "371": "RefTagID",
    "372": "RefMsgType",
    "373": "SessionRejectReason",
    "58": "Text",
    "379": "BusinessRejectRefID",
    "380": "BusinessRejectReason",
    "1472": "NewsID",
    "1473": "NewsCategory",
    "42": "OrigTime",
    "148": "Headline",
    "33": "NoLinesOfText",

    # Security Definition (c/d)
    "320": "SecurityReqID",
    "321": "SecurityRequestType",
    "207": "SecurityExchange",
    "1227": "ProductComplex",
    "55": "Symbol",
    "167": "SecurityType",
    "762": "SecuritySubType",
    "541": "MaturityDate",
    "202": "StrikePrice",
    "201": "PutOrCall",
    "555": "NoLegs",
    "602": "LegSecurityID",
    "603": "LegSecurityIDSource",
    "623": "LegRatioQty",
    "624": "LegSide",
    "566": "LegPrice",
    "323": "SecurityResponseType",

    # New Order Single / Amend / Cancel common
    "11": "ClOrdID",
    "48": "SecurityID",
    "22": "SecurityIDSource",
    "54": "Side",
    "38": "OrderQty",
    "40": "OrderType",  # NOTE: internally we'll normalize to 'OrdType' when emitting header/values
    "44": "Price",
    "59": "TIF",  # TimeInForce -> 'TIF' internal name
    "432": "ExpireDate",
    "1138": "DisplayQty",
    "99": "StopPx",
    "1100": "TriggerType",
    "1102": "TriggerPrice",
    "1107": "TriggerPriceType",
    "1110": "TriggerNewPrice",
    "1111": "TriggerOrderType",
    "18": "ExecInst",

    # Parties block
    "453": "NoPartyIDs",
    "448": "PartyID",
    "447": "PartyIDSource",
    "452": "PartyRole",

    # Regulatory / attributes
    "581": "AccountType",
    "528": "OrderCapacity",
    "529": "OrderRestrictions",
    "1724": "OrderOrigination",
    "2362": "SelfMatchPreventionID",
    "2593": "NoOrderAttributes",
    "2594": "OrderAttributeTypes",
    "2595": "OrderAttributeValues",
    "60": "TransactTime",

    # Amend / Cancel identifiers
    "41": "PreviousID",  # prefer internal PreviousID (OrigClOrdID)
    "37": "ReferenceBy",  # prefer internal ReferenceBy (OrderID)

    # Mass Cancel
    "530": "MassCancelRequestType",

    # Mass Cancel Report (r)
    "1369": "MassActionReportID",
    "531": "MassCancelResponse",
    "532": "MassCancelRejectReason",
    "533": "TotalAffectedOrders",

    # Order Cancel Reject (9)
    "39": "OrdStatus",
    "434": "CxlRejResponseTo",
    "102": "CxlRejReason",
    "1328": "RejectText",
    "1819": "RelatedHighPrice",
    "1820": "RelatedLowPrice",

    # Execution Report (8)
    "526": "SecondaryClOrdID",
    "880": "TrdMatchID",
    "17": "ExecID",
    "19": "ExecRefID",
    "150": "ExecType",
    "1115": "OrderCategory",
    "32": "LastQty",
    "31": "LastPx",
    "151": "LeavesQty",
    "14": "CumQty",
    "797": "CopyMsgIndicator",
    "378": "ExecRestatementReason",
    "2431": "ExecTypeReason",
}

MSGTYPE_MAP_REQUEST_FIX = {
    # Session/Admin (client -> exchange)
    "A": "Logon",
    "0": "Heartbeat",
    "1": "TestRequest",
    "2": "ResendRequest",
    "4": "SequenceReset",
    "5": "Logout",

    # Application
    "c": "SecurityDefinitionRequest",
    "D": "New",       # NewOrderSingle
    "G": "Amend",     # OrderCancelReplaceRequest
    "F": "Cancel",    # OrderCancelRequest
    "q": "MassCancel",# OrderMassCancelRequest

    # Optional RFQ/Quote flows
    "R": "QuoteRequest",
}
MSGTYPE_MAP_RESPONSE_FIX = {
    # Session/Admin (exchange -> client)
    "A": "Logon",
    "0": "Heartbeat",
    "1": "TestRequest",
    "2": "ResendRequest",
    "4": "SequenceReset",
    "5": "Logout",
    "3": "Reject",
    "j": "BusinessMessageReject",
    "B": "News",

    # Application
    "d": "SecurityDefinition",
    "8": "ExecutionReport",
    "9": "OrderCancelReject",
    "r": "OrderMassCancelReport",

    # Optional RFQ/Quote responses
    "AJ": "QuoteResponse",
    "AG": "QuoteRequestReject",
}

MSGTYPE_MAP_REQUEST_BIN = {
    # Session/Admin
    "Logon": "Logon",
    "Heartbeat": "Heartbeat",
    "TestRequest": "TestRequest",
    "ResendRequest": "ResendRequest",
    "SequenceReset": "SequenceReset",
    "Logout": "Logout",

    # Application
    "SecurityDefinitionRequest": "SecurityDefinitionRequest",
    "NewOrderSingle": "New",
    "AmendOrder": "Amend",
    "CancelOrder": "Cancel",
    "MassCancelRequest": "MassCancel",
    "MassQuote": "MassQuote",
    "QuoteRequest": "QuoteRequest",
    "MMPResetRequest": "MMPResetRequest",
}
MSGTYPE_MAP_RESPONSE_BIN = {
    # Session/Admin
    "Logout": "Logout",
    "Reject": "Reject",
    "BusinessMessageReject": "BusinessMessageReject",
    "News": "News",

    # Application
    "SecurityDefinition": "SecurityDefinition",
    "ExecutionReport": "ExecutionReport",
    "OrderCancelRejected": "OrderCancelRejected",
    "OrderAmendRejected": "OrderAmendRejected",
    "MassCancelReport": "MassCancelReport",
    "MassQuoteAck": "MassQuoteAck",
    "QuoteRequestAck": "QuoteRequestAck",
    "MMPResetAck": "MMPResetAck",
}

# BIN FieldName -> internal FieldName (non-tag binary protocols)
NAME_TO_FIELD = {
    # Session/Admin/Common
    "Password": "Password",
    "NewPassword": "NewPassword",
    "NextExpectedMsgSeqNum": "NextExpectedMsgSeqNum",
    "SessionStatus": "SessionStatus",
    "HeartbeatInterval": "HeartbeatInterval",
    "ReferenceTestRequestID": "ReferenceTestRequestID",
    "TestRequestID": "TestRequestID",
    "StartSequence": "StartSequence",
    "EndSequence": "EndSequence",
    "GapFill": "GapFill",
    "NewSequenceNumber": "NewSequenceNumber",
    "LogoutText": "LogoutText",
    "ReferenceSequence": "ReferenceSequence",
    "ReferenceFieldID": "ReferenceFieldID",
    "ReferenceMessageType": "ReferenceMessageType",
    "MessageRejectCode": "MessageRejectCode",
    "ReferenceFieldName": "ReferenceFieldName",
    "BusinessRejectRefID": "BusinessRejectRefID",
    "BusinessRejectReason": "BusinessRejectReason",
    "NewsID": "NewsID",
    "NewsCategory": "NewsCategory",
    "OriginationTime": "OriginationTime",
    "NewsText": "NewsText",

    # Security Definition
    "SecurityRequestID": "SecurityRequestID",
    "SecurityExchange": "SecurityExchange",
    "ProductComplex": "ProductComplex",
    "Symbol": "Symbol",
    "SecurityType": "SecurityType",
    "SecuritySubType": "SecuritySubType",
    "MaturityDate": "MaturityDate",
    "StrikePrice": "StrikePrice",
    "PutOrCall": "PutOrCall",
    "NoLegs": "NoLegs",
    "LegsBodyFieldsPresenceMap": "LegsBodyFieldsPresenceMap",
    "LegSecurityID": "LegSecurityID",
    "LegSide": "LegSide",
    "LegRatio": "LegRatio",
    "LegPrice": "LegPrice",
    "SecurityResponseType": "SecurityResponseType",
    "SecurityID": "SecurityID",
    "ContractCode": "ContractCode",

    # New/Amend/Cancel
    "ClientOrderID": "ID",
    "OriginalClientOrderID": "PreviousID",
    "OrderID": "ReferenceBy",
    "TransactTime": "TransactTime",
    "Side": "Side",
    "OrderQuantity": "OrderQty",
    "OrderType": "OrderType",
    "OrderPrice": "Price",
    # "TimeInForce": "TIF",
    "ExpiryDate": "ExpireDate",
    "OrderCapacity": "OrderCapacity",
    "OrderRestrictions": "OrderRestrictions",
    "AccountType": "AccountType",
    "BrokerClientID": "BrokerClientID",
    "Text": "Text",

    # Risk/flags
    "CancelOnDisconnect": "CancelOnDisconnect",
    "DirectElectronicAccess": "DirectElectronicAccess",
    "AggregatedOrder": "AggregatedOrder",
    "PendingAllocationOrder": "PendingAllocationOrder",
    "LiquidityProvisionOrder": "LiquidityProvisionOrder",
    "RiskReductionOrder": "RiskReductionOrder",

    # Triggering
    "TriggerPrice": "StopPx",
    "TriggerPriceType": "TriggerPriceType",
    "TriggerType": "TriggerType",
    "TriggerNewPrice": "TriggerNewPrice",

    # Iceberg
    "DisplayQuantity": "DisplayQty",

    # Mass cancel
    "MassCancelRequestType": "MassCancelRequestType",
    "MassCancelScope": "MassCancelScope",
    "TotalAffectedOrders": "TotalAffectedOrders",
    "MassActionReportID": "MassActionReportID",
    "MassCancelResponse": "MassCancelResponse",
    "MassCancelRejectReason": "MassCancelRejectReason",
    "QuoteID": "QuoteID",

    # Execution & rejects
    "ExecID": "ExecID",
    "ExecType": "ExecType",
    "OrderStatus": "OrderStatus",
    "LastQuantity": "LastQty",
    "LastPrice": "LastPx",
    "CumulativeQuantity": "CumQty",
    "LeavesQuantity": "LeavesQty",
    "ReasonText": "RejectText",
    "RelatedHighPrice": "RelatedHighPrice",
    "RelatedLowPrice": "RelatedLowPrice",
    "OrderCategory": "OrderCategory",
    "AggressorIndicator": "AggressorIndicator",
    "NoLegs": "NoLegs",
    "LegAllocID": "LegAllocID",
    "LegLastPrice": "LegLastPx",
    "LegLastQuantity": "LegLastQty",
    "ExecRestatementReason": "ExecRestatementReason",
    "ExecTypeReason": "ExecTypeReason",
    "SecondaryClientOrderID": "SecondaryClOrdID",

    # Cancel/Amend Rejects
    "CancelRejectCode": "CancelRejectCode",
    "AmendRejectCode": "AmendRejectCode",

    # SEP/extra IDs
    "SelfMatchPreventionID": "SelfMatchPreventionID",
    "ClientIDShortCode": "ClientIDShortCode",
    "LegalEntityID": "LegalEntityID",
    "ProprietaryClientID": "ProprietaryClientID",
    "EnteringFirm": "EnteringFirm",
    "OriginationTrader": "OriginationTrader",
    "CustomerAccount": "CustomerAccount",
    "CorrespondentBroker": "CorrespondentBroker",
    "MarketMaker": "MarketMaker",
    "DecisionMaker": "DecisionMaker",
    "InvestmentDecisionWithinFirm": "InvestmentDecisionWithinFirm",
    "ExecutionDecisionWithinFirm": "ExecutionDecisionWithinFirm",
    "InvestmentDecisionCountry": "InvestmentDecisionCountry",
    "ExecutionDecisionCountry": "ExecutionDecisionCountry",
    "ClientBranchCountry": "ClientBranchCountry",
}

# ---------------------------------------------
# RMG (Risk Management Gateway) Message Type maps
# ---------------------------------------------
MSGTYPE_MAP_REQUEST_RMG = {
    # Session/Admin
    "A": "Logon",
    "0": "Heartbeat",
    "1": "TestRequest",
    "2": "ResendRequest",
    "4": "SequenceReset",
    "5": "Logout",

    # Application
    "CX": "PartyDetailsDefinitionRequest",
    "CF": "PartyDetailsListRequest",
    "CS": "PartyRiskLimitsDefinitionRequest",
    "CL": "PartyRiskLimitsRequest",
    "DH": "PartyActionRequest",

    # Future use
    "DA": "PartyEntitlementsDefinitionRequest",
}
MSGTYPE_MAP_RESPONSE_RMG = {
    # Session/Admin
    "A": "Logon",
    "0": "Heartbeat",
    "1": "TestRequest",
    "2": "ResendRequest",
    "4": "SequenceReset",
    "5": "Logout",
    "3": "Reject",
    "j": "BusinessMessageReject",
    "B": "News",

    # Application (exchange -> client)
    "CY": "PartyDetailsDefinitionRequestAck",
    "CG": "PartyDetailsListReport",
    "CT": "PartyRiskLimitsDefinitionRequestAck",
    "CM": "PartyRiskLimitsReport",
    "DI": "PartyActionReport",

    # Future use
    "DB": "PartyEntitlementsDefinitionRequestAck",
}

RMG_TAG_TO_FIELD: Dict[str, str] = {
    # Standard header/trailer
    "8": "BeginString",
    "9": "BodyLength",
    "35": "MsgType",
    "1128": "ApplVerID",
    "49": "SenderCompID",
    "56": "TargetCompID",
    "34": "MsgSeqNum",
    "43": "PossDupFlag",
    "97": "PossResend",
    "52": "SendingTime",
    "122": "OrigSendingTime",
    "10": "CheckSum",

    # Session/Admin/Other
    "98": "EncryptMethod",
    "108": "HeartBtInt",
    "789": "NextExpectedMsgSeqNum",
    "1400": "EncryptedPasswordMethod",
    "1402": "EncryptedPassword",
    "1404": "EncryptedNewPassword",
    "1137": "DefaultApplVerID",
    "1409": "SessionStatus",
    "112": "TestReqID",
    "7": "BeginSeqNo",
    "16": "EndSeqNo",
    "123": "GapFillFlag",
    "36": "NewSeqNo",
    "45": "RefSeqNum",
    "371": "RefTagID",
    "372": "RefMsgType",
    "373": "SessionRejectReason",
    "379": "BusinessRejectRefID",
    "380": "BusinessRejectReason",
    "1472": "NewsID",
    "1473": "NewsCategory",
    "42": "OrigTime",
    "148": "Headline",
    "33": "NoLinesOfText",
    "58": "Text",

    # Party Details Definition
    "1505": "PartyDetailsListRequestID",
    "1676": "NoPartyUpdates",
    "1324": "ListUpdateAction",
    "1671": "NoPartyDetails",
    "1691": "PartyDetailID",
    "1692": "PartyDetailIDSource",
    "1693": "PartyDetailRole",
    "1562": "NoRelatedPartyDetailID",
    "1563": "RelatedPartyDetailID",
    "1564": "RelatedPartyDetailIDSource",
    "1565": "RelatedPartyDetailRole",
    "1675": "RelatedPartyDetailRoleQualifier",
    "1878": "PartyDetailRequestStatus",
    "1877": "PartyDetailRequestResult",

    # Party Details List
    "1510": "PartyDetailsListReportID",
    "1511": "RequestResult",
    "1512": "TotNoParties",
    "893": "LastFragment",
    "1672": "PartyDetailStatus",

    # Risk Limits Definition
    "1666": "RiskLimitRequestID",
    "1761": "RiskLimitRequestResult",
    "1762": "RiskLimitRequestStatus",
    "1677": "NoPartyRiskLimits",
    "1669": "NoRiskLimits",
    "1529": "NoRiskLimitTypes",
    "1530": "RiskLimitType",
    "1531": "RiskLimitAmount",
    "1532": "RiskLimitCurrency",

    # Risk Limits Request/Report
    "1760": "RiskLimitRequestType",
    "1667": "RiskLimitReportID",
    "325": "UnsolicitedIndicator",
    "1694": "NoPartyDetailSubIDs",
    "1695": "PartyDetailSubID",
    "1696": "PartyDetailISubIDType",
    "1767": "RiskLimitAction",
    "1765": "RiskLimitUtilizationPercent",
    "1559": "NoRiskWarningLevels",
    "1769": "RiskWarningLevelAction",
    "1560": "RiskWarningLevelPercent",
    "1561": "RiskWarningLevelName",

    # Instrument Scope
    "1534": "NoRiskInstrumentScopes",
    "1535": "InstrumentScopeOperator",
    "1616": "InstrumentScopeSecurityExchange",
    "1544": "InstrumentScopeProductComplex",
    "1545": "InstrumentScopeSecurityGroup",
    "1547": "InstrumentScopeSecurityType",
    "1548": "InstrumentScopeSecuritySubType",
    "1556": "InstrumentScopeSecurityDesc",
    "1536": "InstrumentScopeSymbol",

    # MMP
    "2336": "RiskLimitVelocityPeriod",
    "2337": "RiskLimitVelocityUnit",

    # Party Action
    "2328": "PartyActionRequestID",
    "2329": "PartyActionType",
    "2331": "PartyActionReportID",
    "2332": "PartyActionResponse",
    "2333": "PartyActionRejectReason",

    # Relationships
    "453": "NoPartyIDs",
    "448": "PartyID",
    "447": "PartyIDSource",
    "452": "PartyRole",
    "1514": "NoPartyRelationships",
    "1515": "PartyRelationship",

    # Common
    "1328": "RejectText",
    "60": "TransactTime",

    # Entitlements (future)
    "1770": "EntitlementRequestID",
    "1772": "NoPartyEntitlements",
    "1773": "NoEntitlements",
    "1774": "EntitlementIndicator",
    "1777": "NoEntitlementAttrib",
    "1778": "EntitlementAttribType",
    "1779": "EntitlementAttribDataType",
    "1780": "EntitlementAttribValue",
    "1882": "EntitlementRequestStatus",
    "1881": "EntitlementRequestResult",
}

# Alias lookup across all protocols
ACTION_ALIAS: Dict[str, str] = MSGTYPE_MAP_REQUEST_FIX | MSGTYPE_MAP_RESPONSE_FIX | MSGTYPE_MAP_REQUEST_BIN | MSGTYPE_MAP_RESPONSE_BIN | MSGTYPE_MAP_REQUEST_RMG | MSGTYPE_MAP_RESPONSE_RMG
ACTION_ALIAS["MarketSession"] = "MarketSession"

REQUEST_TYPES: Set[str] = set(MSGTYPE_MAP_REQUEST_FIX) | set(MSGTYPE_MAP_REQUEST_BIN) | set(MSGTYPE_MAP_REQUEST_RMG)
REQUEST_TYPES.add("MarketSession")

RESPONSE_TYPES: Set[str] = set(MSGTYPE_MAP_RESPONSE_FIX) | set(MSGTYPE_MAP_RESPONSE_BIN) | set(MSGTYPE_MAP_RESPONSE_RMG)

# --------------------------
# Utility functions
# --------------------------

# --------------------------
# ordertype normalization
# --------------------------
ORDER_SIZE_NUMERIC_MAP = {"1": "Buy", "2": "Sell"}

def normalize_order_size_value(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if s == "":
        return ""
    if re.fullmatch(r"-?\d+", s):
        return ORDER_SIZE_NUMERIC_MAP.get(s, s)
    return s

# --------------------------
# ordertype normalization
# --------------------------
ORDER_TYPE_NUMERIC_MAP = {"1": "Market", "2": "Limit", "3": "StopMarket", "4": "StopLimit"}

def normalize_order_type_value(value: Any) -> str:
    if value is None:
        return "Limit"
    s = str(value).strip()
    if s == "":
        return "Limit"
    if re.fullmatch(r"-?\d+", s):
        return ORDER_TYPE_NUMERIC_MAP.get(s, s)
    return s

# --------------------------
# TIF / TimeInForce normalization
# --------------------------
TIF_NUMERIC_MAP = {"0": "Day", "1": "GTC", "3": "IOC", "4": "FOK", "6": "GTD"}

def normalize_tif_value(value: Any) -> str:
    """Normalize TimeInForce/TIF values.

    Rules:
      - If value is numeric (tag59 semantics): 0->Day, 1->GTC, 3->IOC, 4->FOK, 6->GTD
      - If value is text (e.g. Day/GTC/IOC/FOK/GTD): keep as-is
    """
    if value is None:
        return "Day"
    s = str(value).strip()
    if s == "":
        return "Day"
    if re.fullmatch(r"-?\d+", s):
        return TIF_NUMERIC_MAP.get(s, s)
    return s


def parse_json_or_jsonl(path: str) -> List[Dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    s = text.lstrip()
    if s.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON root must be an array of test cases.")
        return data

    cases: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSONL on line {lineno},{line}: {e.msg}") from e
        if not isinstance(obj, dict):
            raise ValueError(f"JSONL line {lineno},{line} must be a JSON object.")

        test_case_id = obj.get("testCaseId")
        if not test_case_id or not str(test_case_id).isdigit():
            logging.error(f"wrong JSONL line {lineno},{line}")
            continue
        cases.append(obj)
    return cases

def load_cases_any(input_path: str,skeleton_confidence_threshold:float, enrich_confidence_threshold:float,assert_confidence_threshold:float, verify_confidence_threshold: float) -> List[Dict[str, Any]]:
    """
    Load test cases from either:
      - JSON array file, or
      - JSONL (each line is a standalone JSON object)

    Filter rule:
      Keep a test case ONLY if every row in 'rows' satisfies:
        row.confidence >= confidence AND row.verify_confidence >= verify_confidence.
      If any row does not meet the threshold, drop the entire test case.
    """
    def case_passes_thresholds(case_obj: Dict[str, Any]) -> bool:
        rows: Iterable[Dict[str, Any]] = case_obj.get("rows", []) or []
        # If there are no rows, treat as NOT passing (can adjust to True if you prefer)
        if not rows:
            return False
        for r in rows:
            rc = 0
            rvc = 0
            try:
                if (not r.get("steps")) and (not r.get("testData")) and len(rows) > 1:
                    continue
                rc = float(r.get("skeleton_confidence", 0) or 0)
                rc = float(r.get("enrich_confidence", 0) or 0)
                rc = float(r.get("assert_confidence", 0) or 0)
                rvc = float(r.get("verify_confidence", 0) or 0)
            except Exception as e:
                logging.error(f"testcase: case_obj:{case_obj},error:{e}")
            if not (rc >= skeleton_confidence_threshold and rc >= enrich_confidence_threshold and rc >= assert_confidence_threshold and rvc >= verify_confidence_threshold):
                return False
        return True

    all_cases = parse_json_or_jsonl(input_path)
    filtered = [c for c in all_cases if case_passes_thresholds(c)]
    return filtered

def is_all_content_empty(case_obj: Dict[str, Any])->bool:
    if not case_obj.get("testCaseId") or case_obj.get("testCaseId") == None:
        return True
    rows = case_obj.get("rows",[])
    for r in rows:
        content = r.get("content", "")
        if len(str(content).strip())>30:
            return False
    return True

def resolve_msgtype_full(code_or_name: str) -> str:
    """Return full message type name from FIX/RMG code or passthrough if already full."""
    if not code_or_name:
        return ""
    if code_or_name in ACTION_ALIAS:
        return code_or_name
    return code_or_name


def alias_action(full_name: str) -> str:
    """Apply alias for request actions (e.g., NewOrderSingle -> New)."""
    return ACTION_ALIAS.get(full_name, '')


def step_type_of(action_full: str) -> str:
    """Decide 'send' or 'assertion' from full action name."""
    if action_full in REQUEST_TYPES:
        return "send"
    if action_full in RESPONSE_TYPES:
        return "assertion"
    if action_full == "MarketSession":
        return "send"
    return "send"


def detect_protocol_and_user(raw_line: str, action_full: str) -> Tuple[str, str]:
    """Detect protocol and select user accordingly."""
    protocol = "BIN" if re.search(r"\bBIN\b|\bbinary\b", raw_line, flags=re.IGNORECASE) else DEFAULTS["DEFAULT_PROTOCOL"]
    if protocol == "BIN":
        user = DEFAULTS["DEFAULT_BIN_USER"]
    else:
        if action_full in set(MSGTYPE_MAP_REQUEST_RMG.values()) | set(MSGTYPE_MAP_RESPONSE_RMG.values()):
            user = DEFAULTS["DEFAULT_RMG_USER"]
        else:
            user = DEFAULTS["DEFAULT_FIX_USER"]
    return protocol, user


def parse_tag_pairs(parts: List[str]) -> Tuple[Dict[str, str], Optional[str]]:
    """Parse a list of 'k=v' parts into a dict; return (fields, msgtype_code)."""
    fields: Dict[str, str] = {}
    msgtype_code: Optional[str] = None
    for p in parts:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k == "35":
            msgtype_code = v
        fields[k] = v
    return fields, msgtype_code





def collect_symbols(parsed_msgs: List[Dict[str, Any]]) -> Set[str]:
    """Collect unique Symbol values from parsed messages."""
    symbols: Set[str] = set()
    for m in parsed_msgs:
        if m["action_full"] == "MarketSession":
            continue
        sym = m["fields"].get("Symbol")
        if sym:
            symbols.add(sym)
    return symbols


def stable_field_order(dynamic_fields: Set[str]) -> List[str]:
    """Return a stable order prioritizing common fields first, then alphabetical."""
    priority = [
        # NOTE: 'Status' is part of fixed prefix; DO NOT put it here
        "Symbol", "SecurityID",
        "Side", "OrderQty", "Price", "OrderType",
        "Text",
        "MassCancelRequestType",
        # "SecurityExchange", "ProductComplex",
        "Time",
        "TIF",  # keep TIF if used by inputs
        # "ContractCode",
    ]
    ordered: List[str] = []
    seen: Set[str] = set()

    for f in priority:
        if f in DROPPED_FIELDS:
            continue
        if f in dynamic_fields and f not in seen:
            ordered.append(f)
            seen.add(f)
    for f in sorted(dynamic_fields):
        if f in DROPPED_FIELDS:
            continue
        if f not in seen:
            ordered.append(f)
            seen.add(f)
    return ordered


def get_action_by_previous_id(parsed_msgs: List[Dict[str, Any]], previous_id: str) -> str:
    for row in parsed_msgs:
        fields = row.get("fields", {})
        if fields.get("#_id") == previous_id:
            return row.get("action", "")
    return ""


def get_fix_tag_value(raw: str, tag: str) -> str:
    pattern = rf'(?:^|\|){re.escape(tag)}=([^|]*)(?=\||$)'
    match = re.search(pattern, raw)
    return match.group(1) if match else ''


def gen_dummpy_action_to_update_lfc(raw:str,test_case: Dict[str, Any])->str:
    symbol = get_fix_tag_value(raw, '55')
    price = get_fix_tag_value(raw, '44')
    tag_value = get_fix_tag_value(raw, '54')
    if tag_value == '1':
        return f'35=D|55={symbol}|54=1|38=1|40=2|44=CalcValue({price}-110)|58=update lfc to let stop order pass'
    elif tag_value == '2':
        return f'35=D|55={symbol}|54=2|38=1|40=2|44=CalcValue({price}+110)|58=update lfc to let stop order pass'
    else:
        logging.error(f"test_case:{test_case.get('testCaseId')},row:{raw},can not get side")
        return ''

def parse_case_messages(test_case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parse all rows' content lines into a flat list of message dicts:
    Each dict: {raw_line, action_full, action, fields: {FieldName: value}, source}
    """
    parsed_msgs: List[Dict[str, Any]] = []
    for index,row in enumerate(test_case.get("rows", [])):
        content = row.get("content", "") or ""
        for raw in content.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            if get_fix_tag_value(raw, '40') == '4':
                # stop order need to add action ahead to update the lfc
                dummy = gen_dummpy_action_to_update_lfc(raw,test_case)
                if dummy:
                    ok = convert_one_line_action(dummy, parsed_msgs, test_case, index)
                    if not ok:
                        return None

            ok = convert_one_line_action(raw, parsed_msgs, test_case, index)
            if not ok:
                return None

    return parsed_msgs

def convert_one_line_action(raw:str, parsed_msgs: List[Dict[str, Any]],test_case: Dict[str, Any],index:int)->bool:
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    if not parts:
        return True
    # skip ImpliedNew and ImpliedTrade line
    if any(p in ['ImpliedNew', 'ImpliedTrade'] for p in parts):
        return True
    # MarketSession|Status=...
    if parts[0] == "MarketSession":
        kv, _ = parse_tag_pairs(parts[1:])
        norm_fields: Dict[str, str] = {}
        if "Status" in kv:
            norm_fields["Status"] = kv["Status"]
        msg = {
            "raw_line": raw,
            "action_full": "MarketSession",
            "action": "MarketSession",
            "fields": norm_fields,
            "source": "content",
        }
        parsed_msgs.append(msg)
        return True

    # Otherwise: expect tag pairs including 35=
    kv, msgcode = parse_tag_pairs(parts)

    # map tags -> internal field names; normalize OrderType -> OrdType
    field_map: Dict[str, str] = {}
    for tag, val in kv.items():
        name = TAG_TO_FIELD.get(tag, tag)
        if name == "ID":
            field_map["#_id"] = f"{index}_{val}"
        elif name == "PreviousID":
            field_map["#_previousID"] = f"{index}_{val}"
        else:
            field_map[name] = val
    if msgcode == '8':  # add ClOrdID for execution report
        if field_map.get('ClOrdID') is None and field_map.get("#_previousID") is not None:
            ref_action = get_action_by_previous_id(parsed_msgs, field_map.get("#_previousID"))
            field_map['ClOrdID'] = f"GetValue({field_map.get('#_previousID')}.{ref_action}.ClOrdID)"
    action_full = resolve_msgtype_full(msgcode or "")
    action_alias = alias_action(action_full)
    if action_alias == '':
        logging.error(f"test_case:{test_case.get('testCaseId')},row:{raw},can not get action")
        return False
    parsed_msgs.append({
        "raw_line": raw,
        "action_full": action_full,
        "action": action_alias,
        "fields": field_map,
        "source": "content",
    })
    return True


def compute_start_send_id_gap(last_send_id: int) -> int:
    """Compute the starting send ID for the next case: (next multiple of 10) + 1."""
    if last_send_id == 0:
        return 1
    base = ((last_send_id // 10) + 1) * 10  # next multiple of 10
    return base + 1


def add_tag(jira_value: Optional[str], test_case_id: str) -> str:
    """
    Prefix each jira key with 'StoryId=' and append 'ZephyrId=test_case_id'.

    Rules:
    - If input contains ',', split by comma and join with ','.
    - Otherwise split by whitespace and join with ','.
    - Finally append ',ZephyrId=xxx'.
    """
    if jira_value is None:
        base = ""
    else:
        s = jira_value.strip()
        if not s:
            base = ""
        elif "," in s:
            parts = [p.strip() for p in s.split(",") if p.strip()]
            base = ",".join(f"StoryId={p}" for p in parts)
        else:
            parts = [p for p in re.split(r"\s+", s) if p]
            base = ",".join(f"StoryId={p}" for p in parts)

    # Append ZephyrId
    if base:
        return f"{base},ZephyrId={test_case_id}"
    else:
        return f"ZephyrId={test_case_id}"



def build_case_rows(test_case: Dict[str, Any],
                    global_last_send_id: int) -> Tuple[List[List[str]], int]:
    """
    Build CSV rows for a single test case and return:
    (rows_out, new_global_last_send_id)
    """
    rows_out: List[List[str]] = []

    test_case_id = str(test_case.get("testCaseId", "")).strip()
    test_case_name = str(test_case.get("testCaseName", "")).strip()
    jira = add_tag(str(test_case.get("jira", "")).strip(), test_case_id)

    # 1) Parse messages for this case (multi-rows support)
    parsed_msgs = parse_case_messages(test_case)
    if parsed_msgs is None:
        return None,None

    # (4) MarketSession de-dup & prepare first MarketSession to be the first action
    ms_indices = [i for i, m in enumerate(parsed_msgs) if m["action_full"] == "MarketSession"]
    first_ms = parsed_msgs[ms_indices[0]] if ms_indices else None
    dedupe_all_ms = False
    if len(ms_indices) > 1:
        first_fields = parsed_msgs[ms_indices[0]]["fields"]
        dedupe_all_ms = all(parsed_msgs[i]["fields"] == first_fields for i in ms_indices)

    # Build body messages according to the de-dup rule
    if first_ms:
        if dedupe_all_ms:
            body_msgs = [m for m in parsed_msgs if m["action_full"] != "MarketSession"]
        else:
            body_msgs = [m for idx, m in enumerate(parsed_msgs) if idx != ms_indices[0]]
    else:
        body_msgs = list(parsed_msgs)

    # 2) Determine dynamics: has New? unique symbols?
    has_new = any(m["action"] == "New" for m in parsed_msgs)
    symbols = collect_symbols(parsed_msgs)

    # 3) Build dynamic field set
    dynamic_fields: Set[str] = set()
    for m in parsed_msgs:
        for k in m["fields"].keys():
            dynamic_fields.add(k)
    # dynamic_fields.add("ContractCode")
    if has_new:
        dynamic_fields.add("OrderType")
        dynamic_fields.add("TIF")
        dynamic_fields.add("NoPartyIDs")
        dynamic_fields.add("AccountType")
        dynamic_fields.add("MassCancelRequestType")
        dynamic_fields.add("#_id")
        dynamic_fields.add("#_previousID")
        dynamic_fields.add("ContractCode")
        dynamic_fields.add("startTime")
        dynamic_fields.add("tomStartTime")
        dynamic_fields.add("ClOrdID")
        dynamic_fields.add("ExecInst")


    # ---- IMPORTANT: ensure no overlap with fixed prefix ----
    FIXED_PREFIX = ["Status", "ID", "PreviousID", "step_type", "Action", "User", "protocol"]
    fixed_prefix_set = set(FIXED_PREFIX)
    # If MarketSession appears (or present in any), Status should be in fixed prefix only
    if "Status" in dynamic_fields:
        dynamic_fields.discard("Status")

    ordered_fields_all = stable_field_order(dynamic_fields)
    # Remove any fields that collide with fixed prefix (safety net)
    ordered_fields = [f for f in ordered_fields_all if f not in fixed_prefix_set]
    # ordered_fields = [f for f in ordered_fields_all]

    # 4) Emit block header
    rows_out.append(["TEST_CASE_START"])
    rows_out.append([test_case_id])
    rows_out.append(["TC Symbol"])
    rows_out.append(["Description, " + test_case_name])
    rows_out.append([jira])

    # 5) Emit table header (UNIQUE by construction)
    header = FIXED_PREFIX + ordered_fields
    rows_out.append(header)

    # 6) ID state (gap mode only)
    send_id = compute_start_send_id_gap(global_last_send_id)
    last_send_id_used = global_last_send_id
    new_id_stack: Dict[str, List[int]] = defaultdict(list)

    def render_values(field_values: Dict[str, Any]) -> List[str]:
        """Render dynamic values aligned to ordered_fields (no 'Status' here)."""
        out: List[str] = []
        for f in ordered_fields:
            if f == "Side" and field_values.get("__action_alias__") in ["New",'Amend']:
                out.append(normalize_order_size_value(field_values.get(f)))
            elif f == "TIF" and field_values.get("__action_alias__") in ["New",'Amend']:
                out.append(normalize_tif_value(field_values.get(f)))
            elif f == "OrderType" and field_values.get("__action_alias__") in ["New",'Amend']:
                out.append(normalize_order_type_value(field_values.get(f)))
            elif f == "NoPartyIDs" and field_values.get("__action_alias__") in ["New"]:
                out.append(field_values.get(f,DEFAULTS["DEFAULT_NOPARTYIDS"]))
            elif f == "NoPartyIDs" and field_values.get("__action_alias__") in ["Cancel",'Amend']:
                out.append(field_values.get(f,DEFAULTS["DEFAULT_AMEND_NOPARTYIDS"]))
            elif f == "AccountType" and field_values.get("__action_alias__") in ["New",'Amend']:
                out.append(field_values.get(f,DEFAULTS["DEFAULT_ACCOUNTTYPE"]))
            elif f == "MassCancelRequestType" and field_values.get("__action_alias__") == "MassCancel":
                out.append(field_values.get(f,7))
            elif f == "ExecInst" and field_values.get("__action_alias__") in ["New",'Amend']:
                out.append(field_values.get(f,'o'))
            elif f in ("ContractCode") and field_values.get("__action_alias__") in ("MarketSession",'MassCancel'):
                out.append(field_values.get(f, DEFAULT_CONTRACT_CODE))
            else:
                out.append(field_values.get(f, ""))
        return out

    def emit_send(action_full: str, action_alias: str, user: str, protocol: str,
                  field_values: Dict[str, Any], previous_id: str = ""):
        nonlocal send_id, last_send_id_used
        # put action alias in the map for defaults
        field_values = dict(field_values)
        field_values["__action_alias__"] = action_alias

        # fixed prefix columns
        fixed_cols = ["", str(send_id), str(previous_id), "send", action_alias, user, protocol]
        # Status in first fixed col (only for MarketSession rows)
        if action_alias == "MarketSession":
            fixed_cols[0] = field_values.get("Status", "")

        values = render_values(field_values)
        row = fixed_cols + values
        rows_out.append(row)

        last_send_id_used = max(last_send_id_used, send_id)
        send_id += 1
        if action_alias == 'MarketSession':
            times = TEMPORARY_SCHEDULE_BY_SESSION[fixed_cols[0]]
            field_map =  {
                'ContractCode':DEFAULT_CONTRACT_CODE,
                'startTime': times[0],
                'tomStartTime': times[1]
            }
            emit_send("MncCreateSchedule", "MncCreateSchedule", "", "MNC", field_map)

    def emit_assertion(action_full: str, action_alias: str, user: str, protocol: str,
                       field_values: Dict[str, Any]):
        # assertions have empty ID fields; Status left empty (unless you want to carry over session)
        field_values = dict(field_values)
        field_values["__action_alias__"] = action_alias

        fixed_cols = ["", "", "", "assertion", action_alias, user, protocol]
        values = render_values(field_values)
        row = fixed_cols + values
        rows_out.append(row)

    # 7) (1) Ensure the first action is MarketSession (if present)
    if first_ms is not None:
        emit_send("MarketSession", "MarketSession", "", "", dict(first_ms["fields"]))

    # 8) (2) Auto MassCancel goes right after the first MarketSession
    if has_new:
        # for sym in sorted(symbols) if symbols else [""]:
        #     fv = {"Symbol": sym} if sym else {}
        protocol, user = DEFAULTS["DEFAULT_PROTOCOL"], DEFAULTS["DEFAULT_FIX_USER"]
        emit_send("OrderMassCancelRequest", "MassCancel", user, protocol, {}, previous_id="")

    # 9) Emit the rest messages (MarketSession removed from body_msgs)
    alien_id_dic = {}
    for m in body_msgs:
        action_full = m["action_full"]
        action_alias = alias_action(action_full)
        stype = step_type_of(action_full)
        protocol, user = detect_protocol_and_user(m["raw_line"], action_full)
        fv = dict(m["fields"])
        prev = ''
        if fv.get("#_previousID",'') != '':
            prev = alien_id_dic.get(fv.get("#_previousID",''),'')
        if stype == "send":
            emit_send(action_full, action_alias, user, protocol, fv, previous_id=prev)
        else:
            emit_assertion(action_full, action_alias, user, protocol, fv)
        if fv.get("#_id",'') != '':
            alien_id_dic[fv.get("#_id")] = send_id -1

    # 10) End marker
    rows_out.append(["TEST_CASE_END"])
    return rows_out, last_send_id_used

def is_zephyr_id_in_csv0(zephyr_id: str, csv0_path: str) -> bool:
    try:
        with open(csv0_path, mode='r', encoding='utf-8') as f:
            for line in f:
                if zephyr_id in line:
                    return True
        return False
    except Exception as e:
        raise RuntimeError(f"Error reading CSV: {e}")
    return False


def convert_json_to_flat_csv(input_path: str, output_csv_path: str, output_csv0_path:str):
    """
    Flatten JSON/JSONL into a simple CSV:
      - Header = union(top-level keys except 'rows', row-level keys) + 'converted'
      - Each row in 'rows' produces one CSV line, combining top-level fields + row fields
      - converted = YES if all rows satisfy thresholds, else NO
    """
    cases = parse_json_or_jsonl(input_path)

    # 1) Collect headers
    top_keys: Set[str] = set()
    row_keys: Set[str] = set()
    for tc in cases:
        top_keys.update(k for k in tc.keys() if k != "rows")
        for r in tc.get("rows", []) or []:
            row_keys.update(r.keys())

    # Stable order: some common fields first, then others alpha-sorted
    top_priority = ["testCaseId", "testCaseName", "testCaseDescription","testDescription","jira","preConditions", "caseIndex"]
    ordered_top = [k for k in top_priority if k in top_keys]
    ordered_top += sorted(k for k in top_keys if k not in set(ordered_top))

    row_priority = ["seq", "steps", "testData", "expectedResults", "content",
                    "confidence", "verify_confidence", "suggest"]
    ordered_row = [k for k in row_priority if k in row_keys]
    ordered_row += sorted(k for k in row_keys if k not in set(ordered_row))

    # 2) Add converted column (avoid collision if input already has 'converted')
    converted_col = "converted" if ("converted" not in top_keys and "converted" not in row_keys) else "converted_generated"
    header = ordered_top + ordered_row + [converted_col]

    # 3) Write CSV
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        header = [h for h in header if h not in DROPPED_FIELDS]

        writer.writerow(header)
        for tc in cases:
            base = {k: v for k, v in tc.items() if k != "rows"}
            conv = is_zephyr_id_in_csv0(f"ZephyrId={tc.get('testCaseId')}",output_csv0_path)

            rows = tc.get("rows", []) or []
            # If rows is empty, you can choose to output one line or skip.
            # Current behavior: output one line with converted=NO.
            if not rows:
                row_dict = dict(base)
                row_dict[converted_col] = conv
                writer.writerow([row_dict.get(h, "") for h in header])
                continue

            for r in rows:
                row_dict = dict(base)
                row_dict.update(r)  # row fields override same-named top-level keys
                row_dict[converted_col] = conv
                writer.writerow([row_dict.get(h, "") for h in header])


def convert_json_to_csv0(input_path: str, output_csv_path: str, skeleton_confidence_threshold:float = 0.8, enrich_confidence_threshold:float = 0.8,assert_confidence_threshold:float = 0.8,verify_confidence_threshold:float = 0.8):
    """
    Convert Step-1 JSON/JSONL to the final CSV with blocks.
    'gap' ID mode is enforced by design.
    """
    data = load_cases_any(input_path, skeleton_confidence_threshold, enrich_confidence_threshold,assert_confidence_threshold, verify_confidence_threshold)  # <--- UPDATED: support JSON & JSONL
    rows_main: List[List[str]] = []
    global_last_send_id = 0

    for case in data:
        case_rows, temp_global_last_send_id = build_case_rows(case, global_last_send_id)
        if case_rows is None:
            continue
        if temp_global_last_send_id is not None:
            global_last_send_id =temp_global_last_send_id
        rows_main.extend(case_rows)
        rows_main.append([])  # blank line between cases

    # Write CSV
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for r in rows_main:
            writer.writerow(r)


def testcase_convert(input_path: str, output_csv0_path: str,output_flat_csv_path: str, skeleton_confidence_threshold:float = 0.8, enrich_confidence_threshold:float = 0.8,assert_confidence_threshold:float = 0.8,verify_confidence_threshold:float = 0.8):
    convert_json_to_csv0(input_path, output_csv0_path, skeleton_confidence_threshold,enrich_confidence_threshold,assert_confidence_threshold, verify_confidence_threshold)
    csv_bdd(output_csv0_path)
    convert_json_to_flat_csv(input_path, output_flat_csv_path, output_csv0_path)

if __name__ == "__main__":
    input_file_path = Path(r"/Users/work/Documents/code/ai/workspaces/result/evan_gw_testcases_sample_100.json")
    input_file_name = input_file_path.name
    base_path = input_file_path.parent
    file_name_without_post_fix = input_file_name.rsplit(".", 1)[0]
    output_csv0_path = fr"{base_path}\{file_name_without_post_fix}_csv0.csv"
    output_flat_csv_path = fr"{base_path}\{file_name_without_post_fix}_flat.csv"
    skeleton_confidence_threshold = 0.7
    enrich_confidence_threshold = 0.7
    assert_confidence_threshold = 0.7
    verify_confidence_threshold = 0
    testcase_convert(input_file_path, output_csv0_path, output_flat_csv_path,skeleton_confidence_threshold,enrich_confidence_threshold,assert_confidence_threshold,verify_confidence_threshold)
