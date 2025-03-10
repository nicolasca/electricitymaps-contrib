#!/usr/bin/env python3
"""
Parser for U.S. Energy Information Administration, https://www.eia.gov/ .

Aggregates and standardizes data from most of the US ISOs,
and exposes them via a unified API.

Requires an API key, set in the EIA_KEY environment variable. Get one here:
https://www.eia.gov/opendata/register.php
"""
import os
from datetime import datetime, timedelta
from logging import Logger, getLogger
from typing import Optional

import arrow
from dateutil import parser, tz
from requests import Session

from parsers.ENTSOE import merge_production_outputs
from parsers.lib.config import refetch_frequency
from parsers.lib.utils import get_token
from parsers.lib.validation import validate

# Reverse exchanges need to be multiplied by -1, since they are reported in the opposite direction
REVERSE_EXCHANGES = [
    "US-CA->MX-BC",
    "MX-BC->US-CAL-CISO",
    "CA-SK->US-CENT-SWPP",
    "CA-MB->US-MIDW-MISO",
    "CA-ON->US-MIDW-MISO",
    "CA-QC->US-NE-ISNE",
    "CA-NB->US-NE-ISNE",
    "CA-BC->US-NW-BPAT",
    "CA-AB->US-NW-NWMT",
    "CA-QC->US-NY-NYIS",
    "CA-ON->US-NY-NYIS",
    "MX-NE->US-TEX-ERCO",
    "MX-NO->US-TEX-ERCO",
    "US-SW-PNM->US-SW-SRP",  # For some reason EBA.SRP-PNM.ID.H exists in EIA, but PNM-SRP does not. Probably because it is unidirectional
]

NEGATIVE_PRODUCTION_THRESHOLDS_TYPE = {
    "default": -10,
    "coal": -50,
    "gas": -20,
    "solar": -100,
    "wind": -20,
    "unknown": -50,
}

