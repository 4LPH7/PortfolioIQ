---
phase: 06-product-ux-polish
plan: 03
status: complete
commits: 1
completed_at: 2026-09-30T15:15:00+05:30
---

# Plan 06-03: 3-Step Guided Onboarding Wizard & CSV Portfolio Holdings Importer Summary

## Overview

Plan 06-03 implemented an intuitive, progressive onboarding wizard and a flexible CSV portfolio importer supporting Zerodha Holdings, Zerodha Tradebook, and generic broker exports.

## Key Accomplishments

1. **DTO Contracts (`src/models/dtos.py`):**
   - Added `CSVImportHoldingDTO`: Typed representation of imported holdings with tradingsymbol, quantity, average price, exchange, resolved instrument token, and invested value.
   - Added `CSVImportResultDTO`: Comprehensive envelope detailing imported count, skipped count, imported holdings list, row error diagnostics, and status message.

2. **Backend CSV Import Gateway (`src/api/v1/blueprint.py`):**
   - Implemented `POST /api/v1/holdings/import-csv` supporting `multipart/form-data` file uploads, JSON payloads with raw text, and raw CSV streams.
   - Robust column matching automatically recognizing Zerodha Holdings (`Instrument`, `Qty.`, `Avg. cost`), Tradebook (`symbol`, `trade_type`, `quantity`, `price`), and generic broker formats.
   - For Tradebook files, accurately aggregates multi-tranche purchases into net quantities and weighted average prices.
   - Resolves or generates deterministic pseudo-tokens in `instrument_master` and safely upserts records into `user_holdings` using PostgreSQL `ON CONFLICT (user_id, instrument_token, product)` handling.

3. **Progressive Onboarding Wizard (`frontend/onboarding.html`):**
   - Step 1: Environment & Execution Mode — Interactive selection between Paper Trading / Simulation (default recommendation) and Zerodha Kite Connect live execution with connection test telemetry.
   - Step 2: Portfolio Holdings Ingestion — Option A for direct Zerodha Kite SOD sync; Option B for interactive drag-and-drop CSV file upload with real-time parsed table preview.
   - Step 3: Target Allocations & Safety Guard — Sliders and inputs for target equity exposure, minimum cash buffer (₹), and allocation drift sensitivity threshold (%), persisting configuration to `app_config`.

4. **Testing & Verification (`tests/test_onboarding_import.py`):**
   - Built 9 unit and integration tests covering:
     - Zerodha holdings CSV parsing
     - Tradebook buy aggregation
     - Generic broker column parsing
     - Missing required columns rejection (HTTP 400)
     - Empty payload handling (HTTP 400)
     - Authentication gates (HTTP 401)
     - Complete DOM wizard structure, controls, and script inclusions
   - All 41 Phase 6 tests pass in 1.28s.
   - 0 Ruff lint errors.