# based on https://www.eia.gov/beta/electricity/gridmonitor/dashboard/electric_overview/US48/US48
# or https://www.eia.gov/opendata/qb.php?category=3390101
# List includes regions and Balancing Authorities.
REGIONS = {
    # Non-US regions - EIA
    "MX-BC": "CFE",
    "MX-NE": "CEN",
    "MX-NO": "CFE",
    "CA-SK": "SPC",
    "CA-MB": "MHEB",
    "CA-ON": "IESO",
    "CA-QC": "HQT",
    "CA-NB": "NBSO",
    "CA-BC": "BCHA",
    "CA-AB": "AESO",
    # New regions - EIA
    "US-CAL-BANC": "BANC",  # Balancing Authority Of Northern California
    "US-CAL-CISO": "CISO",  # California Independent System Operator
    "US-CAL-IID": "IID",  # Imperial Irrigation District
    "US-CAL-LDWP": "LDWP",  # Los Angeles Department Of Water And Power
    "US-CAL-TIDC": "TIDC",  # Turlock Irrigation District
    "US-CAR-CPLE": "CPLE",  # Duke Energy Progress East
    "US-CAR-CPLW": "CPLW",  # Duke Energy Progress West
    "US-CAR-DUK": "DUK",  # Duke Energy Carolinas
    "US-CAR-SC": "SC",  # South Carolina Public Service Authority
    "US-CAR-SCEG": "SCEG",  # South Carolina Electric & Gas Company
    "US-CAR-YAD": "YAD",  # Alcoa Power Generating, Inc. - Yadkin Division
    "US-CENT-SPA": "SPA",  # Southwestern Power Administration
    "US-CENT-SWPP": "SWPP",  # Southwest Power Pool
    "US-FLA-FMPP": "FMPP",  # Florida Municipal Power Pool
    "US-FLA-FPC": "FPC",  # Duke Energy Florida Inc
    "US-FLA-FPL": "FPL",  # Florida Power & Light Company
    "US-FLA-GVL": "GVL",  # Gainesville Regional Utilities
    "US-FLA-HST": "HST",  # City Of Homestead
    "US-FLA-JEA": "JEA",  # Jea
    "US-FLA-NSB": "NSB",  # New Smyrna Beach, Utilities Commission Of
    "US-FLA-SEC": "SEC",  # Seminole Electric Cooperative
    "US-FLA-TAL": "TAL",  # City Of Tallahassee
    "US-FLA-TEC": "TEC",  # Tampa Electric Company
    "US-MIDA-PJM": "PJM",  # Pjm Interconnection, Llc
    "US-MIDW-AECI": "AECI",  # Associated Electric Cooperative, Inc.
    "US-MIDW-GLHB": "GLHB",  # GridLiance
    "US-MIDW-LGEE": "LGEE",  # Louisville Gas And Electric Company And Kentucky Utilities
    "US-MIDW-MISO": "MISO",  # Midcontinent Independent Transmission System Operator, Inc..
    "US-NE-ISNE": "ISNE",  # Iso New England Inc.
    "US-NW-AVA": "AVA",  # Avista Corporation
    "US-NW-BPAT": "BPAT",  # Bonneville Power Administration
    "US-NW-CHPD": "CHPD",  # Public Utility District No. 1 Of Chelan County
    "US-NW-DOPD": "DOPD",  # Pud No. 1 Of Douglas County
    "US-NW-GCPD": "GCPD",  # Public Utility District No. 2 Of Grant County, Washington
    "US-NW-GRID": "GRID",  # Gridforce Energy Management, Llc
    "US-NW-GWA": "GWA",  # Naturener Power Watch, Llc (Gwa)
    "US-NW-IPCO": "IPCO",  # Idaho Power Company
    "US-NW-NEVP": "NEVP",  # Nevada Power Company
    "US-NW-NWMT": "NWMT",  # Northwestern Energy (Nwmt)
    "US-NW-PACE": "PACE",  # Pacificorp - East
    "US-NW-PACW": "PACW",  # Pacificorp - West
    "US-NW-PGE": "PGE",  # Portland General Electric Company
    "US-NW-PSCO": "PSCO",  # Public Service Company Of Colorado
    "US-NW-PSEI": "PSEI",  # Puget Sound Energy
    "US-NW-SCL": "SCL",  # Seattle City Light
    "US-NW-TPWR": "TPWR",  # City Of Tacoma, Department Of Public Utilities, Light Division
    "US-NW-WACM": "WACM",  # Western Area Power Administration - Rocky Mountain Region
    "US-NW-WAUW": "WAUW",  # Western Area Power Administration Ugp West
    "US-NW-WWA": "WWA",  # Naturener Wind Watch, Llc
    "US-NY-NYIS": "NYIS",  # New York Independent System Operator
    "US-SE-AEC": "AEC",  # Powersouth Energy Cooperative
    "US-SE-SEPA": "SEPA",  # Southeastern Power Administration
    "US-SE-SOCO": "SOCO",  # Southern Company Services, Inc. - Trans
    "US-SW-AZPS": "AZPS",  # Arizona Public Service Company
    "US-SW-EPE": "EPE",  # El Paso Electric Company
    "US-SW-GRIF": "GRIF",  # Griffith Energy, Llc
    "US-SW-GRMA": "GRMA",  # Gila River Power, Llc
    "US-SW-HGMA": "HGMA",  # New Harquahala Generating Company, Llc - Hgba
    "US-SW-PNM": "PNM",  # Public Service Company Of New Mexico
    "US-SW-SRP": "SRP",  # Salt River Project
    "US-SW-TEPC": "TEPC",  # Tucson Electric Power Company
    "US-SW-WALC": "WALC",  # Western Area Power Administration - Desert Southwest Region
    "US-TEN-TVA": "TVA",  # Tennessee Valley Authority
    "US-TEX-ERCO": "ERCO",  # Electric Reliability Council Of Texas, Inc.
}

EXCHANGES = {
    # Exchanges to non-US BAs
    "MX-BC->US-CAL-CISO": "&facets[fromba][]=CISO&facets[toba][]=CFE",  # Unable to verify if MX-BC is correct
    "CA-SK->US-CENT-SWPP": "&facets[fromba][]=SWPP&facets[toba][]=SPC",
    "CA-MB->US-MIDW-MISO": "&facets[fromba][]=MISO&facets[toba][]=MHEB",
    "CA-ON->US-MIDW-MISO": "&facets[fromba][]=MISO&facets[toba][]=IESO",
    "CA-QC->US-NE-ISNE": "&facets[fromba][]=ISNE&facets[toba][]=HQT",
    "CA-NB->US-NE-ISNE": "&facets[fromba][]=ISNE&facets[toba][]=NBSO",
    "CA-BC->US-NW-BPAT": "&facets[fromba][]=BPAT&facets[toba][]=BCHA",
    "CA-AB->US-NW-NWMT": "&facets[fromba][]=NWMT&facets[toba][]=AESO",
    "CA-QC->US-NY-NYIS": "&facets[fromba][]=NYIS&facets[toba][]=HQT",
    "CA-ON->US-NY-NYIS": "&facets[fromba][]=NYIS&facets[toba][]=IESO",
    "MX-NE->US-TEX-ERCO": "&facets[fromba][]=ERCO&facets[toba][]=CEN",  # Unable to verify if MX-NE is correct
    "MX-NO->US-TEX-ERCO": "&facets[fromba][]=ERCO&facets[toba][]=CFE",  # Unable to verify if MX-NO is correct
    # Exchanges to other US balancing authorities
    "US-CAL-BANC->US-NW-BPAT": "&facets[fromba][]=BANC&facets[toba][]=BPAT",
    "US-CAL-BANC->US-CAL-CISO": "&facets[fromba][]=BANC&facets[toba][]=CISO",
    "US-CAL-BANC->US-CAL-TIDC": "&facets[fromba][]=BANC&facets[toba][]=TIDC",
    "US-CAL-CISO->US-SW-AZPS": "&facets[fromba][]=CISO&facets[toba][]=AZPS",
    "US-CAL-CISO->US-NW-BPAT": "&facets[fromba][]=CISO&facets[toba][]=BPAT",
    "US-CAL-CISO->US-CAL-IID": "&facets[fromba][]=CISO&facets[toba][]=IID",
    "US-CAL-CISO->US-CAL-LDWP": "&facets[fromba][]=CISO&facets[toba][]=LDWP",
    "US-CAL-CISO->US-NW-NEVP": "&facets[fromba][]=CISO&facets[toba][]=NEVP",
    "US-CAL-CISO->US-NW-PACW": "&facets[fromba][]=CISO&facets[toba][]=PACW",
    "US-CAL-CISO->US-SW-SRP": "&facets[fromba][]=CISO&facets[toba][]=SRP",
    "US-CAL-CISO->US-CAL-TIDC": "&facets[fromba][]=CISO&facets[toba][]=TIDC",
    "US-CAL-CISO->US-SW-WALC": "&facets[fromba][]=CISO&facets[toba][]=WALC",
    "US-CAL-IID->US-SW-AZPS": "&facets[fromba][]=IID&facets[toba][]=AZPS",
    "US-CAL-IID->US-SW-WALC": "&facets[fromba][]=IID&facets[toba][]=WALC",
    "US-CAL-LDWP->US-SW-AZPS": "&facets[fromba][]=LDWP&facets[toba][]=AZPS",
    "US-CAL-LDWP->US-NW-BPAT": "&facets[fromba][]=LDWP&facets[toba][]=BPAT",
    "US-CAL-LDWP->US-NW-NEVP": "&facets[fromba][]=LDWP&facets[toba][]=NEVP",
    "US-CAL-LDWP->US-NW-PACE": "&facets[fromba][]=LDWP&facets[toba][]=PACE",
    "US-CAL-LDWP->US-SW-WALC": "&facets[fromba][]=LDWP&facets[toba][]=WALC",
    "US-CAR-CPLE->US-CAR-YAD": "&facets[fromba][]=CPLE&facets[toba][]=YAD",
    "US-CAR-CPLE->US-CAR-DUK": "&facets[fromba][]=CPLE&facets[toba][]=DUK",
    "US-CAR-CPLE->US-MIDA-PJM": "&facets[fromba][]=CPLE&facets[toba][]=PJM",
    "US-CAR-CPLE->US-CAR-SCEG": "&facets[fromba][]=CPLE&facets[toba][]=SCEG",
    "US-CAR-CPLE->US-CAR-SC": "&facets[fromba][]=CPLE&facets[toba][]=SC",
    "US-CAR-CPLW->US-CAR-DUK": "&facets[fromba][]=CPLW&facets[toba][]=DUK",
    "US-CAR-CPLW->US-MIDA-PJM": "&facets[fromba][]=CPLW&facets[toba][]=PJM",
    "US-CAR-CPLW->US-TEN-TVA": "&facets[fromba][]=CPLW&facets[toba][]=TVA",
    "US-CAR-DUK->US-CAR-YAD": "&facets[fromba][]=DUK&facets[toba][]=YAD",
    "US-CAR-DUK->US-MIDA-PJM": "&facets[fromba][]=DUK&facets[toba][]=PJM",
    "US-CAR-DUK->US-CAR-SCEG": "&facets[fromba][]=DUK&facets[toba][]=SCEG",
    "US-CAR-DUK->US-CAR-SC": "&facets[fromba][]=DUK&facets[toba][]=SC",
    "US-CAR-DUK->US-SE-SEPA": "&facets[fromba][]=DUK&facets[toba][]=SEPA",
    "US-CAR-DUK->US-SE-SOCO": "&facets[fromba][]=DUK&facets[toba][]=SOCO",
    "US-CAR-DUK->US-TEN-TVA": "&facets[fromba][]=DUK&facets[toba][]=TVA",
    "US-CAR-SC->US-CAR-SCEG": "&facets[fromba][]=SC&facets[toba][]=SCEG",
    "US-CAR-SC->US-SE-SEPA": "&facets[fromba][]=SC&facets[toba][]=SEPA",
    "US-CAR-SC->US-SE-SOCO": "&facets[fromba][]=SC&facets[toba][]=SOCO",
    "US-CAR-SCEG->US-SE-SEPA": "&facets[fromba][]=SCEG&facets[toba][]=SEPA",
    "US-CAR-SCEG->US-SE-SOCO": "&facets[fromba][]=SCEG&facets[toba][]=SOCO",
    "US-CENT-SPA->US-MIDW-AECI": "&facets[fromba][]=SPA&facets[toba][]=AECI",
    "US-CENT-SPA->US-MIDW-MISO": "&facets[fromba][]=SPA&facets[toba][]=MISO",
    "US-CENT-SPA->US-CENT-SWPP": "&facets[fromba][]=SPA&facets[toba][]=SWPP",
    "US-CENT-SWPP->US-MIDW-AECI": "&facets[fromba][]=SWPP&facets[toba][]=AECI",
    "US-CENT-SWPP->US-SW-EPE": "&facets[fromba][]=SWPP&facets[toba][]=EPE",
    "US-CENT-SWPP->US-TEX-ERCO": "&facets[fromba][]=SWPP&facets[toba][]=ERCO",
    "US-CENT-SWPP->US-MIDW-MISO": "&facets[fromba][]=SWPP&facets[toba][]=MISO",
    "US-CENT-SWPP->US-NW-PSCO": "&facets[fromba][]=SWPP&facets[toba][]=PSCO",
    "US-CENT-SWPP->US-SW-PNM": "&facets[fromba][]=SWPP&facets[toba][]=PNM",
    "US-CENT-SWPP->US-NW-WACM": "&facets[fromba][]=SWPP&facets[toba][]=WACM",
    "US-CENT-SWPP->US-NW-WAUW": "&facets[fromba][]=SWPP&facets[toba][]=WAUW",
    "US-FLA-FMPP->US-FLA-FPC": "&facets[fromba][]=FMPP&facets[toba][]=FPC",
    "US-FLA-FMPP->US-FLA-FPL": "&facets[fromba][]=FMPP&facets[toba][]=FPL",
    "US-FLA-FMPP->US-FLA-JEA": "&facets[fromba][]=FMPP&facets[toba][]=JEA",
    "US-FLA-FMPP->US-FLA-TEC": "&facets[fromba][]=FMPP&facets[toba][]=TEC",
    "US-FLA-FPC->US-FLA-TAL": "&facets[fromba][]=FPC&facets[toba][]=TAL",
    "US-FLA-FPC->US-FLA-FPL": "&facets[fromba][]=FPC&facets[toba][]=FPL",
    "US-FLA-FPC->US-FLA-GVL": "&facets[fromba][]=FPC&facets[toba][]=GVL",
    "US-FLA-FPC->US-FLA-SEC": "&facets[fromba][]=FPC&facets[toba][]=SEC",
    "US-FLA-FPC->US-SE-SOCO": "&facets[fromba][]=FPC&facets[toba][]=SOCO",
    "US-FLA-FPC->US-FLA-TEC": "&facets[fromba][]=FPC&facets[toba][]=TEC",
    "US-FLA-FPC->US-FLA-NSB": "&facets[fromba][]=FPC&facets[toba][]=NSB",
    "US-FLA-FPL->US-FLA-HST": "&facets[fromba][]=FPL&facets[toba][]=HST",
    "US-FLA-FPL->US-FLA-GVL": "&facets[fromba][]=FPL&facets[toba][]=GVL",
    "US-FLA-FPL->US-FLA-JEA": "&facets[fromba][]=FPL&facets[toba][]=JEA",
    "US-FLA-FPL->US-FLA-SEC": "&facets[fromba][]=FPL&facets[toba][]=SEC",
    "US-FLA-FPL->US-SE-SOCO": "&facets[fromba][]=FPL&facets[toba][]=SOCO",
    "US-FLA-FPL->US-FLA-TEC": "&facets[fromba][]=FPL&facets[toba][]=TEC",
    "US-FLA-FPL->US-FLA-NSB": "&facets[fromba][]=FPL&facets[toba][]=NSB",
    "US-FLA-JEA->US-FLA-SEC": "&facets[fromba][]=JEA&facets[toba][]=SEC",
    "US-FLA-SEC->US-FLA-TEC": "&facets[fromba][]=SEC&facets[toba][]=TEC",
    "US-FLA-TAL->US-SE-SOCO": "&facets[fromba][]=TAL&facets[toba][]=SOCO",
    "US-MIDA-PJM->US-MIDW-LGEE": "&facets[fromba][]=PJM&facets[toba][]=LGEE",
    "US-MIDA-PJM->US-MIDW-MISO": "&facets[fromba][]=PJM&facets[toba][]=MISO",
    "US-MIDA-PJM->US-NY-NYIS": "&facets[fromba][]=PJM&facets[toba][]=NYIS",
    "US-MIDA-PJM->US-TEN-TVA": "&facets[fromba][]=PJM&facets[toba][]=TVA",
    "US-MIDW-AECI->US-MIDW-MISO": "&facets[fromba][]=AECI&facets[toba][]=MISO",
    "US-MIDW-AECI->US-TEN-TVA": "&facets[fromba][]=AECI&facets[toba][]=TVA",
    "US-MIDW-GLHB->US-MIDW-LGEE": "&facets[fromba][]=GLHB&facets[toba][]=LGEE",
    "US-MIDW-GLHB->US-MIDW-MISO": "&facets[fromba][]=GLHB&facets[toba][]=MISO",
    "US-MIDW-GLHB->US-TEN-TVA": "&facets[fromba][]=EEI&facets[toba][]=TVA",
    "US-MIDW-LGEE->US-MIDW-MISO": "&facets[fromba][]=LGEE&facets[toba][]=MISO",
    "US-MIDW-LGEE->US-TEN-TVA": "&facets[fromba][]=LGEE&facets[toba][]=TVA",
    "US-MIDW-MISO->US-SE-AEC": "&facets[fromba][]=MISO&facets[toba][]=AEC",
    "US-MIDW-MISO->US-SE-SOCO": "&facets[fromba][]=MISO&facets[toba][]=SOCO",
    "US-MIDW-MISO->US-TEN-TVA": "&facets[fromba][]=MISO&facets[toba][]=TVA",
    "US-NE-ISNE->US-NY-NYIS": "&facets[fromba][]=ISNE&facets[toba][]=NYIS",
    "US-NW-AVA->US-NW-BPAT": "&facets[fromba][]=AVA&facets[toba][]=BPAT",
    "US-NW-AVA->US-NW-IPCO": "&facets[fromba][]=AVA&facets[toba][]=IPCO",
    "US-NW-AVA->US-NW-NWMT": "&facets[fromba][]=AVA&facets[toba][]=NWMT",
    "US-NW-AVA->US-NW-PACW": "&facets[fromba][]=AVA&facets[toba][]=PACW",
    "US-NW-AVA->US-NW-CHPD": "&facets[fromba][]=AVA&facets[toba][]=CHPD",
    "US-NW-AVA->US-NW-GCPD": "&facets[fromba][]=AVA&facets[toba][]=GCPD",
    "US-NW-BPAT->US-NW-TPWR": "&facets[fromba][]=BPAT&facets[toba][]=TPWR",
    "US-NW-BPAT->US-NW-GRID": "&facets[fromba][]=BPAT&facets[toba][]=GRID",
    "US-NW-BPAT->US-NW-IPCO": "&facets[fromba][]=BPAT&facets[toba][]=IPCO",
    "US-NW-BPAT->US-NW-NEVP": "&facets[fromba][]=BPAT&facets[toba][]=NEVP",
    "US-NW-BPAT->US-NW-NWMT": "&facets[fromba][]=BPAT&facets[toba][]=NWMT",
    "US-NW-BPAT->US-NW-DOPD": "&facets[fromba][]=BPAT&facets[toba][]=DOPD",
    "US-NW-BPAT->US-NW-PACW": "&facets[fromba][]=BPAT&facets[toba][]=PACW",
    "US-NW-BPAT->US-NW-PGE": "&facets[fromba][]=BPAT&facets[toba][]=PGE",
    "US-NW-BPAT->US-NW-CHPD": "&facets[fromba][]=BPAT&facets[toba][]=CHPD",
    "US-NW-BPAT->US-NW-GCPD": "&facets[fromba][]=BPAT&facets[toba][]=GCPD",
    "US-NW-BPAT->US-NW-PSEI": "&facets[fromba][]=BPAT&facets[toba][]=PSEI",
    "US-NW-BPAT->US-NW-SCL": "&facets[fromba][]=BPAT&facets[toba][]=SCL",
    "US-NW-CHPD->US-NW-DOPD": "&facets[fromba][]=CHPD&facets[toba][]=DOPD",
    "US-NW-CHPD->US-NW-PSEI": "&facets[fromba][]=CHPD&facets[toba][]=PSEI",
    "US-NW-GCPD->US-NW-PACW": "&facets[fromba][]=GCPD&facets[toba][]=PACW",
    "US-NW-GCPD->US-NW-PSEI": "&facets[fromba][]=GCPD&facets[toba][]=PSEI",
    "US-NW-GWA->US-NW-NWMT": "&facets[fromba][]=GWA&facets[toba][]=NWMT",
    "US-NW-IPCO->US-NW-NEVP": "&facets[fromba][]=IPCO&facets[toba][]=NEVP",
    "US-NW-IPCO->US-NW-NWMT": "&facets[fromba][]=IPCO&facets[toba][]=NWMT",
    "US-NW-IPCO->US-NW-PACE": "&facets[fromba][]=IPCO&facets[toba][]=PACE",
    "US-NW-IPCO->US-NW-PACW": "&facets[fromba][]=IPCO&facets[toba][]=PACW",
    "US-NW-NEVP->US-NW-PACE": "&facets[fromba][]=NEVP&facets[toba][]=PACE",
    "US-NW-NEVP->US-SW-WALC": "&facets[fromba][]=NEVP&facets[toba][]=WALC",
    "US-NW-NWMT->US-NW-WWA": "&facets[fromba][]=NWMT&facets[toba][]=WWA",
    "US-NW-NWMT->US-NW-PACE": "&facets[fromba][]=NWMT&facets[toba][]=PACE",
    "US-NW-NWMT->US-NW-WAUW": "&facets[fromba][]=NWMT&facets[toba][]=WAUW",
    "US-NW-PACE->US-SW-AZPS": "&facets[fromba][]=PACE&facets[toba][]=AZPS",
    "US-NW-PACE->US-NW-PACW": "&facets[fromba][]=PACE&facets[toba][]=PACW",
    "US-NW-PACE->US-NW-WACM": "&facets[fromba][]=PACE&facets[toba][]=WACM",
    "US-NW-PACW->US-NW-PGE": "&facets[fromba][]=PACW&facets[toba][]=PGE",
    "US-NW-PSCO->US-SW-PNM": "&facets[fromba][]=PSCO&facets[toba][]=PNM",
    "US-NW-PSCO->US-NW-WACM": "&facets[fromba][]=PSCO&facets[toba][]=WACM",
    "US-NW-PSEI->US-NW-TPWR": "&facets[fromba][]=PSEI&facets[toba][]=TPWR",
    "US-NW-PSEI->US-NW-SCL": "&facets[fromba][]=PSEI&facets[toba][]=SCL",
    "US-NW-WACM->US-SW-AZPS": "&facets[fromba][]=WACM&facets[toba][]=AZPS",
    "US-NW-WACM->US-SW-PNM": "&facets[fromba][]=WACM&facets[toba][]=PNM",
    "US-NW-WACM->US-SW-WALC": "&facets[fromba][]=WACM&facets[toba][]=WALC",
    "US-NW-WACM->US-NW-WAUW": "&facets[fromba][]=WACM&facets[toba][]=WAUW",
    "US-SE-AEC->US-SE-SOCO": "&facets[fromba][]=AEC&facets[toba][]=SOCO",
    "US-SE-SEPA->US-SE-SOCO": "&facets[fromba][]=SEPA&facets[toba][]=SOCO",
    "US-SE-SOCO->US-TEN-TVA": "&facets[fromba][]=SOCO&facets[toba][]=TVA",
    "US-SW-AZPS->US-SW-GRMA": "&facets[fromba][]=AZPS&facets[toba][]=GRMA",
    "US-SW-AZPS->US-SW-PNM": "&facets[fromba][]=AZPS&facets[toba][]=PNM",
    "US-SW-AZPS->US-SW-SRP": "&facets[fromba][]=AZPS&facets[toba][]=SRP",
    "US-SW-AZPS->US-SW-TEPC": "&facets[fromba][]=AZPS&facets[toba][]=TEPC",
    "US-SW-AZPS->US-SW-WALC": "&facets[fromba][]=AZPS&facets[toba][]=WALC",
    "US-SW-EPE->US-SW-PNM": "&facets[fromba][]=EPE&facets[toba][]=PNM",
    "US-SW-EPE->US-SW-TEPC": "&facets[fromba][]=EPE&facets[toba][]=TEPC",
    "US-SW-GRIF->US-SW-WALC": "&facets[fromba][]=GRIF&facets[toba][]=WALC",
    "US-SW-HGMA->US-SW-SRP": "&facets[fromba][]=HGMA&facets[toba][]=SRP",
    "US-SW-PNM->US-SW-TEPC": "&facets[fromba][]=PNM&facets[toba][]=TEPC",
    "US-SW-PNM->US-SW-SRP": "&facets[fromba][]=SRP&facets[toba][]=PNM",
    "US-SW-SRP->US-SW-TEPC": "&facets[fromba][]=SRP&facets[toba][]=TEPC",
    "US-SW-SRP->US-SW-WALC": "&facets[fromba][]=SRP&facets[toba][]=WALC",
    "US-SW-TEPC->US-SW-WALC": "&facets[fromba][]=TEPC&facets[toba][]=WALC",
}

TYPES = {
    # 'biomass': 'BM',  # not currently supported
    "coal": "COL",
    "gas": "NG",
    "hydro": "WAT",
    "nuclear": "NUC",
    "oil": "OIL",
    "unknown": "OTH",
    "solar": "SUN",
    "wind": "WND",
}

BASE_URL = "https://api.eia.gov/v2/electricity/rto"

PRODUCTION = (
    f"{BASE_URL}/region-data/data/"
    "?data[]=value&facets[respondent][]={}&facets[type][]=NG&frequency=hourly"
)
CONSUMPTION = (
    f"{BASE_URL}/region-data/data/"
    "?data[]=value&facets[respondent][]={}&facets[type][]=D&frequency=hourly"
)
CONSUMPTION_FORECAST = (
    f"{BASE_URL}/region-data/data/"
    "?data[]=value&facets[respondent][]={}&facets[type][]=DF&frequency=hourly"
)
PRODUCTION_MIX = (
    f"{BASE_URL}/fuel-type-data/data/"
    "?data[]=value&facets[respondent][]={}&facets[fueltype][]={}&frequency=hourly"
)
EXCHANGE = f"{BASE_URL}/interchange-data/data/" "?data[]=value{}&frequency=hourly"


@refetch_frequency(timedelta(days=1))
def fetch_production(
    zone_key: str,
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
):
    return _fetch(
        zone_key,
        PRODUCTION.format(REGIONS[zone_key]),
        session=session,
        target_datetime=target_datetime,
        logger=logger,
    )


@refetch_frequency(timedelta(days=1))
def fetch_consumption(
    zone_key: str,
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
):
    consumption = _fetch(
        zone_key,
        CONSUMPTION.format(REGIONS[zone_key]),
        session=session,
        target_datetime=target_datetime,
        logger=logger,
    )
    for point in consumption:
        point["consumption"] = point.pop("value")

    return consumption


@refetch_frequency(timedelta(days=1))
def fetch_consumption_forecast(
    zone_key: str,
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
):
    return _fetch(
        zone_key,
        CONSUMPTION_FORECAST.format(REGIONS[zone_key]),
        session=session,
        target_datetime=target_datetime,
        logger=logger,
    )


@refetch_frequency(timedelta(days=1))
def fetch_production_mix(
    zone_key: str,
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
):
    mixes = []
    for type, code in TYPES.items():
        url_prefix = PRODUCTION_MIX.format(REGIONS[zone_key], code)
        mix = _fetch(
            zone_key,
            url_prefix,
            session=session,
            target_datetime=target_datetime,
            logger=logger,
        )

        # EIA does not currently split production from the Virgil Summer C
        # plant across the two owning/ utilizing BAs:
        # US-CAR-SCEG and US-CAR-SC,
        # but attributes it all to US-CAR-SCEG
        # Here we apply a temporary fix for that until EIA properly splits the production
        # This split can be found in the eGRID data,
        # https://www.epa.gov/energy/emissions-generation-resource-integrated-database-egrid
        SC_VIRGIL_OWNERSHIP = 0.3333333
        if zone_key == "US-CAR-SC" and type == "nuclear":
            url_prefix = PRODUCTION_MIX.format(REGIONS["US-CAR-SCEG"], code)
            mix = _fetch(
                "US-CAR-SCEG",
                url_prefix,
                session=session,
                target_datetime=target_datetime,
                logger=logger,
            )
            for point in mix:
                point.update({"value": point["value"] * SC_VIRGIL_OWNERSHIP})

        if zone_key == "US-CAR-SCEG" and type == "nuclear":
            for point in mix:
                point.update({"value": point["value"] * (1 - SC_VIRGIL_OWNERSHIP)})

        if not mix:
            continue
        for point in mix:
            negative_threshold = NEGATIVE_PRODUCTION_THRESHOLDS_TYPE.get(
                type, NEGATIVE_PRODUCTION_THRESHOLDS_TYPE["default"]
            )

            if (
                type != "hydro"
                and point["value"]
                and 0 > point["value"] >= negative_threshold
            ):
                point["value"] = 0

            if type == "hydro" and point["value"] and point["value"] < 0:
                point.update(
                    {
                        "production": {},  # required by merge_production_outputs()
                        "storage": {type: point.pop("value")},
                    }
                )
            else:
                point.update(
                    {
                        "production": {type: point.pop("value")},
                        "storage": {},  # required by merge_production_outputs()
                    }
                )

            # replace small negative values (>-5) with 0s This is necessary for solar
            point = validate(point, logger=logger, remove_negative=True)
        mixes.append(mix)

    if not mixes:
        logger.warning(f"No production mix data found for {zone_key}")
        return []

    # Some of the returned mixes could be for older timeframes.
    # Fx the latest oil data could be 6 months old.
    # In this case we want to discard the old data as we won't be able to merge it
    timeframes = [sorted(map(lambda x: x["datetime"], mix)) for mix in mixes]
    latest_timeframe = max(timeframes, key=lambda x: x[-1])

    correct_mixes = []
    for mix in mixes:
        correct_mix = []
        for production_in_mix in mix:
            if production_in_mix["datetime"] in latest_timeframe:
                correct_mix.append(production_in_mix)
        if len(correct_mix) > 0:
            correct_mixes.append(correct_mix)

    return merge_production_outputs(correct_mixes, zone_key, merge_source="eia.gov")


@refetch_frequency(timedelta(days=1))
def fetch_exchange(
    zone_key1: str,
    zone_key2: str,
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
):
    sortedcodes = "->".join(sorted([zone_key1, zone_key2]))
    exchange = _fetch(
        sortedcodes,
        url_prefix=EXCHANGE.format(EXCHANGES[sortedcodes]),
        session=session,
        target_datetime=target_datetime,
        logger=logger,
    )
    for point in exchange:
        point.update(
            {
                "sortedZoneKeys": point.pop("zoneKey"),
                "netFlow": point.pop("value"),
            }
        )
        if sortedcodes in REVERSE_EXCHANGES:
            point["netFlow"] = -point["netFlow"]

    return exchange


def _fetch(
    zone_key: str,
    url_prefix: str,
    session: Optional[Session] = None,
    target_datetime: Optional[datetime] = None,
    logger: Logger = getLogger(__name__),
):
    # get EIA API key
    API_KEY = get_token("EIA_KEY")

    if target_datetime:
        try:
            target_datetime = arrow.get(target_datetime).datetime
        except arrow.parser.ParserError:
            raise ValueError(
                f"target_datetime must be a valid datetime - received {target_datetime}"
            )
        utc = tz.gettz("UTC")
        eia_ts_format = "%Y-%m-%dT%H"
        end = target_datetime.astimezone(utc) + timedelta(hours=1)
        start = end - timedelta(days=1)
        url = f"{url_prefix}&api_key={API_KEY}&start={start.strftime(eia_ts_format)}&end={end.strftime(eia_ts_format)}"
    else:
        url = f"{url_prefix}&api_key={API_KEY}&sort[0][column]=period&sort[0][direction]=desc&length=24"

    s = session or Session()
    req = s.get(url)
    raw_data = req.json()
    if raw_data.get("response", {}).get("data", None) is None:
        return []
    return [
        {
            "zoneKey": zone_key,
            "datetime": _get_utc_datetime_from_datapoint(
                parser.parse(datapoint["period"])
            ),
            "value": datapoint["value"],
            "source": "eia.gov",
        }
        for datapoint in raw_data["response"]["data"]
    ]


def _conform_timestamp_convention(dt: datetime):
    # The timestamp given by EIA represents the end of the time interval.
    # ElectricityMap using another convention,
    # where the timestamp represents the beginning of the interval.
    # So we need shift the datetime 1 hour back.
    return dt - timedelta(hours=1)


def _get_utc_datetime_from_datapoint(dt: datetime):
    """update to beginning hour convention and timezone to utc"""
    dt_beginning_hour = _conform_timestamp_convention(dt)
    dt_utc = arrow.get(dt_beginning_hour).to("utc")
    return dt_utc.datetime


if __name__ == "__main__":
    from pprint import pprint

    # pprint(fetch_production('US-CENT-SWPP'))
    # # pprint(fetch_consumption_forecast('US-CAL-CISO'))
    pprint(
        fetch_exchange(
            zone_key1="US-CENT-SWPP",
            zone_key2="CA-SK",
            target_datetime=datetime(2022, 3, 1),
        )
    )
